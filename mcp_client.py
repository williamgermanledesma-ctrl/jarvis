"""
mcp_client.py
-------------
A resilient Model Context Protocol (MCP) client. MCP is an open standard that
lets an agent connect to external "tool servers" instead of hand-writing every
tool wrapper. This client speaks the core protocol over stdio (JSON-RPC 2.0):
it launches a server process, performs the initialize handshake, lists the
server's tools, and calls them.

Resilience: each server gets a daemon thread that drains stdout into a queue,
so a slow or chatty server never blocks the Flask backend. A second thread logs
the server's stderr. Requests wait on the queue with a timeout, and async
notifications are handled without stalling pending calls.

CONFIG: mcp_servers.json supports BOTH formats —
  - Ecosystem standard (same as Claude Desktop):
      {"mcpServers": {"sqlite": {"command": "npx", "args": ["-y", "..."]}}}
  - Jarvis legacy list:
      {"servers": [{"name": "sqlite", "command": ["npx", "-y", "..."]}]}

SCOPE (honest): implements initialize / tools/list / tools/call over stdio.
Not the full spec (no resources, prompts, sampling, or SSE transport).
"""

import os
import json
import time
import queue
import logging
import threading
import subprocess
import itertools

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("JarvisMCP")

_CONFIG = os.path.join(os.path.dirname(__file__), "mcp_servers.json")


class MCPServer:
    """One MCP server subprocess (JSON-RPC 2.0 over stdio), with a reader thread."""

    def __init__(self, name, command, args=None):
        if isinstance(command, list):
            self.argv = command + (args or [])
        else:
            self.argv = [command] + (args or [])
        self.name = name
        self.proc = None
        self.tools = []
        self._q = queue.Queue()
        self._ids = itertools.count(1)
        self._send_lock = threading.Lock()
        self.error = None

    def start(self):
        try:
            self.proc = subprocess.Popen(
                self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1)
        except FileNotFoundError:
            self.error = f"Command not found: {self.argv[0]} (installed / on PATH?)"
            logger.error(self.error)
            return []
        except Exception as e:
            self.error = str(e)
            logger.error(f"Failed to spawn '{self.name}': {e}")
            return []

        threading.Thread(target=self._stdout_loop, daemon=True).start()
        threading.Thread(target=self._stderr_loop, daemon=True).start()

        try:
            self._request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {"roots": {"listChanged": False}},
                "clientInfo": {"name": "Jarvis", "version": "1.1"},
            }, timeout=10)
            self._notify("notifications/initialized", {})
            res = self._request("tools/list", {}, timeout=8)
            self.tools = (res or {}).get("tools", [])
            logger.info(f"MCP '{self.name}' connected - {len(self.tools)} tool(s).")
        except Exception as e:
            self.error = f"Handshake failed: {e}"
            logger.error(self.error)
            self.stop()
            return []
        return self.tools

    def _stdout_loop(self):
        try:
            for line in iter(self.proc.stdout.readline, ""):
                line = line.strip()
                if line:
                    self._q.put(line)
        except Exception as e:
            logger.debug(f"stdout reader '{self.name}' ended: {e}")

    def _stderr_loop(self):
        try:
            for line in iter(self.proc.stderr.readline, ""):
                line = line.strip()
                if line:
                    logger.warning(f"[MCP {self.name} stderr] {line}")
        except Exception:
            pass

    def _send(self, obj):
        with self._send_lock:
            self.proc.stdin.write(json.dumps(obj) + "\n")
            self.proc.stdin.flush()

    def _notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method, params, timeout=15):
        if not self.proc or self.proc.poll() is not None:
            raise RuntimeError(f"server '{self.name}' is not running")
        rid = next(self._ids)
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        start = time.time()
        while time.time() - start < timeout:
            try:
                line = self._q.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == rid:
                if "error" in msg:
                    raise RuntimeError(f"server error: {msg['error']}")
                return msg.get("result", {})
            elif "method" in msg:
                m = msg.get("method", "")
                if m in ("notifications/message", "notifications/log"):
                    logger.info(f"[MCP {self.name}] {msg.get('params', {}).get('message','')}")
        raise TimeoutError(f"'{self.name}' timed out after {timeout}s on {method}")

    def call_tool(self, tool_name, arguments):
        res = self._request("tools/call",
                            {"name": tool_name, "arguments": arguments or {}}, timeout=30)
        parts = [b.get("text", "") for b in res.get("content", [])
                 if b.get("type") == "text"]
        return "\n".join(parts) or "(no text content returned)"

    def stop(self):
        if not self.proc:
            return
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.proc = None


