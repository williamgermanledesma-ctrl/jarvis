#!/usr/bin/env bash
# Railway/most PaaS provide $PORT at runtime. Default to 5000 if it's unset.
# Using bash parameter expansion guarantees a real number reaches gunicorn,
# avoiding the "'$PORT' is not a valid port number" crash that happens when a
# start command isn't run through a shell.
PORT="${PORT:-5000}"
exec gunicorn --bind "0.0.0.0:${PORT}" --workers 1 --threads 4 --timeout 180 server:app
