# Jarvis — cloud deployment image (Railway, Render, Fly, etc.)
# This runs Jarvis in CLOUD mode: cloud providers only (Claude/Gemini),
# no Ollama, no Docker-in-Docker, no local MCP subprocesses.
FROM python:3.12-slim

WORKDIR /app

# System deps kept minimal — no Node/Docker needed in cloud mode.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better layer caching).
# Use the SLIM cloud requirements — no Ollama, no voice stack (those break the
# container build and aren't used in cloud mode).
COPY requirements-cloud.txt .
RUN pip install --no-cache-dir -r requirements-cloud.txt

# Copy the app.
COPY . .

# Cloud mode + Railway provides $PORT at runtime.
ENV JARVIS_CLOUD_MODE=1
ENV PORT=5000
EXPOSE 5000

# Launch gunicorn — it auto-reads gunicorn.conf.py, which binds $PORT in Python.
# No $PORT in the command, so shell-expansion issues can't occur.
CMD ["gunicorn", "server:app"]
