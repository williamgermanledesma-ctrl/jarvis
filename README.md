# Local AI Assistant — Mac / Apple Silicon

A personal "Jarvis": a local LLM (via Ollama) that calls your Python functions,
remembers things across conversations, and can run truly hands-free with a
"Hey Jarvis" wake word. Everything runs locally — nothing leaves your machine.

| Phase | What it adds | Status |
|-------|-------------|--------|
| 1 | Reasoning brain + tools + approval gate (terminal) | ✅ `main.py` |
| 2 | Browser UI + voice (type or speak) | ✅ `server.py` |
| 3 | Long-term memory (vector database) | ✅ `memory.py` |
| 4 | Hands-free "Hey Jarvis" wake word | ✅ `wake_listener.py` (optional) |

---

## Folder structure

```
jarvis/
├── main.py              # Terminal version (Phase 1)
├── server.py            # Web server — UI + voice + memory (run this)
├── memory.py            # Long-term memory: ChromaDB + Ollama embeddings
├── wake_listener.py     # OPTIONAL hands-free "Hey Jarvis" listener
├── registry.py          # Lists every tool, flags destructive ones
├── requirements.txt     # Dependencies (core + optional voice)
├── README.md
├── templates/
│   └── index.html       # Browser UI
├── tools/
│   ├── __init__.py
│   └── actions.py       # The functions the AI can call
└── memory_store/        # (created automatically — your saved memories)
```

## What the assistant can do (tools)

| Tool | Does | Approval? |
|------|------|-----------|
| `get_disk_usage`   | Check free disk space        | No |
| `list_files`       | List a folder                | No |
| `read_text_file`   | Read a text file             | No |
| `run_safe_command` | Allow-listed read-only cmds  | No |
| `remember`         | Save a fact to memory        | No |
| `recall`           | Search memory                | No |
| `write_text_file`  | Create/overwrite a file      | **Yes** |
| `run_shell_command`| Run any terminal command     | **Yes** |

---

## Setup

### 1. Install Ollama and pull models
From https://ollama.com/download, then in Terminal:
```bash
ollama pull llama3.1:8b        # the brain
ollama pull nomic-embed-text   # for memory embeddings (Phase 3)
```

### 2. Python environment + core deps
```bash
cd ~/jarvis
python3 -m venv venv
source venv/bin/activate
pip install ollama flask chromadb
```
That's everything for Phases 1–3. (The wake word needs more — see below.)

---

## Run the web UI (Phases 1–3)
```bash
source venv/bin/activate
python server.py
```
Open **http://127.0.0.1:5000** in Chrome or Safari.

### Hands-free mode (in the browser — easiest)
Click **🎧 hands-free** in the header. The browser then listens continuously:
you speak, it sends your words to the assistant, speaks the reply aloud, and
automatically goes back to listening — no clicking, no wake word needed. Click
the button again to stop.

