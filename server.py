"""
server.py
---------
A small Flask web server that puts a browser UI in front of the assistant.

Voice is handled entirely by the BROWSER (the Web Speech API), so there is
nothing extra to install for speech. This server just handles the chat logic
and the human-in-the-loop approval gate.

Approval flow across the web:
  1. Browser POSTs your message to /chat.
  2. If the model wants a SAFE tool, the server runs it and replies normally.
  3. If the model wants a DESTRUCTIVE tool, the server does NOT run it. It
     replies with {"pending": {...}} describing the action.
  4. The browser shows a confirm dialog. On approval it POSTs to /approve,
     which finally runs the tool and continues the conversation.

Run with:  python server.py
Then open: http://127.0.0.1:5000
"""

import ollama
from flask import Flask, request, jsonify, render_template, Response, stream_with_context
import json as _json
from registry import SCHEMAS, FUNCTIONS, DESTRUCTIVE, TOOLS
import memory
import conversations
import projects
import profile
import providers
import code_index
import git_txn
import mcp_client
import runtime_config
from tools import actions

# Two models: a fast text model for normal turns, and a vision model for image
# turns. TEXT_MODEL handles tool-calling well; VISION_MODEL can see images but
# is weaker at tools, so image turns answer directly instead of chaining tools.
TEXT_MODEL = "llama3.1:8b"     # default text model (change via UI)
VISION_MODEL = "llava"         # pull with: ollama pull llava
CODING_MODEL = "llama3.1:8b"   # used on engineer-persona turns; set to a coding
                               # model you've pulled (e.g. qwen2.5-coder) via UI
MODEL = TEXT_MODEL             # current default

# Model selection mode: "auto" picks based on whether images are involved;
# "text" or "vision" force one. Controlled from the UI.
MODEL_MODE = "auto"

# Which backend handles turns: "local" (Ollama, private), "claude", or "gemini".
# In cloud mode this defaults to a cloud provider since Ollama isn't present.
PROVIDER = runtime_config.DEFAULT_PROVIDER

# Keep at most this many recent messages (plus the system prompt) in context,
# so long sessions don't overflow the model's context window.
MAX_HISTORY_MESSAGES = 30

app = Flask(__name__)

import os


