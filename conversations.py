"""
conversations.py
----------------
Persists chat conversations to disk as JSON so they survive restarts. This is
separate from memory.py: memory stores *facts* for semantic recall, while this
stores the *raw chat logs* so you can scroll back through past sessions.

Each conversation is one JSON file in ./conversations/, named by timestamp.
"""

import os
import json
import glob
import datetime
import uuid

CONV_DIR = os.path.join(os.path.dirname(__file__), "conversations")
os.makedirs(CONV_DIR, exist_ok=True)


def set_dir(path: str):
    """Point conversation storage at a specific folder (e.g. a project's)."""
    global CONV_DIR
    CONV_DIR = path
    os.makedirs(CONV_DIR, exist_ok=True)


def _path_for(conv_id: str):
    return os.path.join(CONV_DIR, f"{conv_id}.json")


def new_id():
    """Generate a fresh, unique conversation id (timestamp + short random suffix)."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:4]}"


def save(conv_id: str, history: list):
    """
    Save a conversation. We store only user/assistant text turns (not system
    prompts or raw tool payloads) so the saved log stays readable.
    """
    turns = []
    for m in history:
        role = m.get("role")
        if role in ("user", "assistant"):
            content = m.get("content") or ""
            if content.strip():
                turns.append({"role": role, "content": content})
    if not turns:
        return  # nothing worth saving yet
    data = {
        "id": conv_id,
        "updated": datetime.datetime.now().isoformat(timespec="seconds"),
        "turns": turns,
    }
    try:
        with open(_path_for(conv_id), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def load(conv_id: str):
    """Load a single conversation by id, or None if missing."""
    try:
        with open(_path_for(conv_id), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def list_all():
    """Return summaries of saved conversations, newest first."""
    out = []
    for path in sorted(glob.glob(os.path.join(CONV_DIR, "*.json")), reverse=True):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            turns = data.get("turns", [])
            first_user = next((t["content"] for t in turns if t["role"] == "user"), "")
            preview = (first_user[:60] + "…") if len(first_user) > 60 else first_user
            out.append({
                "id": data.get("id"),
                "updated": data.get("updated"),
                "turns": len(turns),
                "preview": preview or "(empty)",
            })
        except Exception:
            continue
    return out


def delete(conv_id: str):
    """Delete a saved conversation. Returns True if removed."""
    try:
        os.remove(_path_for(conv_id))
        return True
    except Exception:
        return False