This is the simplest path to a hands-free experience and needs nothing extra
installed (it uses the browser's built-in speech, same as the 🎙 button).
Destructive actions still pause for an on-screen click — they're never
auto-approved by voice. Works in Chrome or Safari.

> Note: the browser can't do a true always-on "Hey Jarvis" wake word — that's
> why the optional `wake_listener.py` exists. But hands-free mode covers most
> of the same need without the install friction.

### Memory in action
- Tell it something: *"Remember that my main project is called Helios."*
  → it calls `remember` and saves it.
- Later, in a fresh session: *"What's my project called?"*
  → relevant memories are pulled in automatically, so it knows.
- View everything saved: visit **http://127.0.0.1:5000/memory**.

Memories persist on disk in `memory_store/`, so they survive restarts.

---

## Hands-free "Hey Jarvis" (Phase 4 — optional)

This runs as a SEPARATE script. Your UI works with or without it. Because there's
no screen, the listener **auto-denies destructive actions** by default — approve
those in the web UI instead. (Toggle `AUTO_DENY` in `wake_listener.py`.)

### Extra install (this is the heavy part)
```bash
source venv/bin/activate
pip install openwakeword sounddevice numpy requests openai-whisper
brew install portaudio ffmpeg
```
If you don't have Homebrew, get it at https://brew.sh first. The first run
downloads the wake-word and Whisper models (a few hundred MB).

### Run it (two Terminal tabs)
```bash
# Tab 1 — the server must be running:
python server.py

# Tab 2 — the listener:
python wake_listener.py
```
Say **"Hey Jarvis"**, wait for "Yes?", then speak your command. It transcribes
with Whisper, sends it to the server, and speaks the reply back via macOS `say`.

The first time, macOS will ask for microphone permission for your Terminal —
allow it (System Settings → Privacy & Security → Microphone).

---

## Still prefer terminal-only?
`python main.py` runs the Phase 1 command-line version.

## Advanced (latest additions)
- **Ephemeral deployment testing** (`deploy_test`, needs Docker): instead of
  just checking syntax, Jarvis can copy the project into a throwaway container,
  apply init steps (e.g. rename `.env.example` → `.env`), install deps, start the
  backend, and run integration tests against the running app — then tear it all
  down. A real pre-approval smoke test, fully isolated from your host.
- **MCP client (external tool servers)**: Jarvis speaks the Model Context
  Protocol, so you can plug in pre-built tool servers instead of hand-writing
  every tool. Copy `mcp_servers.example.json` to `mcp_servers.json` and list your
  servers — **both config formats work**: the ecosystem-standard `mcpServers`
  object (copy-paste from Claude Desktop or anywhere) and the legacy `servers`
  list. Their tools appear automatically (namespaced `mcp__server__tool`) in the
  agentic loop, and connected servers show in the Tools drawer. The client is
  resilient: each server runs on its own reader thread so a slow server never
  blocks Jarvis, stderr is logged for debugging, and MCP tools that look like
  they mutate state (write/delete/execute/…) are auto-gated behind the approval
  prompt. Minimal-but-real (initialize / tools/list / tools/call over stdio).
  Example servers in the template: sqlite, fetch, filesystem.
- **Auto-delegation to the Engineer**: in the general Assistant persona, when
  Jarvis detects a coding request (local provider), it automatically delegates
  that turn to the Engineer sub-agent — swapping in the engineer prompt and your
  coding model — and shows "delegating to the Engineer…" so you see it happen.
  It returns to the primary assistant afterward. No more manual dropdown flips.
- **Project rules (`.jarvisrules`)**: each project can hold a rules file of
  architectural invariants (e.g. "always rename `.env.example` to `.env` before
  running") that is always injected into the system prompt, so the model abides
  by your setup constraints at every step. Edit it in the Tools drawer.
- **Generic linter** (`run_linter`): runs a language-appropriate check —
  flake8/pyflakes for Python, eslint for JS/TS, rustc for Rust, gofmt for Go.
  (Pine Script has no offline linter — it compiles only in TradingView — so
  Jarvis says so honestly rather than pretending to verify it.)
- **Sandboxed shell** (Tools drawer, opt-in): routes `run_shell_command` through
  an ephemeral `docker run --rm --network none` container, so even a destructive
  script can't touch your host. Requires Docker running; off by default.
- **Runtime CI (real tests)**: after you approve a Python change in the Engineer
  persona, Jarvis syntax-checks it *and* runs the project's test suite (pytest)
  if one exists. Failing tests feed back so the model fixes its own logical
  regressions before asking for final sign-off — not just "does it parse" but
  "does it work."
- **Git safety net** (Tools drawer → enable per project): Jarvis works on a
  throwaway `jarvis/session-…` branch and checkpoints before each edit. Reject a
  change, or hit "Roll back to clean," and the workspace is restored to its last
  good state with `git reset --hard` — failed multi-file edits can't leave you
  in a broken state. It never touches remotes or your real branches.
- **Web search** (Tools drawer → enable; **off by default**): gives the frozen
  local model an on-demand window to the live web (DuckDuckGo, no API key) for
  current API docs or unfamiliar error messages. ⚠ Enabling it sends queries to
  the internet — it's opt-in and clearly marked, like the cloud providers.
- **Smart context compression**: long sessions no longer just slice off old
  messages. When history fills, Jarvis compresses the oldest turns into a running
  "session summary" kept near the system prompt — so early decisions and
  constraints survive indefinitely while the context stays lean.
- **Surgical edits (`apply_patch`)**: instead of rewriting whole files, the
  assistant replaces an exact block of text — far fewer tokens, and small models
  keep structural coherence. It verifies the target block appears exactly once
  before editing.
- **Visual diff approval**: when a file change pauses for approval, the card now
  shows a color-coded unified diff (red deletions, green insertions) so you see
  exactly what will change before granting write access — computed server-side
  with Python's `difflib`.
- **Self-validating edits**: after you approve a Python file change in the
  Engineer persona, Jarvis automatically syntax-checks it; if it's broken, the
  error is fed back and the model patches again — a mini CI loop that means you
  only step in once the code at least parses.
- **Semantic code search (AST + vector)**: `index_codebase` parses your Python
  into symbols (functions, classes, signatures, docstrings) and stores them in
  the project's vector DB; `search_code` then answers questions like "where is
  auth handled?" by pulling only the relevant symbols into context — instead of
  dumping whole files and saturating the context window.
- **Pick any local model** (🧩 **Models** in the sidebar): Jarvis lists the
  models you've actually pulled in Ollama and lets you assign which one handles
  **text**, **coding** (used automatically on the Engineer persona), and
  **vision**. Pull a strong coding model — e.g. `ollama pull qwen2.5-coder` —
  then select it here; no code edits, no guessing at version names. The Engineer
  persona then routes its turns to your chosen coding model.
- **Whole-codebase context** (`scan_codebase` tool): before editing a multi-file
  project, the assistant can scan it — building a file tree plus previews of each
  code file — so it understands structure first (the Windsurf-style approach).
  It's workspace-confined and skips noise dirs (node_modules, .git, venv).
- **Better tool use & responses**: small local models sometimes *printed* a tool
  call as raw JSON (`{"name": ...}`) instead of running it. Jarvis now detects
  that and executes the real tool, and the personas steer toward warmer, clearer,
  Claude-like prose instead of robotic narration.
- **Multiple providers** (top-bar dropdown): route a turn to **🔒 Local**
  (Ollama, private, default), **☁ Claude**, or **☁ Gemini**. Cloud options need
  an API key set as an environment variable, and they send that turn's messages
  off your machine — Jarvis warns you when you switch and marks cloud turns.
  ```bash
  export ANTHROPIC_API_KEY=sk-ant-...   # enables Claude
  export GEMINI_API_KEY=...             # enables Gemini (generous free tier)
  python server.py
  ```
  Providers without a key show as disabled in the dropdown. The autonomous tool
  loop runs on **Local**; cloud providers answer in text (great for quality on
  hard reasoning/writing, where the 8B local model struggles).
- **Claude & Gemini with tools (agentic cloud loop)**: when the provider is
  **☁ Claude** or **☁ Gemini**, Jarvis runs the *full* Write-Test-Fix tool loop
  with that model's reasoning — it chains tools (list files, run tests, write
  code) and pauses destructive steps for your approval, just like Local but with
  stronger reasoning. Every step goes to the cloud; tool traces show ☁.
- **API keys via `.env`**: copy `.env.example` to `.env` and paste your keys —
  they load automatically at startup, no shell exports needed:
  ```bash
  cp .env.example .env
  # then edit .env:
  #   ANTHROPIC_API_KEY=sk-ant-...
  #   GEMINI_API_KEY=...
  ```
  The `.env` file is gitignored so your keys never get committed. Shell-exported
  variables still work and take precedence if both are set.
- **Resumable chats**: click any chat under **Recents** to load it back into the
  live session and continue the thread — not just view it. New messages append
  to the same saved conversation.
- **Streaming replies**: answers stream in token-by-token (you watch Jarvis
  "type"), with live `running <tool>…` events as it works. Uses Server-Sent
  Events from Flask — same stack, no rebuild.
- **Config profile** (⚙ **Profile** in the sidebar): a permanent note about your
  environment — machine, Python version, preferred packages, conventions — that
  is *always* injected into context. This is the third memory tier, alongside
  ephemeral history and per-project semantic memory. Stored in
  `config_profile.txt`; edit it in the UI or directly.
- **Smart voice (wake_listener.py)**: with `torch` installed, the voice loop
  uses **Silero VAD** so a mid-sentence pause (under ~700ms, tunable via
  `MIN_TURN_SILENCE_MS`) won't cut you off, and **barge-in** — if you start
  talking while Jarvis is speaking, it stops instantly. Falls back to fixed-
  length recording if torch isn't present.

## Projects & model switching
- **Projects** (📦 dropdown + ＋ button): each project is an isolated workspace
  with its **own uploaded files, saved chats, and memory**. Switching projects
  gives you a clean context; nothing leaks between them. Projects live in
  `<workspace>/projects/<name>/`. Create one with ＋.
- **Model switching** (🧠 dropdown): **auto** uses the fast text model
  (`llama3.1:8b`) for normal turns and automatically switches to the **vision**
  model (`llava`) when the project has images or you just uploaded one. **text**
  and **vision** force one regardless.
  - Pull the vision model once to enable it:  `ollama pull llava`
  - On vision turns the assistant answers about the image **directly** and does
    not chain tools, because `llava` is much weaker at tool-calling than
    `llama3.1`. That's an intentional tradeoff: reliable image answers over
    flaky tool use.
  - Vision models are larger and slower, so auto-mode keeps the fast text model
    as the default and only reaches for `llava` when an image is actually present.

## Personas, tools, history, and saved chats
- **Autonomous tool chaining**: the assistant can now run several tools in one
  turn — e.g. list files, then read one, then run a command — deciding each step
  from the last result, up to 5 steps (`MAX_TOOL_STEPS` in `server.py`). If a
  step is destructive, the chain pauses for your approval and resumes after you
  click, so autonomy never bypasses the safety gate.
- **File uploads** (📎 button): upload text files or images into the workspace
  `uploads/` folder. The assistant is told what arrived and can inspect text
  files. **Images are stored but not *seen*** — `llama3.1:8b` is text-only. To
  analyze image contents, switch `MODEL` to a vision model like `llava`; the
  upload/storage path is already in place, so it's a one-line change.
- **Workspace indicator**: the status bar shows where files are confined (📂).
- **Persona switch** (header dropdown): flip between **⚙ engineer** (the
  Write-Test-Fix coding agent) and **💬 assistant** (general helper). Switching
  starts a fresh chat; your long-term memory is kept.
- **🛠 tools button**: lists every tool the assistant can use and flags which
  ones need approval — so it's self-documenting as you add more.
- **History trimming**: only the most recent ~30 messages stay in context, so
  long sessions won't overflow the model. Adjust `MAX_HISTORY_MESSAGES` in
  `server.py`.
- **Saved conversations**: every chat is written to `conversations/` as JSON and
  survives restarts. Browse them at `http://127.0.0.1:5000/conversations`, or a
  single one at `/conversations/<id>`. (This is separate from memory, which
  stores *facts*; this stores the raw chat log.)
- **⏹ stop button**: cuts off spoken replies immediately.

## Safety hardening (important)
This assistant can run shell commands and write files, so a few guardrails sit
on top of the approval gate:
- **Command denylist**: shell commands matching dangerous patterns (`rm -rf`,
  `sudo`, `curl | sh`, `shutdown`, raw disk writes, etc.) are auto-rejected
  before they even reach the approval prompt.
- **Workspace confinement**: `read_text_file` and `write_text_file` are limited
  to your home directory by default. Lock it tighter by setting an environment
  variable before launching:
  ```bash
  export JARVIS_WORKSPACE=~/jarvis_workspace
  python server.py
  ```
- **Audit log**: every shell command and file write (and every blocked attempt)
  is appended to `jarvis_audit.log` with a timestamp, so you have a record of
  everything the assistant did.

The approval gate is still your primary protection — read each proposed command
before approving. These layers just catch the obvious-bad cases automatically.

## Adding a new capability
1. Write a function in `tools/actions.py`.
2. Register it in `registry.py` (set `destructive` true/false).
3. Restart `server.py`.

---

## Troubleshooting
- **Memory errors / "model not found"** — run `ollama pull nomic-embed-text`.
- **`ModuleNotFoundError: chromadb`** — `pip install chromadb` inside the venv.
- **Wake word: "Missing a dependency"** — install the Phase 4 packages and
  `brew install portaudio ffmpeg`.
- **Wake word never triggers** — lower `WAKE_THRESHOLD` in `wake_listener.py`
  (e.g. 0.4); triggers too often — raise it. Check Terminal's mic permission.
- **Whisper is slow** — `base.en` is the balance; `tiny.en` is faster/less
  accurate. Change in `wake_listener.py`.
- **Can't reach Ollama** — the Ollama app must be running (menu-bar icon).

## Safety reminder
`run_shell_command` can run any command the model proposes. The approval gate is
your protection — read each command before clicking Approve, and don't approve
reflexively. The wake-word listener auto-denies these by design.
# jarvis
