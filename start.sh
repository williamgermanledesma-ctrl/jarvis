#!/usr/bin/env bash
# Gunicorn reads gunicorn.conf.py, which binds $PORT in Python (no shell
# expansion needed). This script is just a convenience entrypoint.
exec gunicorn server:app
