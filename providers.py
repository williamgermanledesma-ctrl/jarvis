"""
providers.py
------------
Lets Jarvis route a turn to different model backends:

  - "local"  : Ollama on your machine (private, free, default)
  - "claude" : Anthropic Claude API (cloud, needs ANTHROPIC_API_KEY)
  - "gemini" : Google Gemini API (cloud, needs GEMINI_API_KEY)

PRIVACY NOTE: 'claude' and 'gemini' send that turn's messages to a cloud
service. Local stays on your machine. The server marks cloud turns clearly in
the UI so you always know what left your computer.

API keys are read from environment variables — never hard-code them:
    export ANTHROPIC_API_KEY=sk-ant-...
    export GEMINI_API_KEY=...

These calls are plain HTTPS (via the 'requests' library) so there are no extra
SDK installs. Tool-calling through cloud providers is intentionally NOT wired
here — cloud turns answer in text. Use local for the autonomous tool loop.
"""

import os
import json
import requests

CLAUDE_MODEL = "claude-sonnet-4-6"
GEMINI_MODEL = "gemini-2.0-flash"   # fast, generous free tier

# OpenAI-compatible providers: same request/response shape, different base URL,
# key env var, and a sensible default model. Adding one entry here is enough to
# support a whole provider (its tool/chat format matches OpenAI's).
OPENAI_COMPATIBLE = {
    "openai": {
        "base": "https://api.openai.com/v1",
        "key_env": "OPENAI_API_KEY",
        "default_model": "gpt-4o-mini",   # cheap, capable default
    },
    "openrouter": {
        "base": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
        "default_model": "openai/gpt-4o-mini",  # OpenRouter routes many models
    },
    "groq": {
        "base": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",
        "default_model": "llama-3.3-70b-versatile",  # fast, free-tier open model
    },
}

# Runtime key overrides (bring-your-own-key). Built to include every provider.
_key_override = {"ANTHROPIC_API_KEY": None, "GEMINI_API_KEY": None}
for _p in OPENAI_COMPATIBLE.values():
    _key_override[_p["key_env"]] = None


def set_key(which, value):
    """Set a runtime API key (which = 'ANTHROPIC_API_KEY' or 'GEMINI_API_KEY')."""
    if which in _key_override:
        _key_override[which] = value or None
        return True
    return False


def _key(name):
    """Get a key: runtime override first, then environment."""
    return _key_override.get(name) or os.environ.get(name)


def load_dotenv():
    """
    Load key=value pairs from a .env file next to the code into os.environ.
    Tiny and dependency-free. Shell-exported vars take precedence (we don't
    overwrite anything already set). Lines starting with # are comments.
    """
    path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")  # tolerate quotes
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception:
        pass


# Load .env as soon as this module is imported, before any key is read.
load_dotenv()


def available():
    """Which providers are usable right now (based on keys present)."""
    avail = {
        "local": True,
        "claude": bool(_key("ANTHROPIC_API_KEY")),
        "gemini": bool(_key("GEMINI_API_KEY")),
    }
    for name, cfg in OPENAI_COMPATIBLE.items():
        avail[name] = bool(_key(cfg["key_env"]))
    return avail


def _to_text_messages(history):
    """Flatten our history into simple {role, content} text turns."""
    out = []
    system = ""
    for m in history:
        role = m.get("role")
        content = m.get("content") or ""
        if role == "system":
            system += ("\n" + content if system else content)
        elif role in ("user", "assistant") and content:
            out.append({"role": role, "content": content})
        elif role == "tool":
            # Fold tool results in as context for cloud models.
            out.append({"role": "user", "content": f"[tool result] {content}"})
    return system, out


def claude_chat(history, stream=False):
    """Call Claude. Yields text chunks if stream=True, else returns full text."""
    key = _key("ANTHROPIC_API_KEY")
    if not key:
        return _err("Claude not configured. Set ANTHROPIC_API_KEY.", stream)
    system, msgs = _to_text_messages(history)
    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": 2048,
        "system": system or "You are Jarvis, a helpful assistant.",
        "messages": msgs,
        "stream": stream,
    }
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    url = "https://api.anthropic.com/v1/messages"
    if not stream:
        r = requests.post(url, headers=headers, json=body, timeout=120)
        if r.status_code != 200:
            return f"Claude error {r.status_code}: {r.text[:200]}"
        data = r.json()
        return "".join(b.get("text", "") for b in data.get("content", []))
    return _claude_stream(url, headers, body)


def _claude_stream(url, headers, body):
    with requests.post(url, headers=headers, json=body, stream=True, timeout=120) as r:
        for line in r.iter_lines():
            if not line or not line.startswith(b"data: "):
                continue
            try:
                evt = json.loads(line[6:])
            except Exception:
                continue
            if evt.get("type") == "content_block_delta":
                yield evt.get("delta", {}).get("text", "")


