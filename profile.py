"""
profile.py
----------
The Configuration Profile: a permanent, static description of your environment
that Jarvis should always know — system architecture, preferred package
versions, local quirks, conventions. This is memory tier 3.

The three memory tiers in this project:
  1. Ephemeral  — recent conversation turns (server.py trims HISTORY)
  2. Semantic   — per-project facts in ChromaDB (memory.py), queried by relevance
  3. Config     — THIS: a small static profile, always injected, never queried

Stored as a plain text file you can edit directly or via the UI.
"""

import os

PROFILE_PATH = os.path.join(os.path.dirname(__file__), "config_profile.txt")

DEFAULT_PROFILE = """\
# Jarvis Configuration Profile
# Edit this with your real environment details. It's always available to Jarvis.

Machine: Mac (Apple Silicon)
Python: 3.12 in a venv at ~/jarvis/venv
Local model runtime: Ollama
Preferred models: llama3.1:8b (text), llava (vision), nomic-embed-text (memory)
Shell: zsh
Conventions:
- Prefer complete, runnable code over fragments.
- Confirm before any destructive action.
- Keep file operations inside the workspace.
"""


def get():
    """Return the profile text, creating it with defaults on first run."""
    if not os.path.exists(PROFILE_PATH):
        try:
            with open(PROFILE_PATH, "w", encoding="utf-8") as f:
                f.write(DEFAULT_PROFILE)
        except Exception:
            return DEFAULT_PROFILE
    try:
        with open(PROFILE_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return DEFAULT_PROFILE


def set_text(text: str):
    """Overwrite the profile."""
    try:
        with open(PROFILE_PATH, "w", encoding="utf-8") as f:
            f.write(text)
        return True
    except Exception:
        return False
