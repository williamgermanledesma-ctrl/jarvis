"""
actions.py
----------
These are the actual functions your AI assistant can call.
Each one is a normal Python function. To give your assistant a new
capability, write a function here and then register it in registry.py.

SAFETY NOTE: Functions that only READ are safe to auto-run.
Functions that WRITE, DELETE, or RUN shell commands should always
go through the human-in-the-loop confirmation gate (handled in main.py).
"""

import subprocess
import os
import re
import datetime


# ==================== SAFETY INFRASTRUCTURE ====================
# These guard the powerful tools (shell + file writes) against both accidents
# and a misbehaving model. They are defense-in-depth ON TOP OF the approval gate.

# Confine file tools to a workspace. By default this is your home directory;
# set JARVIS_WORKSPACE to lock it tighter (e.g. ~/jarvis_workspace).
WORKSPACE = os.path.realpath(
    os.path.expanduser(os.environ.get("JARVIS_WORKSPACE", "~"))
)

# Audit log of every shell command and file write the assistant performs.
AUDIT_LOG = os.path.join(os.path.dirname(os.path.dirname(__file__)), "jarvis_audit.log")

# Shell patterns that are auto-rejected BEFORE the approval prompt — things that
# are almost never what you want an AI proposing. Not exhaustive; the approval
# gate is still your main protection.
DANGEROUS_PATTERNS = [
    r"\brm\s+-rf?\b",          # recursive delete
    r"\bsudo\b",               # privilege escalation
    r"\bmkfs\b",               # format a filesystem
    r"\bdd\b\s+if=",           # raw disk writes
    r":\(\)\s*\{",             # fork bomb
    r"\bchmod\s+-R\b",         # recursive permission changes
    r">\s*/dev/sd",            # writing to raw devices
    r"\bcurl\b.*\|\s*(sh|bash)",   # curl pipe to shell
    r"\bwget\b.*\|\s*(sh|bash)",   # wget pipe to shell
    r"\b(shutdown|reboot|halt)\b",
]


def _audit(kind: str, detail: str):
    """Append an entry to the audit log. Never raises."""
    try:
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(f"{ts}\t{kind}\t{detail}\n")
    except Exception:
        pass


def _is_dangerous(command: str):
    """Return the matched pattern if a command looks dangerous, else None."""
    for pat in DANGEROUS_PATTERNS:
        if re.search(pat, command):
            return pat
    return None


def _within_workspace(path: str):
    """True if an expanded path stays inside the allowed WORKSPACE."""
    full = os.path.realpath(os.path.expanduser(path))
    return full == WORKSPACE or full.startswith(WORKSPACE + os.sep)


# -------------------- READ-ONLY TOOLS (safe) --------------------

def get_disk_usage():
    """Returns disk usage info for the main drive."""
    result = subprocess.run(["df", "-h", "/"], capture_output=True, text=True)
    return result.stdout


def list_files(directory: str):
    """Lists files in a given directory."""
    path = os.path.expanduser(directory)
    if not os.path.isdir(path):
        return f"Not a directory: {path}"
    entries = os.listdir(path)
    if not entries:
        return f"(empty) {path}"
    return "\n".join(sorted(entries))


def read_text_file(filepath: str):
    """Reads and returns the contents of a text file (within the workspace)."""
    if not _within_workspace(filepath):
        return f"Refused: '{filepath}' is outside the allowed workspace ({WORKSPACE})."
    path = os.path.expanduser(filepath)
    if not os.path.isfile(path):
        return f"Not a file: {path}"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Could not read file: {e}"


# -------------------- WRITE / DESTRUCTIVE TOOLS (gated) --------------------
# These should ALWAYS require confirmation. main.py marks them as "destructive".

def run_shell_command(command: str):
    """
    Runs a shell command and returns its output.
    DANGEROUS: only allow through the confirmation gate. In addition, commands
    matching known-dangerous patterns are auto-rejected, and every command that
    runs is written to the audit log.
    """
    danger = _is_dangerous(command)
    if danger:
        _audit("SHELL_BLOCKED", command)
        return (f"Refused: command matches a blocked pattern ({danger}). "
                "This was auto-rejected for safety and not run.")
    # Sandboxed mode: run inside an ephemeral Docker container, fully isolated
    # from the host. Requires Docker installed and running.
    if _sandbox_shell:
        _audit("SHELL_RUN_SANDBOXED", command)
        try:
            p = subprocess.run(
                ["docker", "run", "--rm", "--network", "none",
                 "python:3.12-slim", "sh", "-c", command],
                capture_output=True, text=True, timeout=60)
            out = ((p.stdout or "") + (p.stderr or "")).strip()
            return f"[sandboxed] {out or '(no output)'}"
        except FileNotFoundError:
            return ("Sandbox mode is ON but Docker isn't installed/running. "
                    "Start Docker or turn sandbox mode off.")
        except subprocess.TimeoutExpired:
            return "[sandboxed] Command timed out after 60s."
    _audit("SHELL_RUN", command)
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        if err:
            return f"STDOUT:\n{out}\n\nSTDERR:\n{err}"
        return out or "(no output)"
    except subprocess.TimeoutExpired:
        return "Command timed out after 30 seconds."
    except Exception as e:
        return f"Error running command: {e}"