def gemini_chat(history, stream=False):
    """Call Gemini. Yields text chunks if stream=True, else returns full text."""
    key = _key("GEMINI_API_KEY")
    if not key:
        return _err("Gemini not configured. Set GEMINI_API_KEY.", stream)
    system, msgs = _to_text_messages(history)
    contents = []
    for m in msgs:
        role = "user" if m["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})
    body = {"contents": contents}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    base = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}"
    if not stream:
        url = f"{base}:generateContent?key={key}"
        r = requests.post(url, json=body, timeout=120)
        if r.status_code != 200:
            return f"Gemini error {r.status_code}: {r.text[:200]}"
        data = r.json()
        try:
            return "".join(p.get("text", "")
                           for p in data["candidates"][0]["content"]["parts"])
        except Exception:
            return "Gemini returned no usable text."
    return _gemini_stream(base, key, body)


def _gemini_stream(base, key, body):
    url = f"{base}:streamGenerateContent?alt=sse&key={key}"
    with requests.post(url, json=body, stream=True, timeout=120) as r:
        for line in r.iter_lines():
            if not line or not line.startswith(b"data: "):
                continue
            try:
                evt = json.loads(line[6:])
                parts = evt["candidates"][0]["content"]["parts"]
                for p in parts:
                    if p.get("text"):
                        yield p["text"]
            except Exception:
                continue


def _err(msg, stream):
    if stream:
        def g():
            yield msg
        return g()
    return msg


# ----------------- Claude tool-calling -----------------
# Claude uses a different format than Ollama: tools are described with
# input_schema, and the conversation carries tool_use / tool_result content
# blocks. These helpers translate so the server's tool loop can drive Claude.

def ollama_tools_to_claude(schemas):
    """Convert our Ollama-style tool schemas to Anthropic's tool format."""
    out = []
    for s in schemas:
        fn = s["function"]
        out.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return out


def _history_to_claude_messages(history):
    """
    Convert our internal history (which may contain Ollama-style tool messages)
    into Claude's message list. We keep the system prompt separate.
    Returns (system_text, claude_messages).
    """
    system = ""
    msgs = []
    for m in history:
        role = m.get("role")
        if role == "system":
            system += ("\n" + (m.get("content") or "") if system else (m.get("content") or ""))
        elif role == "user":
            msgs.append({"role": "user", "content": m.get("content") or ""})
        elif role == "assistant":
            # Plain assistant text (we store Claude tool_use turns separately).
            if m.get("content"):
                msgs.append({"role": "assistant", "content": m["content"]})
        elif role == "tool":
            # Represent a tool result as a user turn carrying tool_result, but
            # only if the previous assistant turn was a tool_use (Claude rule).
            # For simplicity we attach it as plain context text.
            msgs.append({"role": "user", "content": f"[tool result for {m.get('name','tool')}] {m.get('content','')}"})
    return system, msgs


def claude_tool_turn(history, schemas):
    """
    One Claude turn WITH tools. Returns a dict:
      {"type": "tool_use", "name": ..., "args": ..., "id": ..., "text": ...}
      or {"type": "text", "text": ...}
    The server loop runs the tool (with approval gating) and calls again.
    """
    key = _key("ANTHROPIC_API_KEY")
    if not key:
        return {"type": "text", "text": "Claude not configured. Set ANTHROPIC_API_KEY."}
    system, msgs = _history_to_claude_messages(history)
    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": 2048,
        "system": system or "You are Jarvis, a helpful assistant.",
        "messages": msgs,
        "tools": ollama_tools_to_claude(schemas),
    }
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    r = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=body, timeout=120)
    if r.status_code != 200:
        return {"type": "text", "text": f"Claude error {r.status_code}: {r.text[:200]}"}
    data = r.json()
    text_parts, tool_use = [], None
    for block in data.get("content", []):
        if block.get("type") == "text":
            text_parts.append(block["text"])
        elif block.get("type") == "tool_use":
            tool_use = block
    if tool_use:
        return {"type": "tool_use", "name": tool_use["name"],
                "args": tool_use.get("input", {}), "id": tool_use.get("id"),
                "text": "".join(text_parts)}
    return {"type": "text", "text": "".join(text_parts)}


# ----------------- Gemini tool-calling -----------------
# Gemini uses functionDeclarations in tools, returns functionCall parts, and
# you reply with functionResponse parts. We translate so the server's loop can
# drive Gemini the same way it drives Claude.

def ollama_tools_to_gemini(schemas):
    """Convert our Ollama-style tool schemas to Gemini's functionDeclarations."""
    decls = []
    for s in schemas:
        fn = s["function"]
        params = fn.get("parameters", {"type": "object", "properties": {}})
        decls.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "parameters": params,
        })
    return [{"functionDeclarations": decls}]


