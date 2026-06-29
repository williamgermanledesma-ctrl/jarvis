"""
memory.py
---------
Long-term memory using ChromaDB with local Ollama embeddings. Nothing leaves
your machine.

Memory is now PROJECT-SCOPED: each project has its own Chroma collection, so
facts saved while working on project A don't leak into project B. Switching the
active project (set_project) changes which collection remember/recall use.

One-time model pull required:
    ollama pull nomic-embed-text
"""

import re
try:
    import ollama
except ImportError:
    ollama = None  # cloud mode fallback
import chromadb

EMBED_MODEL = "nomic-embed-text"
STORE_DIR = "./memory_store"

_client = chromadb.PersistentClient(path=STORE_DIR)

# Chroma collection names must be 3-63 chars, alphanumeric/_/-, so we sanitize
# the project name into a safe collection name.
def _collection_name(project: str):
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", project or "default")
    safe = safe.strip("_-") or "default"
    return f"mem_{safe}"[:63]


_active_project = "default"
_collection = _client.get_or_create_collection(name=_collection_name(_active_project))


def set_project(project: str):
    """Switch which project's memory collection is active."""
    global _active_project, _collection
    _active_project = project or "default"
    _collection = _client.get_or_create_collection(name=_collection_name(_active_project))
    return _active_project


def _embed(text: str):
    """Turn text into an embedding vector using Ollama (local)."""
    if ollama is None:
        raise RuntimeError("no local embedder (cloud mode)")
    resp = ollama.embeddings(model=EMBED_MODEL, prompt=text)
    return resp["embedding"]


def remember(fact: str):
    """Save a fact to the active project's long-term memory."""
    fact = fact.strip()
    if not fact:
        return "Nothing to remember (empty)."
    try:
        fact_id = f"fact-{_collection.count()}"
        _collection.add(
            ids=[fact_id],
            embeddings=[_embed(fact)],
            documents=[fact],
        )
    except Exception:
        return "Memory needs a local embedding model (not available in cloud mode)."
    return f"Saved to memory: \"{fact}\""


def recall(query: str, n: int = 3):
    """Find the most relevant saved facts in the active project."""
    try:
        if _collection.count() == 0:
            return "(memory is empty)"
        results = _collection.query(
            query_embeddings=[_embed(query)],
            n_results=min(n, _collection.count()),
        )
    except Exception:
        return "(memory unavailable in cloud mode)"
    docs = results.get("documents", [[]])[0]
    if not docs:
        return "(no relevant memories found)"
    return "\n".join(f"- {d}" for d in docs)


def list_all():
    """Return every saved fact in the active project."""
    data = _collection.get()
    docs = data.get("documents", [])
    if not docs:
        return "(memory is empty)"
    return "\n".join(f"- {d}" for d in docs)


def forget_all():
    """Wipe the active project's memories. Destructive — gate behind a confirm."""
    global _collection
    name = _collection_name(_active_project)
    _client.delete_collection(name)
    _collection = _client.get_or_create_collection(name=name)
    return f"All memories cleared for project '{_active_project}'."