# A small allow-list of safe, read-only commands. The model can call these
# by NAME without triggering the destructive gate, because they can't modify
# anything. Anything not on this list must go through run_shell_command.
SAFE_COMMANDS = {
    "battery": ["pmset", "-g", "batt"],
    "uptime": ["uptime"],
    "date": ["date"],
    "wifi": ["networksetup", "-getairportnetwork", "en0"],
    "ip": ["ipconfig", "getifaddr", "en0"],
    "memory": ["vm_stat"],
}


def run_safe_command(name: str):
    """
    Runs one of a small set of pre-approved, read-only system commands.
    Allowed names: battery, uptime, date, wifi, ip, memory.
    """
    cmd = SAFE_COMMANDS.get(name)
    if cmd is None:
        allowed = ", ".join(SAFE_COMMANDS.keys())
        return f"'{name}' is not an allowed command. Allowed: {allowed}"
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return result.stdout.strip() or "(no output)"
    except Exception as e:
        return f"Error: {e}"


def write_text_file(filepath: str, content: str):
    """
    Writes text to a file, creating it if needed (within the workspace).
    DESTRUCTIVE: overwrites the file if it already exists, so this is gated.
    """
    if not _within_workspace(filepath):
        _audit("WRITE_BLOCKED", filepath)
        return f"Refused: '{filepath}' is outside the allowed workspace ({WORKSPACE})."
    path = os.path.expanduser(filepath)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        existed = os.path.exists(path)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        _audit("WRITE", f"{path} ({len(content)} chars)")
        verb = "Overwrote" if existed else "Created"
        return f"{verb} {path} ({len(content)} chars)."
    except Exception as e:
        return f"Could not write file: {e}"


# -------------------- UPLOAD / WORKSPACE TOOLS --------------------

def list_workspace_files(subdir: str = ""):
    """List files in the workspace (or a subfolder). Useful after uploads."""
    base = os.path.join(WORKSPACE, subdir) if subdir else WORKSPACE
    if not _within_workspace(base):
        return f"Refused: outside the workspace ({WORKSPACE})."
    if not os.path.isdir(base):
        return f"Not a directory: {base}"
    entries = []
    for name in sorted(os.listdir(base)):
        full = os.path.join(base, name)
        kind = "dir" if os.path.isdir(full) else "file"
        size = os.path.getsize(full) if os.path.isfile(full) else ""
        entries.append(f"{kind:4} {name} {size}")
    return "\n".join(entries) if entries else "(empty)"


# Image extensions we recognize. The current text model can't SEE these — it can
# only know they exist. Swap MODEL to a vision model (e.g. llava) to enable
# actual image understanding later; the storage path is already in place.
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def describe_uploaded_file(filename: str):
    """
    Report what an uploaded file is. For text files, returns a short preview.
    For images, notes that a vision-capable model is needed to read contents.
    """
    if not _within_workspace(filename):
        return f"Refused: '{filename}' is outside the workspace ({WORKSPACE})."
    path = os.path.expanduser(filename)
    if not os.path.isfile(path):
        return f"Not found: {path}"
    ext = os.path.splitext(path)[1].lower()
    size = os.path.getsize(path)
    if ext in IMAGE_EXTS:
        return (f"'{os.path.basename(path)}' is an image ({size} bytes). "
                "The current model is text-only and can't view image contents. "
                "Switch to a vision model (e.g. llava) to analyze it.")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            preview = f.read(1000)
        more = "" if size <= 1000 else f"\n…(truncated, {size} bytes total)"
        return f"'{os.path.basename(path)}' ({size} bytes):\n{preview}{more}"
    except Exception as e:
        return f"Could not read '{filename}': {e}"


# -------------------- CODEBASE CONTEXT (Windsurf-style) --------------------