_servers = {}


def _normalize_config(data):
    """Return [{name, command, args}] from either supported format."""
    out = []
    if isinstance(data.get("mcpServers"), dict):
        for name, opts in data["mcpServers"].items():
            out.append({"name": name, "command": opts.get("command"),
                        "args": opts.get("args", [])})
    if isinstance(data.get("servers"), list):
        for s in data["servers"]:
            out.append({"name": s.get("name"), "command": s.get("command"),
                        "args": s.get("args", [])})
    return [c for c in out if c.get("name") and c.get("command")]


def load_config():
    if not os.path.exists(_CONFIG):
        return []
    try:
        with open(_CONFIG, "r", encoding="utf-8") as f:
            return _normalize_config(json.load(f))
    except Exception as e:
        logger.error(f"Could not read {_CONFIG}: {e}")
        return []


def connect_all():
    summary = []
    for cfg in load_config():
        try:
            srv = MCPServer(cfg["name"], cfg["command"], cfg.get("args"))
            tools = srv.start()
            if srv.error and not tools:
                hint = _failure_hint(srv.error)
                summary.append({"name": cfg["name"], "error": srv.error, "hint": hint})
                logger.error(f"MCP '{cfg['name']}' failed: {srv.error}"
                             + (f"  -> {hint}" if hint else ""))
                continue
            _servers[cfg["name"]] = srv
            summary.append({"name": cfg["name"], "tools": [t.get("name") for t in tools]})
        except Exception as e:
            summary.append({"name": cfg["name"], "error": str(e)})
    return summary


def _failure_hint(error):
    """Turn a raw failure into a one-line, actionable hint."""
    e = (error or "").lower()
    if "not found" in e and "command" in e:
        return "That command isn't installed. Install Node (brew install node) or uv, then retry."
    if "timed out" in e:
        return ("The server didn't complete the handshake — usually the npm package "
                "name is wrong/unpublished, or it needs a valid path/credentials. "
                "Check the package exists on npmjs.com and any path argument is real.")
    if "enoent" in e or "no such file" in e:
        return "A path argument points to a folder that doesn't exist. Create it or fix the path."
    return ""


def _is_destructive(tool_name):
    low = tool_name.lower()
    return any(k in low for k in ("write", "delete", "remove", "execute",
                                  "create", "update", "drop", "insert"))


def list_tools():
    schemas = []
    for sname, srv in _servers.items():
        for t in srv.tools:
            schemas.append({
                "type": "function",
                "function": {
                    "name": f"mcp__{sname}__{t['name']}",
                    "description": f"[MCP:{sname}] " + t.get("description", ""),
                    "parameters": t.get("inputSchema", {"type": "object", "properties": {}}),
                },
            })
    return schemas


def destructive_names():
    names = set()
    for sname, srv in _servers.items():
        for t in srv.tools:
            if _is_destructive(t["name"]):
                names.add(f"mcp__{sname}__{t['name']}")
    return names


def call(namespaced_name, arguments):
    try:
        _, sname, tname = namespaced_name.split("__", 2)
    except ValueError:
        return f"Bad MCP tool name: {namespaced_name}"
    srv = _servers.get(sname)
    if not srv:
        return f"MCP server '{sname}' not connected."
    try:
        return srv.call_tool(tname, arguments)
    except Exception as e:
        return f"MCP error: {e}"


def status():
    return [{"name": n, "tools": [t.get("name") for t in s.tools]}
            for n, s in _servers.items()]


def shutdown():
    for s in _servers.values():
        s.stop()
    _servers.clear()
