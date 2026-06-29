"""
runtime_config.py
-----------------
Central switch for how Jarvis is running:

  - LOCAL mode (default): full power on your own machine — Ollama models, MCP
    subprocess servers, Docker sandbox, voice, local filesystem tools.
  - CLOUD mode: deployed on a host like Railway. No Ollama, no Docker, no local
    subprocess MCP, no microphone. Defaults to a cloud provider (Claude/Gemini)
    and gracefully disables the features that can't work in a container.

Set CLOUD mode by exporting an env var before launch:
    JARVIS_CLOUD_MODE=1

In cloud mode, "bring your own key" is the model: each user supplies their own
Claude or Gemini API key from the UI; the server holds no keys by default.
"""

import os

CLOUD_MODE = os.environ.get("JARVIS_CLOUD_MODE", "").lower() in ("1", "true", "yes")

# Feature flags derived from the mode. Local-only features are off in the cloud.
FEATURES = {
    "ollama_local":   not CLOUD_MODE,   # local models
    "mcp_subprocess": not CLOUD_MODE,   # npx/uvx MCP servers spawned as processes
    "docker_sandbox": not CLOUD_MODE,   # run_shell_command in Docker, deploy_test
    "voice":          not CLOUD_MODE,   # wake word / mic (browser voice still works client-side)
    "wake_word":      not CLOUD_MODE,
}

# In cloud mode, default to a cloud provider instead of local Ollama.
DEFAULT_PROVIDER = "claude" if CLOUD_MODE else "local"


def enabled(feature):
    return FEATURES.get(feature, False)


def mode_name():
    return "cloud" if CLOUD_MODE else "local"