def _history_to_gemini_contents(history):
    """
    Build Gemini 'contents' from our history, including functionResponse parts
    for tool results so Gemini can chain. Returns (system_text, contents).
    """
    system = ""
    contents = []
    for m in history:
        role = m.get("role")
        if role == "system":
            system += ("\n" + (m.get("content") or "") if system else (m.get("content") or ""))
        elif role == "user":
            contents.append({"role": "user", "parts": [{"text": m.get("content") or ""}]})
        elif role == "assistant":
            if m.get("content"):
                contents.append({"role": "model", "parts": [{"text": m["content"]}]})
        elif role == "tool":
            # Gemini expects a functionResponse part referencing the tool name.
            contents.append({"role": "user", "parts": [{
                "functionResponse": {
                    "name": m.get("name", "tool"),
                    "response": {"result": m.get("content", "")},
                }
            }]})
    return system, contents


def gemini_tool_turn(history, schemas):
    """
    One Gemini turn WITH tools. Returns:
      {"type": "tool_use", "name": ..., "args": ..., "text": ...}
      or {"type": "text", "text": ...}
    """
    key = _key("GEMINI_API_KEY")
    if not key:
        return {"type": "text", "text": "Gemini not configured. Set GEMINI_API_KEY."}
    system, contents = _history_to_gemini_contents(history)
    body = {"contents": contents, "tools": ollama_tools_to_gemini(schemas)}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={key}")
    try:
        r = requests.post(url, json=body, timeout=120)
    except Exception as e:
        return {"type": "text", "text": f"Gemini request failed: {e}"}
    if r.status_code != 200:
        return {"type": "text", "text": f"Gemini error {r.status_code}: {r.text[:200]}"}
    data = r.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except Exception:
        return {"type": "text", "text": "Gemini returned no usable content."}
    text_parts, fcall = [], None
    for p in parts:
        if "text" in p:
            text_parts.append(p["text"])
        elif "functionCall" in p:
            fcall = p["functionCall"]
    if fcall:
        return {"type": "tool_use", "name": fcall.get("name"),
                "args": fcall.get("args", {}) or {}, "text": "".join(text_parts)}
    return {"type": "text", "text": "".join(text_parts)}


# ----------------- OpenAI-compatible providers (OpenAI / OpenRouter / Groq) -----------------
# These three share the OpenAI chat-completions format, so one implementation
# serves all of them — only the base URL, key, and default model differ.

def _openai_messages(history):
    """Convert our history into OpenAI chat format (system/user/assistant/tool)."""
    msgs = []
    for m in history:
        role = m.get("role")
        content = m.get("content") or ""
        if role == "system":
            msgs.append({"role": "system", "content": content})
        elif role == "user":
            msgs.append({"role": "user", "content": content})
        elif role == "assistant" and content:
            msgs.append({"role": "assistant", "content": content})
        elif role == "tool":
            # Fold tool results in as context (simple, robust across providers).
            msgs.append({"role": "user", "content": f"[tool result] {content}"})
    return msgs


def openai_compatible_chat(provider, history, stream=False, model=None):
    """Chat with an OpenAI-compatible provider. Yields chunks if stream=True."""
    cfg = OPENAI_COMPATIBLE.get(provider)
    if not cfg:
        return _err(f"Unknown provider '{provider}'.", stream)
    key = _key(cfg["key_env"])
    if not key:
        return _err(f"{provider} not configured. Set {cfg['key_env']}.", stream)
    body = {
        "model": model or cfg["default_model"],
        "messages": _openai_messages(history),
        "stream": stream,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    # OpenRouter likes these optional attribution headers.
    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/jarvis"
        headers["X-Title"] = "Jarvis"
    url = f"{cfg['base']}/chat/completions"
    if not stream:
        try:
            r = requests.post(url, headers=headers, json=body, timeout=120)
        except Exception as e:
            return f"{provider} request failed: {e}"
        if r.status_code != 200:
            return f"{provider} error {r.status_code}: {r.text[:200]}"
        try:
            return r.json()["choices"][0]["message"]["content"]
        except Exception:
            return f"{provider} returned no usable text."
    return _openai_stream(url, headers, body, provider)


def _openai_stream(url, headers, body, provider):
    try:
        with requests.post(url, headers=headers, json=body, stream=True, timeout=120) as r:
            if r.status_code != 200:
                yield f"[{provider} error {r.status_code}: {r.text[:160]}]"
                return
            for line in r.iter_lines():
                if not line or not line.startswith(b"data: "):
                    continue
                chunk = line[6:]
                if chunk.strip() == b"[DONE]":
                    break
                try:
                    delta = json.loads(chunk)["choices"][0].get("delta", {})
                    if delta.get("content"):
                        yield delta["content"]
                except Exception:
                    continue
    except Exception as e:
        yield f"[{provider} stream failed: {e}]"
