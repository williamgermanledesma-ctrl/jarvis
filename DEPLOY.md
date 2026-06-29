# Deploying Jarvis

Jarvis was built **local-first**. It can also run in a stripped **cloud mode**
for sharing with others. This guide covers both, honestly — including what
*doesn't* carry over to the cloud and what true multi-user would still require.

---

## Phase 1 — Git & GitHub (do this regardless)

From the project root:

```bash
git init
git add .
git commit -m "Initial commit: local-first Jarvis agent framework"
```

Create an empty repo on GitHub (no README/license, to avoid conflicts), then:

```bash
git remote add origin https://github.com/<you>/jarvis.git
git branch -M main
git push -u origin main
```

**Already protected by `.gitignore`:** `.env`, `mcp_servers.json` (may hold
tokens), `memory_store/`, `conversations/`, `projects/`, `uploads/`,
`config_profile.txt`, `jarvis_audit.log`, `venv/`. Double-check before your
first push that none of these show up in `git status`.

---

## Phase 2 — Cloud mode (deploy so others can try it)

### What cloud mode is

Setting `JARVIS_CLOUD_MODE=1` flips Jarvis into a container-friendly profile:

| Feature | Local | Cloud |
|---|---|---|
| Ollama local models | ✅ | ❌ (no Ollama in a container) |
| Claude / Gemini | ✅ | ✅ (the only option in cloud) |
| MCP subprocess servers | ✅ | ❌ (can't spawn npx/uvx) |
| Docker sandbox / deploy_test | ✅ | ❌ (no Docker-in-Docker) |
| Voice / wake word | ✅ | ❌ (no mic on a server) |
| Surgical patching, diffs, memory, projects | ✅ | ✅ |

Cloud mode defaults the provider to **Claude** and uses **bring-your-own-key**:
each user pastes their own API key in the UI (held in memory, never saved), so
you don't pay for everyone's usage.

### Deploy to Railway

1. Push to GitHub (Phase 1).
2. On Railway: **New Project → Deploy from GitHub repo** → pick your repo.
3. Railway detects the `Dockerfile` automatically. (The `JARVIS_CLOUD_MODE=1`
   env var is baked into the Dockerfile; you can also set it in Railway's
   Variables tab.)
4. Railway provides `$PORT` automatically — the app already binds to it.
5. Deploy. Open the generated URL; you'll see the cloud banner asking for a key.

### Persistence (important)

Railway's container filesystem is **ephemeral** — it resets on every redeploy,
wiping conversations/projects/memory. To keep data, attach a **Volume** in
Railway and mount it where Jarvis writes (e.g. `/app/memory_store`,
`/app/conversations`, `/app/projects`). Without a volume, treat the deployment
as a stateless demo.

### Files added for deployment

- `Dockerfile` — cloud image (Python + gunicorn, no Node/Docker).
- `railway.json` — Railway build/start config.
- `Procfile` — for platforms that use it (Render, Heroku-likes).
- `runtime_config.py` — the local-vs-cloud switch.

---

## Phase 3 — True multi-user (scoped, NOT yet built)

Phase 2 gets Jarvis *deployed and usable*, but it's still fundamentally a
**single-state app**: there is one global `HISTORY`, one `ACTIVE_PROJECT`, one
`PROVIDER`. If two people use the Phase-2 deployment at the same time, they
share that state and will clobber each other. For a real multi-user service,
here's what's required — so you can decide if/when it's worth it:

### 3a. Per-session state isolation (the core refactor)
- Replace module-level globals (`HISTORY`, `CONV_ID`, `ACTIVE_PROJECT`,
  `PROVIDER`, `SESSION_SUMMARY`, key overrides) with a per-session store keyed
  by a session/user id (cookie or token).
- Every route that reads/writes those globals must instead read/write the
  caller's session object. This touches most of `server.py`.
- **Effort: large.** This is the real work and the main reason multi-user isn't
  a flag-flip.

### 3b. Authentication
- At minimum, a login (even a shared password or magic-link) so sessions map to
  people and one user can't read another's conversations.
- Session cookies signed with a secret; HTTPS (Railway gives you this).

### 3c. Per-user storage
- ChromaDB collections, conversations, and projects namespaced per user id, not
  just per project. A volume (or external Postgres/object store) for durability.

### 3d. Abuse & cost controls
- Even with bring-your-own-key, rate-limit requests per session.
- Cap upload sizes; sandbox or disable any tool that touches the host.

### 3e. Concurrency
- Multiple gunicorn workers won't share in-memory state — once state is
  per-session and stored externally (e.g. Redis/Postgres), you can scale workers.
  Until then, keep `--workers 1`.

**Recommendation:** ship Phase 2 first, see whether people actually use it, and
only then invest in 3a. The refactor is far easier to scope against a live app
with real usage than designed up front.