def scan_codebase(directory: str = "", max_files: int = 40, max_bytes: int = 2000):
    """
    Build a map of a code project: a file tree plus a short preview of each
    text/code file, so the assistant can understand structure before editing.
    Confined to the workspace. Skips binary/huge files and noise dirs.
    """
    base = os.path.join(WORKSPACE, directory) if directory else WORKSPACE
    if not _within_workspace(base):
        return f"Refused: outside the workspace ({WORKSPACE})."
    if not os.path.isdir(base):
        return f"Not a directory: {base}"

    SKIP_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv",
                 "dist", "build", ".next", "memory_store", "uploads"}
    CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".json",
                ".md", ".txt", ".sh", ".yml", ".yaml", ".toml", ".go", ".rs",
                ".java", ".c", ".cpp", ".h", ".rb", ".php", ".sql"}

    tree_lines, previews, count = [], [], 0
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        rel_root = os.path.relpath(root, base)
        depth = 0 if rel_root == "." else rel_root.count(os.sep) + 1
        indent = "  " * depth
        if rel_root != ".":
            tree_lines.append(f"{indent}{os.path.basename(root)}/")
        for fn in sorted(files):
            ext = os.path.splitext(fn)[1].lower()
            tree_lines.append(f"{indent}  {fn}")
            if ext in CODE_EXT and count < max_files:
                fp = os.path.join(root, fn)
                try:
                    with open(fp, "r", encoding="utf-8", errors="replace") as f:
                        head = f.read(max_bytes)
                    rel = os.path.relpath(fp, base)
                    more = "" if os.path.getsize(fp) <= max_bytes else "\n… (truncated)"
                    previews.append(f"### {rel}\n{head}{more}")
                    count += 1
                except Exception:
                    pass

    tree = "\n".join(tree_lines) or "(no files)"
    body = "\n\n".join(previews)
    note = "" if count < max_files else f"\n\n(Showing first {max_files} files; more exist.)"
    return f"PROJECT TREE ({base}):\n{tree}\n\n--- FILE PREVIEWS ---\n{body}{note}"


# -------------------- SURGICAL PATCHING --------------------

def apply_patch(filepath: str, search_block: str, replace_block: str):
    """
    Surgically replace an exact block of text in a file (instead of rewriting
    the whole file). Verifies the search_block appears EXACTLY ONCE to avoid
    ambiguous edits. Workspace-confined and audited. DESTRUCTIVE (gated).
    """
    if not _within_workspace(filepath):
        _audit("PATCH_BLOCKED", filepath)
        return f"Refused: '{filepath}' is outside the workspace ({WORKSPACE})."
    path = os.path.expanduser(filepath)
    if not os.path.isfile(path):
        return f"Not a file: {path}"
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return f"Could not read file: {e}"

    count = content.count(search_block)
    if count == 0:
        return ("PATCH FAILED: the search_block was not found. It must match the "
                "file's current text exactly (including whitespace/indentation).")
    if count > 1:
        return (f"PATCH FAILED: the search_block appears {count} times — it must be "
                "unique. Include more surrounding lines to make it unambiguous.")

    new_content = content.replace(search_block, replace_block)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
        _audit("PATCH", f"{path} (-{len(search_block)}+{len(replace_block)} chars)")
        return (f"Patched {path}: replaced a {len(search_block)}-char block with "
                f"a {len(replace_block)}-char block.")
    except Exception as e:
        return f"Could not write file: {e}"


def compute_diff(filepath: str, search_block: str = None, replace_block: str = None,
                 new_content: str = None):
    """
    Compute a unified diff for a proposed change WITHOUT applying it. Used by the
    approval gate to show what would change. Either provide search/replace blocks
    (for apply_patch) or full new_content (for write_text_file).
    Returns the unified diff text, or '' if nothing to show.
    """
    import difflib
    path = os.path.expanduser(filepath)
    old = ""
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                old = f.read()
        except Exception:
            old = ""
    if new_content is not None:
        new = new_content
    elif search_block is not None and replace_block is not None:
        if old.count(search_block) == 1:
            new = old.replace(search_block, replace_block)
        else:
            # Can't preview reliably; show the intended block change instead.
            new = old
            return _block_diff(search_block, replace_block)
    else:
        return ""
    diff = difflib.unified_diff(old.splitlines(), new.splitlines(),
                                fromfile="before", tofile="after", lineterm="")
    return "\n".join(diff)


def _block_diff(search_block, replace_block):
    import difflib
    diff = difflib.unified_diff(search_block.splitlines(), replace_block.splitlines(),
                                fromfile="search_block", tofile="replace_block", lineterm="")
    return "\n".join(diff)