def _load_persona():
    """
    Load the Jarvis Software-Engineering persona from SYSTEM_PROMPT.md so the
    server and that file stay in sync. The prompt lives between the two dashed
    delimiter lines in that file. Falls back to a built-in default if the file
    is missing or unreadable.
    """
    fallback = (
        "You are a helpful local assistant on a Mac. When the user asks you "
        "to do something on their computer, use the available tools. Be concise."
    )
    path = os.path.join(os.path.dirname(__file__), "SYSTEM_PROMPT.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        # The actual prompt sits between lines of 50+ dashes.
        parts = text.split("-----------------------------------------------------------------------")
        if len(parts) >= 3 and parts[1].strip():
            return parts[1].strip()
    except Exception:
        pass
    return fallback


SYSTEM_PERSONA = _load_persona()

# Guidance appended to every persona: stops the "here is a function call" JSON
# leak, and nudges toward warmer, clearer, Claude-like prose.
_STYLE_GUIDE = (
    "\n\n--- How to respond ---\n"
    "When you need to use a tool, invoke it through the tool mechanism — do NOT "
    "write the function call as text, do NOT print JSON like {\"name\": ...}, and "
    "do NOT say 'here is a function call'. Either call the tool or answer in "
    "plain language.\n"
    "Write like a thoughtful, friendly expert: clear, warm, and direct. Use short "
    "paragraphs and only use lists when they genuinely help. Lead with the answer, "
    "then add brief context. Avoid robotic phrasing and avoid restating the "
    "question. When you run tools, explain what you found in natural language "
    "rather than dumping raw output."
)

# The two switchable personas. "engineer" is the Write-Test-Fix coding agent
# loaded from SYSTEM_PROMPT.md; "assistant" is the general-purpose helper.
PERSONAS = {
    "engineer": SYSTEM_PERSONA + _STYLE_GUIDE,
    "assistant": (
        "You are Jarvis, a helpful local assistant on a Mac. When the user asks "
        "you to do something on their computer, use the available tools to "
        "actually do it." + _STYLE_GUIDE
    ),
}
def _system_content(persona_key):
    """Persona prompt + always-on config profile + this project's rules."""
    base = PERSONAS[persona_key]
    extras = []
    try:
        prof = profile.get().strip()
        if prof:
            extras.append("--- Environment profile (always applies) ---\n" + prof)
    except Exception:
        pass
    try:
        rules = projects.get_rules(ACTIVE_PROJECT).strip()
        if rules:
            extras.append("--- Project rules (.jarvisrules — strictly follow these) ---\n" + rules)
    except Exception:
        pass
    return base + ("\n\n" + "\n\n".join(extras) if extras else "")


ACTIVE_PERSONA = "engineer"  # default at launch
ACTIVE_PROJECT = projects.DEFAULT_PROJECT  # set before first _system_content call

# Conversation history kept in memory for a single local user.
HISTORY = [{
    "role": "system",
    "content": _system_content(ACTIVE_PERSONA),
}]

# Id of the conversation currently being recorded to disk.
CONV_ID = conversations.new_id()

# Initialize storage to point at the default project's folders.
projects.create(ACTIVE_PROJECT)
memory.set_project(ACTIVE_PROJECT)
code_index.set_project(ACTIVE_PROJECT)
conversations.set_dir(projects.conversations_dir(ACTIVE_PROJECT))

# Connect any configured MCP servers (external tool servers). Safe if none.
# Connect any configured MCP servers (external tool servers). Subprocess MCP
# servers only run in local mode — a cloud container can't spawn npx/uvx safely.
if runtime_config.enabled("mcp_subprocess"):
    try:
        _mcp_summary = mcp_client.connect_all()
        if _mcp_summary:
            print("MCP servers connected:", _mcp_summary)
        import atexit
        atexit.register(mcp_client.shutdown)
    except Exception as _e:
        print("MCP connect skipped:", _e)
else:
    print("MCP subprocess servers disabled (cloud mode).")


def _choose_model(turn_mentions_image=False):
    """
    Pick the local model for this turn. Vision turns use the vision model. On
    engineer-persona text turns we use CODING_MODEL (a model you've tuned for
    code); otherwise TEXT_MODEL. 'text'/'vision' force one regardless.
    """
    if MODEL_MODE == "vision":
        return VISION_MODEL
    if MODEL_MODE == "auto" and (turn_mentions_image or projects.has_images(ACTIVE_PROJECT)):
        return VISION_MODEL
    # text turn: prefer the coding model when in the engineer persona
    if ACTIVE_PERSONA == "engineer":
        return CODING_MODEL
    return TEXT_MODEL


SESSION_SUMMARY = ""  # running compressed memory of older turns


def _trim_history():
    """
    Keep HISTORY lean without losing early context. When it exceeds the limit,
    compress the OLDEST messages into a running 'session summary' (via a quick
    local model call) and keep that near the system prompt, instead of just
    slicing old turns away. This preserves early constraints indefinitely.
    """
    global HISTORY, SESSION_SUMMARY
    if len(HISTORY) <= MAX_HISTORY_MESSAGES + 1:
        return
    system = HISTORY[0]
    # The block we're about to drop (oldest ~15 user/assistant messages).
    drop_count = max(1, MAX_HISTORY_MESSAGES // 2)
    old_block = HISTORY[1:1 + drop_count]
    recent = HISTORY[1 + drop_count:]

    # Build text of the old block for summarization.
    snippet = []
    for m in old_block:
        role = m.get("role")
        if role in ("user", "assistant") and m.get("content"):
            snippet.append(f"{role}: {m['content'][:500]}")
    if snippet:
        try:
            prompt = ("Compress the following conversation excerpt into a tight "
                      "bulleted list of durable facts, decisions, and constraints "
                      "that must be remembered for the rest of the session. Be "
                      "terse.\n\n" + "\n".join(snippet))
            resp = ollama.chat(model=TEXT_MODEL,
                               messages=[{"role": "user", "content": prompt}])
            new_summary = resp["message"]["content"].strip()
            # Merge with any prior summary, keeping it bounded.
            SESSION_SUMMARY = (SESSION_SUMMARY + "\n" + new_summary).strip()[-2000:]
        except Exception:
            pass  # never block on summarization

    # Rebuild: system prompt, then the running summary, then recent turns.
    rebuilt = [system]
    if SESSION_SUMMARY:
        rebuilt.append({"role": "system",
                        "content": "--- Session summary (earlier context) ---\n" + SESSION_SUMMARY})
    rebuilt.extend(recent)
    HISTORY = rebuilt


def _run_tool(name, args):
    """Execute a registered tool by name, returning a string result."""
    # MCP tools are namespaced mcp__server__tool and dispatched to their server.
    if name.startswith("mcp__"):
        try:
            return str(mcp_client.call(name, args))
        except Exception as e:
            return f"MCP error: {e}"
    if name not in FUNCTIONS:
        return f"Unknown tool: {name}"
    try:
        return str(FUNCTIONS[name](**args))
    except Exception as e:
        return f"Error: {e}"


def _all_schemas():
    """Local tool schemas + any connected MCP server tools."""
    try:
        return SCHEMAS + mcp_client.list_tools()
    except Exception:
        return SCHEMAS


def _is_destructive(name):
    """True if a tool needs approval — local DESTRUCTIVE map or an MCP write-tool."""
    if DESTRUCTIVE.get(name, False):
        return True
    try:
        if name.startswith("mcp__"):
            return name in mcp_client.destructive_names()
    except Exception:
        pass
    return False


def _final_answer(model=None):
    """Ask the model to summarize after tool results are in HISTORY."""
    final = ollama.chat(model=model or MODEL, messages=HISTORY)
    msg = final["message"]
    HISTORY.append(msg)
    _trim_history()
    conversations.save(CONV_ID, HISTORY)
    return msg["content"]


@app.route("/")
def index():
    return render_template("index.html")


MAX_TOOL_STEPS = 5  # cap tool calls per turn so a confused model can't loop forever


import re as _re

def _extract_leaked_toolcall(text):
    """
    Small models (like llama3.1:8b) sometimes PRINT a tool call as text instead
    of actually invoking it, e.g.:
        {"name": "jarvis_run_tests", "parameters": {...}}
    This detects that pattern and returns (name, args) so we can run the real
    tool, or None if the text is a normal answer. Handles 'parameters' or
    'arguments' as the key, with or without ``` fences.
    """
    if not text or "{" not in text:
        return None
    # Find a JSON object that has a "name" and a params/arguments key.
    candidates = _re.findall(r'\{[^{}]*"name"\s*:\s*"([a-zA-Z_]+)"[^{}]*\}', text)
    # Try to parse the full object for arguments.
    for m in _re.finditer(r'\{.*?"name"\s*:\s*"([a-zA-Z_]+)".*?\}', text, _re.DOTALL):
        blob = m.group(0)
        name = m.group(1)
        if name not in FUNCTIONS:
            continue
        args = {}
        # pull out parameters/arguments object if present
        pm = _re.search(r'"(?:parameters|arguments)"\s*:\s*(\{.*\})', blob, _re.DOTALL)
        if pm:
            try:
                args = _json.loads(pm.group(1))
            except Exception:
                args = {}
        # Normalize string "None"/"null" to absent
        args = {k: (None if v in ("None", "null") else v) for k, v in args.items()}
        args = {k: v for k, v in args.items() if v is not None}
        return name, args
    return None


def _pending_payload(name, args, ran):
    """
    Build the 'pending' approval payload. For file-changing tools, attach a
    unified diff so the UI can show exactly what will change before approval.
    """
    payload = {"name": name, "args": args, "ran": ran}
    try:
        if name == "apply_patch":
            payload["diff"] = actions.compute_diff(
                args.get("filepath", ""),
                search_block=args.get("search_block"),
                replace_block=args.get("replace_block"))
        elif name == "write_text_file":
            payload["diff"] = actions.compute_diff(
                args.get("filepath", ""),
                new_content=args.get("content", ""))
    except Exception:
        pass
    return payload


def _looks_like_coding(text):
    """
    Heuristic: does this request warrant the Engineer sub-agent? Used by the
    orchestration router when the user is in the general 'assistant' persona.
    """
    t = text.lower()
    signals = ["code", "function", "script", "bug", "error", "refactor",
               "write a", "fix the", "patch", "test", "compile", "implement",
               "class ", "def ", "api", "regex", "algorithm", ".py", ".js",
               "debug", "stack trace", "traceback", "lint"]
    return sum(1 for s in signals if s in t) >= 1


def _run_tool_loop(ran_so_far=None, model=None):
    """
    Drive the autonomous tool loop with the text model. Repeatedly ask the
    model, run any SAFE tool it requests, feed the result back, and continue —
    until the model returns a plain answer, hits a DESTRUCTIVE tool (which
    pauses the chain for approval), or hits the step cap.
    """
    model = model or TEXT_MODEL
    ran = ran_so_far or []
    for _ in range(MAX_TOOL_STEPS - len(ran)):
        response = ollama.chat(model=model, messages=HISTORY, tools=_all_schemas())
        msg = response["message"]
        HISTORY.append(msg)

        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            # The model returned text. But small models sometimes PRINT a tool
            # call instead of invoking it — catch that and run it for real.
            leaked = _extract_leaked_toolcall(msg.get("content", ""))
            if leaked:
                name, args = leaked
                if _is_destructive(name):
                    conversations.save(CONV_ID, HISTORY)
                    return jsonify({"pending": _pending_payload(name, args, ran)})
                result = _run_tool(name, args)
                HISTORY.append({"role": "tool", "name": name, "content": result})
                ran.append(name)
                continue  # let the model react to the real result
            # Genuine final answer.
            _trim_history()
            conversations.save(CONV_ID, HISTORY)
            return jsonify({"reply": msg["content"], "ran": ran})

        call = tool_calls[0]
        name = call["function"]["name"]
        args = call["function"]["arguments"]

        if _is_destructive(name):
            # Pause the whole chain. The browser approves, then /approve resumes
            # the loop by calling back in. We pass along what's run so far so the
            # step cap still applies across the pause.
            conversations.save(CONV_ID, HISTORY)
            return jsonify({"pending": _pending_payload(name, args, ran)})

        # Safe tool: run it, record it, and let the loop continue so the model
        # can chain another step based on the result.
        result = _run_tool(name, args)
        HISTORY.append({"role": "tool", "name": name, "content": result})
        ran.append(name)

    # Hit the step cap — ask for one final summary without more tools.
    _trim_history()
    conversations.save(CONV_ID, HISTORY)
    final = ollama.chat(model=model, messages=HISTORY)
    HISTORY.append(final["message"])
    conversations.save(CONV_ID, HISTORY)
    return jsonify({"reply": final["message"]["content"], "ran": ran,
                    "note": f"stopped after {MAX_TOOL_STEPS} tool steps"})


def _vision_answer():
    """
    Answer an image-involving turn with the vision model, directly (no tool
    chaining — llava is weak at tools). Attaches the project's image files so
    the model can actually see them.
    """
    import os
    image_paths = []
    up = projects.uploads_dir(ACTIVE_PROJECT)
    for f in sorted(os.listdir(up)):
        if os.path.splitext(f)[1].lower() in actions.IMAGE_EXTS:
            image_paths.append(os.path.join(up, f))

    # Ollama accepts image file paths via the 'images' field on the last message.
    msgs = [m for m in HISTORY]
    if image_paths and msgs and msgs[-1]["role"] == "user":
        msgs[-1] = {**msgs[-1], "images": image_paths}

    final = ollama.chat(model=VISION_MODEL, messages=msgs)
    msg = final["message"]
    HISTORY.append({"role": "assistant", "content": msg["content"]})
    _trim_history()
    conversations.save(CONV_ID, HISTORY)
    return jsonify({"reply": msg["content"], "model": VISION_MODEL})


def _run_cloud_loop(provider, ran_so_far=None):
    """
    Agentic tool loop driven by a CLOUD provider (Claude or Gemini). Same shape
    as the local loop: the provider proposes a tool, we run safe ones and feed
    results back, pausing on destructive ones for approval. Every step here goes
    to the cloud.
    """
    turn_fn = (providers.claude_tool_turn if provider == "claude"
               else providers.gemini_tool_turn)
    summary_fn = (providers.claude_chat if provider == "claude"
                  else providers.gemini_chat)
    ran = ran_so_far or []
    for _ in range(MAX_TOOL_STEPS - len(ran)):
        turn = turn_fn(HISTORY, _all_schemas())
        if turn["type"] == "text":
            HISTORY.append({"role": "assistant", "content": turn["text"]})
            _trim_history(); conversations.save(CONV_ID, HISTORY)
            return jsonify({"reply": turn["text"], "ran": ran, "model": provider})

        name, args = turn["name"], turn.get("args", {})
        if turn.get("text"):
            HISTORY.append({"role": "assistant", "content": turn["text"]})

        if _is_destructive(name):
            conversations.save(CONV_ID, HISTORY)
            p = _pending_payload(name, args, ran); p["provider"] = provider
            return jsonify({"pending": p})

        result = _run_tool(name, args)
        HISTORY.append({"role": "tool", "name": name, "content": result})
        ran.append(name)

    txt = summary_fn(HISTORY, stream=False)
    HISTORY.append({"role": "assistant", "content": txt})
    _trim_history(); conversations.save(CONV_ID, HISTORY)
    return jsonify({"reply": txt, "ran": ran, "model": provider,
                    "note": f"stopped after {MAX_TOOL_STEPS} tool steps"})


@app.route("/chat", methods=["POST"])
def chat():
    user_input = request.json.get("message", "").strip()
    if not user_input:
        return jsonify({"reply": "(empty message)"})

    HISTORY.append({"role": "user", "content": user_input})

    # Phase 3: quietly pull relevant long-term memories and give them to the
    # model as context, so it "just knows" things the user mentioned before.
    try:
        relevant = memory.recall(user_input, n=3)
        if relevant and not relevant.startswith("("):
            HISTORY.append({
                "role": "system",
                "content": f"Relevant things you remember about the user:\n{relevant}",
            })
    except Exception:
        pass  # memory is optional; never block a turn on it

    # Cloud providers now both run the agentic tool loop.
    if PROVIDER in ("claude", "gemini"):
        return _run_cloud_loop(PROVIDER)

    # Decide which model handles this turn. Vision turns answer directly;
    # text turns run the autonomous tool loop.
    chosen = _choose_model()
    if chosen == VISION_MODEL:
        return _vision_answer()
    return _run_tool_loop(model=chosen)


def _sse(event, data):
    """Format one Server-Sent Event."""
    return f"event: {event}\ndata: {_json.dumps(data)}\n\n"


@app.route("/chat/stream", methods=["POST"])
def chat_stream():
    """
    Streaming version of /chat using Server-Sent Events. It runs any safe tool
    chain first (emitting 'tool' events), and streams the final text answer
    token-by-token ('token' events). If a destructive tool comes up, it emits a
    'pending' event and stops — the browser then uses the normal /approve flow.
    """
    user_input = (request.json or {}).get("message", "").strip()
    if not user_input:
        return Response(_sse("done", {"reply": "(empty message)"}),
                        mimetype="text/event-stream")

    HISTORY.append({"role": "user", "content": user_input})
    try:
        relevant = memory.recall(user_input, n=3)
        if relevant and not relevant.startswith("("):
            HISTORY.append({"role": "system",
                            "content": f"Relevant things you remember about the user:\n{relevant}"})
    except Exception:
        pass

    # Orchestration router: if the general assistant gets a coding request
    # (local provider), delegate this turn to the Engineer sub-agent — swap in
    # the engineer system prompt and coding model, and tell the user.
    delegated = False
    if (PROVIDER == "local" and ACTIVE_PERSONA == "assistant"
            and _looks_like_coding(user_input)):
        delegated = True
        HISTORY[0] = {"role": "system", "content": _system_content("engineer")}

    chosen = CODING_MODEL if delegated else _choose_model()

    @stream_with_context
    def generate():
        if delegated:
            yield _sse("delegate", {"to": "engineer"})
        # Cloud providers (Claude or Gemini): run the agentic tool loop,
        # emitting tool events, then stream the final answer out.
        if PROVIDER in ("claude", "gemini"):
            turn_fn = (providers.claude_tool_turn if PROVIDER == "claude"
                       else providers.gemini_tool_turn)
            summary_fn = (providers.claude_chat if PROVIDER == "claude"
                          else providers.gemini_chat)
            yield _sse("model", {"model": PROVIDER, "cloud": True})
            ran = []
            for _ in range(MAX_TOOL_STEPS):
                turn = turn_fn(HISTORY, _all_schemas())
                if turn["type"] == "text":
                    HISTORY.append({"role": "assistant", "content": turn["text"]})
                    _trim_history(); conversations.save(CONV_ID, HISTORY)
                    for w in turn["text"].split(" "):
                        yield _sse("token", {"t": w + " "})
                    yield _sse("done", {"reply": turn["text"], "ran": ran, "model": PROVIDER})
                    return
                name, args = turn["name"], turn.get("args", {})
                if turn.get("text"):
                    HISTORY.append({"role": "assistant", "content": turn["text"]})
                if _is_destructive(name):
                    conversations.save(CONV_ID, HISTORY)
                    pp = _pending_payload(name, args, ran); pp["provider"] = PROVIDER
                    yield _sse("pending", pp)
                    return
                yield _sse("tool", {"name": name})
                result = _run_tool(name, args)
                HISTORY.append({"role": "tool", "name": name, "content": result})
                ran.append(name)
            txt = summary_fn(HISTORY, stream=False)
            HISTORY.append({"role": "assistant", "content": txt})
            _trim_history(); conversations.save(CONV_ID, HISTORY)
            yield _sse("done", {"reply": txt, "ran": ran, "model": PROVIDER})
            return

        # Vision turns: stream the vision model's answer directly.
        if chosen == VISION_MODEL:
            import os
            up = projects.uploads_dir(ACTIVE_PROJECT)
            imgs = [os.path.join(up, f) for f in sorted(os.listdir(up))
                    if os.path.splitext(f)[1].lower() in actions.IMAGE_EXTS]
            msgs = [m for m in HISTORY]
            if imgs and msgs and msgs[-1]["role"] == "user":
                msgs[-1] = {**msgs[-1], "images": imgs}
            yield _sse("model", {"model": VISION_MODEL})
            full = ""
            for chunk in ollama.chat(model=VISION_MODEL, messages=msgs, stream=True):
                tok = chunk.get("message", {}).get("content", "")
                if tok:
                    full += tok
                    yield _sse("token", {"t": tok})
            HISTORY.append({"role": "assistant", "content": full})
            _trim_history(); conversations.save(CONV_ID, HISTORY)
            yield _sse("done", {"reply": full, "model": VISION_MODEL})
            return

        # Text turns: run the safe-tool chain, then stream the final answer.
        ran = []
        for _ in range(MAX_TOOL_STEPS):
            resp = ollama.chat(model=chosen, messages=HISTORY, tools=_all_schemas())
            msg = resp["message"]; HISTORY.append(msg)
            calls = msg.get("tool_calls")
            if not calls:
                break
            call = calls[0]; name = call["function"]["name"]; args = call["function"]["arguments"]
            if _is_destructive(name):
                conversations.save(CONV_ID, HISTORY)
                yield _sse("pending", _pending_payload(name, args, ran))
                return
            yield _sse("tool", {"name": name})
            result = _run_tool(name, args)
            HISTORY.append({"role": "tool", "name": name, "content": result})
            ran.append(name)

        # Stream the final answer token-by-token.
        yield _sse("model", {"model": chosen, "ran": ran})
        full = ""
        for chunk in ollama.chat(model=chosen, messages=HISTORY, stream=True):
            tok = chunk.get("message", {}).get("content", "")
            if tok:
                full += tok
                yield _sse("token", {"t": tok})
        HISTORY.append({"role": "assistant", "content": full})
        # If we delegated to the Engineer for this turn, restore the assistant
        # persona prompt so the conversation returns to the primary agent.
        if delegated:
            HISTORY[0] = {"role": "system", "content": _system_content("assistant")}
        _trim_history(); conversations.save(CONV_ID, HISTORY)
        yield _sse("done", {"reply": full, "ran": ran, "delegated": delegated})

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/approve", methods=["POST"])
def approve():
    data = request.json
    name = data.get("name")
    args = data.get("args", {})
    approved = data.get("approved", False)
    ran = data.get("ran", [])

    if not approved:
        HISTORY.append({"role": "tool", "name": name,
                        "content": "User denied this action."})
        # If git transactions are on, offer to restore clean state.
        if git_txn.is_enabled(ACTIVE_PROJECT):
            git_txn.rollback(ACTIVE_PROJECT, projects.project_dir(ACTIVE_PROJECT))
            HISTORY.append({"role": "system",
                            "content": "Change denied; workspace rolled back to the last clean checkpoint."})
    else:
        # Checkpoint before a file-changing tool so we can roll back if needed.
        if git_txn.is_enabled(ACTIVE_PROJECT) and name in ("apply_patch", "write_text_file", "run_shell_command"):
            git_txn.checkpoint(ACTIVE_PROJECT, projects.project_dir(ACTIVE_PROJECT),
                               label=f"before {name}")
        result = _run_tool(name, args)
        HISTORY.append({"role": "tool", "name": name, "content": result})
        ran = ran + [name]

        # Self-validating loop: after a code change in the Engineer persona,
        # syntax-check, then run the test suite if one exists. Failures feed
        # back so the model can self-correct logical regressions (a mini CI).
        if ACTIVE_PERSONA == "engineer" and name in ("apply_patch", "write_text_file"):
            fp = args.get("filepath", "")
            if fp.endswith(".py"):
                check = actions.validate_python(fp)
                if check != "OK":
                    HISTORY.append({"role": "system", "content":
                        f"Automatic validation of {fp} FAILED:\n{check}\n"
                        "Fix it with another apply_patch."})
                else:
                    # Syntax is fine — run the project's tests if present.
                    test_out = actions.run_project_tests()
                    if test_out.startswith("TESTS FAILED"):
                        HISTORY.append({"role": "system", "content":
                            f"Syntax OK, but the test suite is failing:\n{test_out}\n"
                            "Analyze the failure and fix it with another apply_patch."})
                    elif test_out.startswith("TESTS PASSED"):
                        HISTORY.append({"role": "system", "content":
                            f"Validation passed: {fp} compiles and all tests pass."})
                    else:
                        HISTORY.append({"role": "system", "content":
                            f"{fp} compiles (no tests found to run)."})

    # Resume the autonomous loop where it paused, on the right backend.
    if PROVIDER in ("claude", "gemini"):
        return _run_cloud_loop(PROVIDER, ran_so_far=ran)
    return _run_tool_loop(ran_so_far=ran)


@app.route("/reset", methods=["POST"])
def reset():
    global HISTORY, CONV_ID, SESSION_SUMMARY
    HISTORY = HISTORY[:1]; SESSION_SUMMARY = ""  # keep only the system prompt
    CONV_ID = conversations.new_id()  # subsequent turns go to a new saved log
    return jsonify({"ok": True})


@app.route("/persona", methods=["GET", "POST"])
def persona():
    global HISTORY, ACTIVE_PERSONA, CONV_ID
    if request.method == "GET":
        return jsonify({"active": ACTIVE_PERSONA, "available": list(PERSONAS)})

    choice = request.json.get("persona", "")
    if choice not in PERSONAS:
        return jsonify({"error": f"Unknown persona: {choice}",
                        "available": list(PERSONAS)}), 400

    ACTIVE_PERSONA = choice
    # Switching persona starts a fresh conversation (mixing system prompts
    # mid-thread confuses the model). Long-term memory is unaffected — it lives
    # in ChromaDB, not in this in-memory history.
    HISTORY = [{"role": "system", "content": _system_content(choice)}]
    CONV_ID = conversations.new_id()
    return jsonify({"active": ACTIVE_PERSONA})


@app.route("/models", methods=["GET"])
def models_installed():
    """List models actually installed in Ollama, plus current assignments."""
    names = []
    try:
        # ollama.list() returns installed models; tolerate API shape differences.
        data = ollama.list()
        for m in data.get("models", []):
            n = m.get("model") or m.get("name")
            if n:
                names.append(n)
    except Exception as e:
        return jsonify({"installed": [], "error": str(e),
                        "text": TEXT_MODEL, "coding": CODING_MODEL, "vision": VISION_MODEL})
    return jsonify({"installed": sorted(names), "text": TEXT_MODEL,
                    "coding": CODING_MODEL, "vision": VISION_MODEL})


@app.route("/models/set", methods=["POST"])
def models_set():
    """Assign which installed model is used for text / coding / vision."""
    global TEXT_MODEL, CODING_MODEL, VISION_MODEL
    d = request.json or {}
    if d.get("text"):   TEXT_MODEL = d["text"]
    if d.get("coding"): CODING_MODEL = d["coding"]
    if d.get("vision"): VISION_MODEL = d["vision"]
    return jsonify({"text": TEXT_MODEL, "coding": CODING_MODEL, "vision": VISION_MODEL})


@app.route("/apikey", methods=["POST"])
def apikey_route():
    """
    Let a user supply their own API key at runtime (cloud 'bring your own key').
    Held in memory only — never written to disk. which: 'claude' or 'gemini'.
    """
    d = request.json or {}
    which = d.get("which")
    value = d.get("key", "")
    mapping = {"claude": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}
    if which not in mapping:
        return jsonify({"error": "which must be 'claude' or 'gemini'"}), 400
    providers.set_key(mapping[which], value)
    return jsonify({"ok": True, "available": providers.available()})


@app.route("/mode", methods=["GET"])
def mode_route():
    """Tell the UI whether we're running locally or in cloud mode."""
    return jsonify({
        "mode": runtime_config.mode_name(),
        "features": runtime_config.FEATURES,
        "default_provider": runtime_config.DEFAULT_PROVIDER,
        "byo_key": runtime_config.CLOUD_MODE,  # cloud users supply their own key
    })


@app.route("/mcp", methods=["GET"])
def mcp_route():
    """Show connected MCP servers and their tools."""
    return jsonify({"servers": mcp_client.status()})


@app.route("/sandbox", methods=["GET", "POST"])
def sandbox_route():
    """Toggle running shell commands inside an ephemeral Docker container."""
    if request.method == "POST":
        actions.set_sandbox_shell(bool((request.json or {}).get("enabled")))
    return jsonify({"enabled": actions._sandbox_shell})


@app.route("/rules", methods=["GET", "POST"])
def rules_route():
    """Get or set the active project's .jarvisrules (architectural invariants)."""
    global HISTORY
    if request.method == "GET":
        return jsonify({"project": ACTIVE_PROJECT,
                        "rules": projects.get_rules(ACTIVE_PROJECT)})
    text = (request.json or {}).get("rules", "")
    projects.set_rules(ACTIVE_PROJECT, text)
    # Refresh the live system prompt so rules apply immediately.
    if HISTORY:
        HISTORY[0] = {"role": "system", "content": _system_content(ACTIVE_PERSONA)}
    return jsonify({"ok": True})


@app.route("/websearch", methods=["GET", "POST"])
def websearch_route():
    """Toggle the opt-in web search tool (sends queries to the internet)."""
    if request.method == "POST":
        on = bool((request.json or {}).get("enabled"))
        actions.set_web_enabled(on)
    return jsonify({"enabled": actions._web_enabled})


@app.route("/git", methods=["GET", "POST"])
def git_route():
    """Toggle / inspect Git transactions for the active project."""
    proj_path = projects.project_dir(ACTIVE_PROJECT)
    if request.method == "GET":
        return jsonify({"project": ACTIVE_PROJECT,
                        "status": git_txn.status(ACTIVE_PROJECT, proj_path)})
    action = (request.json or {}).get("action")
    if action == "enable":
        msg = git_txn.enable(ACTIVE_PROJECT, proj_path)
    elif action == "disable":
        msg = git_txn.disable(ACTIVE_PROJECT)
    elif action == "rollback":
        msg = git_txn.rollback(ACTIVE_PROJECT, proj_path)
    else:
        return jsonify({"error": "action must be enable/disable/rollback"}), 400
    return jsonify({"message": msg,
                    "status": git_txn.status(ACTIVE_PROJECT, proj_path)})


@app.route("/provider", methods=["GET", "POST"])
def provider_route():
    """Get or set the model provider: local / claude / gemini."""
    global PROVIDER
    if request.method == "GET":
        return jsonify({"provider": PROVIDER, "available": providers.available()})
    choice = request.json.get("provider", "")
    avail = providers.available()
    if choice not in avail:
        return jsonify({"error": "unknown provider"}), 400
    if not avail[choice]:
        return jsonify({"error": f"{choice} has no API key set. "
                        f"Set the environment variable first."}), 400
    PROVIDER = choice
    return jsonify({"provider": PROVIDER, "cloud": choice != "local"})


@app.route("/profile", methods=["GET", "POST"])
def profile_route():
    """Get or update the config profile (memory tier 3)."""
    global HISTORY
    if request.method == "GET":
        return jsonify({"profile": profile.get()})
    text = request.json.get("profile", "")
    profile.set_text(text)
    # Refresh the live system prompt so the new profile applies immediately.
    if HISTORY:
        HISTORY[0] = {"role": "system", "content": _system_content(ACTIVE_PERSONA)}
    return jsonify({"ok": True})


@app.route("/model", methods=["GET", "POST"])
def model_mode():
    """Get or set the model selection mode: auto / text / vision."""
    global MODEL_MODE
    if request.method == "GET":
        return jsonify({"mode": MODEL_MODE, "text": TEXT_MODEL,
                        "vision": VISION_MODEL,
                        "would_use": _choose_model()})
    choice = request.json.get("mode", "")
    if choice not in ("auto", "text", "vision"):
        return jsonify({"error": "mode must be auto, text, or vision"}), 400
    MODEL_MODE = choice
    return jsonify({"mode": MODEL_MODE, "would_use": _choose_model()})


@app.route("/projects", methods=["GET", "POST"])
def projects_route():
    """List projects, or create/switch to one."""
    global HISTORY, CONV_ID, ACTIVE_PROJECT, SESSION_SUMMARY
    if request.method == "GET":
        return jsonify({"active": ACTIVE_PROJECT, "projects": projects.list_all()})

    data = request.json
    action = data.get("action", "switch")
    name = data.get("name", "")

    if action == "create":
        name = projects.create(name)

    # Switch active project: re-scope memory, conversations, and uploads.
    ACTIVE_PROJECT = projects._safe_name(name)
    projects.create(ACTIVE_PROJECT)
    memory.set_project(ACTIVE_PROJECT)
    code_index.set_project(ACTIVE_PROJECT)
    conversations.set_dir(projects.conversations_dir(ACTIVE_PROJECT))
    HISTORY = [{"role": "system", "content": _system_content(ACTIVE_PERSONA)}]
    SESSION_SUMMARY = ""
    CONV_ID = conversations.new_id()
    return jsonify({"active": ACTIVE_PROJECT, "projects": projects.list_all()})


@app.route("/project/<name>", methods=["GET"])
def project_detail(name):
    """Everything the project detail page needs: description, memory, files."""
    safe = projects._safe_name(name)
    projects.create(safe)
    # Read this project's memory without disturbing the active one.
    prev = memory._active_project
    memory.set_project(safe)
    mem = memory.list_all()
    memory.set_project(prev)
    meta = projects.get_meta(safe)
    return jsonify({
        "name": safe,
        "description": meta.get("description", ""),
        "memory": mem,
        "files": projects.list_files(safe),
        "has_images": projects.has_images(safe),
        "persona": ACTIVE_PERSONA,
        "personas": list(PERSONAS),
    })


@app.route("/project/<name>/description", methods=["POST"])
def project_description(name):
    desc = request.json.get("description", "")
    saved = projects.set_description(projects._safe_name(name), desc)
    return jsonify({"description": saved})


@app.route("/tools", methods=["GET"])
def tools_list():
    """Self-documenting list of every registered tool and whether it's gated."""
    out = []
    for t in TOOLS:
        fn = t["schema"]["function"]
        out.append({
            "name": fn["name"],
            "description": fn["description"],
            "destructive": t["destructive"],
        })
    return jsonify({"tools": out})


from werkzeug.utils import secure_filename


@app.route("/workspace", methods=["GET"])
def workspace_info():
    """Report the active workspace + project and a file listing, for the UI."""
    return jsonify({
        "workspace": actions.WORKSPACE,
        "project": ACTIVE_PROJECT,
        "upload_dir": projects.uploads_dir(ACTIVE_PROJECT),
        "listing": projects.list_files(ACTIVE_PROJECT),
        "has_images": projects.has_images(ACTIVE_PROJECT),
    })


@app.route("/upload", methods=["POST"])
def upload():
    """Accept uploaded files into the ACTIVE PROJECT's uploads folder."""
    if "files" not in request.files:
        return jsonify({"error": "no files"}), 400
    dest_dir = projects.uploads_dir(ACTIVE_PROJECT)
    saved, any_image = [], False
    for f in request.files.getlist("files"):
        if not f.filename:
            continue
        name = secure_filename(f.filename)
        dest = os.path.join(dest_dir, name)
        f.save(dest)
        saved.append(name)
        actions._audit("UPLOAD", dest)
        if os.path.splitext(name)[1].lower() in actions.IMAGE_EXTS:
            any_image = True
    if saved:
        note = (f"The user uploaded these files into project '{ACTIVE_PROJECT}': "
                f"{', '.join(saved)}.")
        if any_image:
            note += (" One or more are images — in auto mode the next turn will "
                     "use the vision model to look at them.")
        HISTORY.append({"role": "system", "content": note})
    return jsonify({"saved": saved, "dir": dest_dir, "has_image": any_image})


@app.route("/conversations", methods=["GET"])
def conversations_list():
    return jsonify({"conversations": conversations.list_all()})


@app.route("/conversations/<conv_id>", methods=["GET"])
def conversation_get(conv_id):
    data = conversations.load(conv_id)
    if data is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(data)


@app.route("/conversations/<conv_id>/resume", methods=["POST"])
def conversation_resume(conv_id):
    """
    Load a saved conversation back into the LIVE context so new messages
    continue the same thread. We rebuild HISTORY as system prompt + the saved
    user/assistant turns, and point CONV_ID at this file so it keeps appending.
    """
    global HISTORY, CONV_ID, SESSION_SUMMARY
    data = conversations.load(conv_id)
    if data is None:
        return jsonify({"error": "not found"}), 404
    rebuilt = [{"role": "system", "content": _system_content(ACTIVE_PERSONA)}]
    for t in data.get("turns", []):
        if t.get("role") in ("user", "assistant") and t.get("content"):
            rebuilt.append({"role": t["role"], "content": t["content"]})
    HISTORY = rebuilt
    CONV_ID = conv_id  # continue saving into the same conversation file
    _trim_history()
    return jsonify({"ok": True, "id": conv_id, "turns": data.get("turns", [])})


@app.route("/memory", methods=["GET"])
def memory_list():
    return jsonify({"memories": memory.list_all()})


@app.route("/memory/clear", methods=["POST"])
def memory_clear():
    return jsonify({"result": memory.forget_all()})


if __name__ == "__main__":
    # In cloud mode (e.g. Railway), bind to 0.0.0.0 and the platform's $PORT.
    # Locally, stay on localhost:5000 for privacy.
    port = int(os.environ.get("PORT", "5000"))
    if runtime_config.CLOUD_MODE:
        print(f"Jarvis running in CLOUD mode on 0.0.0.0:{port}")
        app.run(host="0.0.0.0", port=port, debug=False)
    else:
        print(f"Open http://127.0.0.1:{port} in your browser")
        app.run(host="127.0.0.1", port=port, debug=False)
