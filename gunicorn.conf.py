"""
gunicorn.conf.py
----------------
Gunicorn reads this automatically when you run `gunicorn server:app`. By binding
the port HERE in Python, we sidestep the entire class of "'$PORT' is not a valid
port number" errors — those happen when a start command's $PORT isn't expanded
by a shell. os.environ always works, no shell needed.
"""

import os

# Railway (and most PaaS) inject $PORT. Default to 5000 locally.
_port = os.environ.get("PORT", "5000")
# Guard against an unexpanded or junk value.
if not _port.isdigit():
    _port = "5000"

bind = f"0.0.0.0:{_port}"
workers = 1          # single worker keeps in-memory state coherent (Phase 2)
threads = 4
timeout = 180
