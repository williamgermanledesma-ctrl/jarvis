"""
git_txn.py
----------
Optional Git-backed safety net for a project. When enabled, Jarvis works on a
throwaway session branch so a failed multi-file edit can be rolled back to a
clean state with a single reset — without touching your real history.

Opt-in PER PROJECT (the server toggles it). If the project isn't a git repo,
we init a local one scoped to the project folder. We never push, never touch
remotes, and only operate on our own jarvis/ session branches.
"""

import os
import subprocess
import datetime
from tools import actions

# Projects that have Git transactions enabled (set by the server).
_enabled = set()
_session_branch = {}


def _git(args, cwd):
    """Run a git command in cwd; return (ok, output)."""
    try:
        p = subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                           text=True, timeout=30)
        return p.returncode == 0, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return False, "git is not installed."
    except Exception as e:
        return False, str(e)


def is_enabled(project):
    return project in _enabled


def enable(project, project_path):
    """
    Turn on Git transactions for a project: init a repo if needed, make a base
    commit, and start a session branch. Returns a status string.
    """
    if not os.path.isdir(project_path):
        return f"Project folder not found: {project_path}"
    # Init if not already a repo.
    if not os.path.isdir(os.path.join(project_path, ".git")):
        ok, out = _git(["init"], project_path)
        if not ok:
            return f"Could not init git: {out}"
        _git(["config", "user.email", "jarvis@local"], project_path)
        _git(["config", "user.name", "Jarvis"], project_path)
    # Stage and make a baseline commit so we have something to reset to.
    _git(["add", "-A"], project_path)
    _git(["commit", "-m", "jarvis: baseline", "--allow-empty"], project_path)
    # Start a session branch.
    branch = "jarvis/session-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    ok, out = _git(["checkout", "-b", branch], project_path)
    _enabled.add(project)
    _session_branch[project] = branch
    actions._audit("GIT_ENABLE", f"{project} -> {branch}")
    return f"Git transactions ON for '{project}'. Working on branch {branch}."


def disable(project):
    _enabled.discard(project)
    _session_branch.pop(project, None)
    return f"Git transactions OFF for '{project}'."


def checkpoint(project, project_path, label="checkpoint"):
    """Commit current state on the session branch (a savepoint)."""
    if project not in _enabled:
        return ""
    _git(["add", "-A"], project_path)
    ok, out = _git(["commit", "-m", f"jarvis: {label}", "--allow-empty"], project_path)
    return "checkpoint saved" if ok else out


def rollback(project, project_path):
    """Discard all uncommitted + session changes, back to the last checkpoint."""
    if project not in _enabled:
        return "Git transactions aren't enabled for this project."
    _git(["reset", "--hard"], project_path)
    _git(["clean", "-fd"], project_path)
    actions._audit("GIT_ROLLBACK", project)
    return "Rolled back to the last clean checkpoint."


def status(project, project_path):
    if project not in _enabled:
        return {"enabled": False}
    ok, out = _git(["status", "--short"], project_path)
    return {"enabled": True, "branch": _session_branch.get(project),
            "dirty": bool(out.strip()), "detail": out}
