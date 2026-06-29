"""
projects.py
-----------
Projects organize work into isolated folders under the workspace. Each project
has its own folder (with an uploads/ subdir), its own saved conversations, and
its own memory collection (scoped in memory.py).

Layout:
    <workspace>/projects/<project_name>/
        uploads/        <- files uploaded while in this project
        conversations/  <- saved chat logs for this project
"""

import os
import re
import json
import glob
import datetime
from tools import actions

PROJECTS_ROOT = os.path.join(actions.WORKSPACE, "projects")
os.makedirs(PROJECTS_ROOT, exist_ok=True)

DEFAULT_PROJECT = "default"


def _safe_name(name: str):
    """Sanitize a project name into a safe folder name."""
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", (name or "").strip())
    safe = safe.strip("_-")
    return safe or DEFAULT_PROJECT


def project_dir(name: str):
    return os.path.join(PROJECTS_ROOT, _safe_name(name))


def uploads_dir(name: str):
    d = os.path.join(project_dir(name), "uploads")
    os.makedirs(d, exist_ok=True)
    return d


def conversations_dir(name: str):
    d = os.path.join(project_dir(name), "conversations")
    os.makedirs(d, exist_ok=True)
    return d


def create(name: str):
    """Create a new project (folders). Returns the sanitized name."""
    safe = _safe_name(name)
    os.makedirs(project_dir(safe), exist_ok=True)
    uploads_dir(safe)
    conversations_dir(safe)
    return safe


def list_all():
    """List existing projects with basic stats, default first."""
    create(DEFAULT_PROJECT)  # ensure default always exists
    out = []
    for path in sorted(glob.glob(os.path.join(PROJECTS_ROOT, "*"))):
        if not os.path.isdir(path):
            continue
        name = os.path.basename(path)
        up = os.path.join(path, "uploads")
        convs = os.path.join(path, "conversations")
        n_files = len(os.listdir(up)) if os.path.isdir(up) else 0
        n_convs = len(glob.glob(os.path.join(convs, "*.json")))
        out.append({"name": name, "files": n_files, "conversations": n_convs})
    # default first, then alphabetical
    out.sort(key=lambda p: (p["name"] != DEFAULT_PROJECT, p["name"]))
    return out


def has_images(name: str):
    """True if the project's uploads contain any image files."""
    up = uploads_dir(name)
    for f in os.listdir(up):
        if os.path.splitext(f)[1].lower() in actions.IMAGE_EXTS:
            return True
    return False


def list_files(name: str):
    """Human-readable listing of a project's uploaded files."""
    up = uploads_dir(name)
    entries = sorted(os.listdir(up))
    if not entries:
        return "(no files yet)"
    return "\n".join(entries)


def _meta_path(name: str):
    return os.path.join(project_dir(name), "project.json")


def get_meta(name: str):
    """Read a project's metadata (description, created). Returns a dict."""
    try:
        with open(_meta_path(name), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"description": "", "created": ""}


def set_description(name: str, description: str):
    """Save a short description for a project."""
    meta = get_meta(name)
    meta["description"] = description.strip()
    if not meta.get("created"):
        meta["created"] = datetime.datetime.now().isoformat(timespec="seconds")
    try:
        with open(_meta_path(name), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
    except Exception:
        pass
    return meta["description"]


def _rules_path(name: str):
    return os.path.join(project_dir(name), ".jarvisrules")


def get_rules(name: str):
    """Read a project's .jarvisrules (architectural invariants), or ''."""
    try:
        with open(_rules_path(name), "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def set_rules(name: str, text: str):
    """Save a project's .jarvisrules file."""
    try:
        with open(_rules_path(name), "w", encoding="utf-8") as f:
            f.write(text)
        return True
    except Exception:
        return False