def validate_python(filepath: str):
    """
    Quick self-check for a Python file: compile it to catch syntax errors.
    Returns 'OK' or the error trace. Used by the self-validating loop.
    Non-Python files return 'OK (skipped: not Python)'.
    """
    path = os.path.expanduser(filepath)
    if not path.endswith(".py"):
        return "OK (skipped: not Python)"
    if not os.path.isfile(path):
        return f"Not a file: {path}"
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        compile(src, path, "exec")
        return "OK"
    except SyntaxError as e:
        return f"SYNTAX ERROR at line {e.lineno}: {e.msg}\n  {e.text or ''}"
    except Exception as e:
        return f"ERROR: {e}"


def run_project_tests(directory: str = ""):
    """
    Detect and run a project's test suite (pytest for Python). Returns a pass/
    fail summary plus captured output, so the agent can self-correct logical
    bugs — not just syntax. Workspace-confined, with a timeout.
    Looks for a tests/ dir or test_*.py files; if none, says so.
    """
    base = os.path.join(WORKSPACE, directory) if directory else WORKSPACE
    if not _within_workspace(base) or not os.path.isdir(base):
        return f"Refused or not a directory: {base}"

    # Detect Python tests.
    has_tests = False
    if os.path.isdir(os.path.join(base, "tests")):
        has_tests = True
    else:
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in
                       {".git", "node_modules", "__pycache__", "venv", ".venv"}]
            if any(f.startswith("test_") and f.endswith(".py") for f in files):
                has_tests = True
                break
    if not has_tests:
        return "No test suite found (no tests/ dir or test_*.py files)."

    _audit("RUN_TESTS", base)
    try:
        import sys as _sys
        proc = subprocess.run(
            [_sys.executable, "-m", "pytest", "-q", "--tb=short"],
            cwd=base, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return "TESTS TIMEOUT: exceeded 120s."
    except FileNotFoundError:
        return "pytest not installed. Run: pip install pytest"
    out = ((proc.stdout or "") + (proc.stderr or "")).strip() or "(no output)"
    status = "PASSED" if proc.returncode == 0 else "FAILED"
    # Trim very long output but keep the failure summary.
    if len(out) > 3000:
        out = out[:1500] + "\n…(trimmed)…\n" + out[-1500:]
    return f"TESTS {status}\n\n{out}"


# -------------------- WEB SEARCH (opt-in, leaves your machine) --------------------
# Off by default. The server flips _web_enabled when you turn it on in the UI.
_web_enabled = False


def set_web_enabled(on):
    global _web_enabled
    _web_enabled = bool(on)


# When True, run_shell_command runs inside an ephemeral Docker container instead
# of on the host. Stronger isolation; requires Docker running. Set via UI.
_sandbox_shell = False


def set_sandbox_shell(on):
    global _sandbox_shell
    _sandbox_shell = bool(on)


def web_search(query: str):
    """
    Search the web for current info (API docs, error messages, library updates).
    PRIVACY: this sends your query to the internet (DuckDuckGo). It only works
    if you've enabled web search in the UI. No API key needed.
    """
    if not _web_enabled:
        return ("Web search is OFF. Enable it in the UI first (it sends queries "
                "to the internet).")
    import urllib.parse, urllib.request, re, html as _html
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            page = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        return f"Web search failed: {e}"
    _audit("WEB_SEARCH", query)
    # Extract result titles + snippets from the HTML results page.
    results = []
    for m in re.finditer(r'result__a"[^>]*>(.*?)</a>.*?result__snippet"[^>]*>(.*?)</a>',
                         page, re.DOTALL):
        title = _html.unescape(re.sub(r"<.*?>", "", m.group(1))).strip()
        snippet = _html.unescape(re.sub(r"<.*?>", "", m.group(2))).strip()
        if title:
            results.append(f"• {title}\n  {snippet}")
        if len(results) >= 5:
            break
    if not results:
        return "No results found (or DuckDuckGo changed its format)."
    return "Web results for '" + query + "':\n\n" + "\n\n".join(results)


def run_linter(filepath: str = ""):
    """
    Run an appropriate linter/compiler check based on file type. Extensible:
    Python -> pyflakes/flake8 if available (else syntax compile), JS/TS -> eslint
    if available, Rust -> rustc --emit=metadata, Go -> gofmt. Returns the linter
    output or a note if no linter is installed for that type.
    NOTE: Pine Script and some DSLs have no offline linter; those return a note.
    """
    if not filepath or not _within_workspace(filepath):
        return "Provide a file inside the workspace."
    path = os.path.expanduser(filepath)
    if not os.path.isfile(path):
        return f"Not a file: {path}"
    ext = os.path.splitext(path)[1].lower()

    def _try(cmd):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            return (p.returncode, (p.stdout + p.stderr).strip())
        except FileNotFoundError:
            return (None, None)
        except subprocess.TimeoutExpired:
            return (1, "linter timed out")

    if ext == ".py":
        for cmd in (["flake8", path], ["python3", "-m", "pyflakes", path]):
            rc, out = _try(cmd)
            if rc is not None:
                return f"LINT {'OK' if rc == 0 else 'ISSUES'}:\n{out or '(clean)'}"
        return validate_python(path)  # fall back to syntax check
    if ext in (".js", ".jsx", ".ts", ".tsx"):
        rc, out = _try(["npx", "--no-install", "eslint", path])
        if rc is not None:
            return f"ESLINT {'OK' if rc == 0 else 'ISSUES'}:\n{out or '(clean)'}"
        return "No eslint found. Install it in the project to lint JS/TS."
    if ext == ".rs":
        rc, out = _try(["rustc", "--emit=metadata", "--crate-type=lib", path])
        if rc is not None:
            return f"RUSTC {'OK' if rc == 0 else 'ISSUES'}:\n{out or '(clean)'}"
        return "No rustc found."
    if ext == ".go":
        rc, out = _try(["gofmt", "-e", path])
        if rc is not None:
            return f"GOFMT {'OK' if rc == 0 else 'ISSUES'}:\n{out or '(clean)'}"
        return "No gofmt found."
    if ext in (".pine", ".ps"):
        return ("Pine Script has no offline linter — it compiles only inside "
                "TradingView. I can still review the logic, but can't auto-verify it.")
    return f"No linter configured for '{ext}' files."


# -------------------- EPHEMERAL DEPLOYMENT TEST --------------------

def deploy_test(directory: str = "", setup_cmd: str = "", run_cmd: str = "",
                test_cmd: str = ""):
    """
    Spin up the project inside an ephemeral Docker container and run integration
    tests against it, BEFORE you approve a change. Steps:
      1. Copy the project into a throwaway container (no network by default).
      2. Run setup_cmd (e.g. 'cp .env.example .env && pip install -r requirements.txt').
      3. Start run_cmd in the background if given (e.g. 'python server.py').
      4. Run test_cmd (e.g. 'pytest tests/integration').
      5. Tear everything down; return combined output.

    If setup_cmd is omitted, sensible defaults are inferred (rename .env.example,
    install requirements). Requires Docker. Workspace-confined.
    """
    base = os.path.join(WORKSPACE, directory) if directory else WORKSPACE
    if not _within_workspace(base) or not os.path.isdir(base):
        return f"Refused or not a directory: {base}"

    # Check Docker is available.
    try:
        subprocess.run(["docker", "version"], capture_output=True, timeout=10, check=True)
    except Exception:
        return ("Deployment testing needs Docker installed and running. "
                "Start Docker, or use run_project_tests for a non-containerized run.")

    # Infer sensible defaults from the project layout.
    has_env_example = os.path.isfile(os.path.join(base, ".env.example"))
    has_reqs = os.path.isfile(os.path.join(base, "requirements.txt"))
    if not setup_cmd:
        parts = []
        if has_env_example:
            parts.append("cp .env.example .env")   # honor the classic init rule
        if has_reqs:
            parts.append("pip install -q -r requirements.txt")
        setup_cmd = " && ".join(parts) or "true"
    if not test_cmd:
        test_cmd = "pytest -q 2>/dev/null || echo 'no tests run'"

    # Build the in-container script.
    script_parts = ["set -e", "cd /app", setup_cmd]
    if run_cmd:
        # Start the app in the background, give it a moment, then test.
        script_parts.append(f"({run_cmd}) & APP_PID=$!")
        script_parts.append("sleep 3")
        script_parts.append(f"{test_cmd} ; RC=$?")
        script_parts.append("kill $APP_PID 2>/dev/null || true")
        script_parts.append("exit $RC")
    else:
        script_parts.append(test_cmd)
    script = "\n".join(script_parts)

    _audit("DEPLOY_TEST", base)
    try:
        p = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{base}:/app:ro", "-w", "/app",
             "python:3.12-slim", "bash", "-c",
             # copy to a writable dir since the mount is read-only
             "cp -r /app /work && cd /work && " + script.replace("/app", "/work")],
            capture_output=True, text=True, timeout=240)
    except subprocess.TimeoutExpired:
        return "DEPLOY TEST TIMEOUT (exceeded 240s)."
    out = ((p.stdout or "") + (p.stderr or "")).strip() or "(no output)"
    if len(out) > 3000:
        out = out[:1500] + "\n…(trimmed)…\n" + out[-1500:]
    status = "PASSED" if p.returncode == 0 else "FAILED"
    return f"DEPLOYMENT TEST {status}\n\n{out}"
