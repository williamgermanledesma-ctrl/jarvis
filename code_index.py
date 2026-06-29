"""
code_index.py
-------------
Semantic code intelligence. Parses Python files with the `ast` module to extract
symbols (functions, classes, signatures, docstrings) instead of dumping raw code,
then stores them in a per-project ChromaDB collection so the assistant can do a
semantic search over code structure — e.g. "where is security handled?" — and
pull only the relevant symbols into context.

This scales far better than dumping whole files: a large repo becomes a compact,
searchable symbol index.
"""

import os
import ast
import re
try:
    import ollama
except ImportError:
    ollama = None  # cloud mode fallback
import chromadb
from tools import actions

EMBED_MODEL = "nomic-embed-text"
_client = chromadb.PersistentClient(path="./memory_store")

# The server sets this so the registered tools know which project to index/search.
ACTIVE_PROJECT = "default"


def set_project(project):
    global ACTIVE_PROJECT
    ACTIVE_PROJECT = project or "default"


def index_codebase_tool(directory: str = ""):
    """Tool wrapper: index the active project's code."""
    return index_project(ACTIVE_PROJECT, directory)


def search_code_tool(query: str):
    """Tool wrapper: semantic search over the active project's code index."""
    return search_code(ACTIVE_PROJECT, query)


def _coll_name(project):
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", project or "default").strip("_-") or "default"
    return f"code_{safe}"[:63]


def _embed(text):
    return ollama.embeddings(model=EMBED_MODEL, prompt=text)["embedding"]


def _extract_symbols(path):
    """Parse one Python file into a list of symbol descriptions."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src)
    except Exception:
        return []
    symbols = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = ", ".join(a.arg for a in node.args.args)
            doc = ast.get_docstring(node) or ""
            symbols.append({
                "kind": "function", "name": node.name,
                "signature": f"def {node.name}({args})",
                "doc": doc, "line": node.lineno,
            })
        elif isinstance(node, ast.ClassDef):
            doc = ast.get_docstring(node) or ""
            methods = [n.name for n in node.body
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            symbols.append({
                "kind": "class", "name": node.name,
                "signature": f"class {node.name}",
                "doc": doc, "methods": methods, "line": node.lineno,
            })
    return symbols


def index_project(project, directory=""):
    """
    Walk the project's code, extract symbols, and (re)build its code index in
    ChromaDB. Returns a short summary string.
    """
    base = os.path.join(actions.WORKSPACE, directory) if directory else actions.WORKSPACE
    if not actions._within_workspace(base) or not os.path.isdir(base):
        return f"Refused or not a directory: {base}"

    name = _coll_name(project)
    try:
        _client.delete_collection(name)
    except Exception:
        pass
    coll = _client.get_or_create_collection(name=name)

    SKIP = {".git", "node_modules", "__pycache__", "venv", ".venv", "memory_store"}
    ids, docs, embs, metas = [], [], [], []
    n = 0
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith(".")]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(root, fn)
            rel = os.path.relpath(fp, base)
            for sym in _extract_symbols(fp):
                text = f"{sym['signature']} in {rel}"
                if sym.get("doc"):
                    text += f"\n{sym['doc']}"
                if sym.get("methods"):
                    text += f"\nmethods: {', '.join(sym['methods'])}"
                ids.append(f"{rel}:{sym['name']}:{sym['line']}")
                docs.append(text)
                metas.append({"file": rel, "name": sym["name"],
                              "kind": sym["kind"], "line": sym["line"]})
                n += 1

    if not docs:
        return "No Python symbols found to index."
    # Embed in batches to avoid huge single calls.
    for i in range(0, len(docs), 32):
        chunk = docs[i:i+32]
        embs_chunk = [_embed(d) for d in chunk]
        coll.add(ids=ids[i:i+32], documents=chunk,
                 embeddings=embs_chunk, metadatas=metas[i:i+32])
    return f"Indexed {n} symbols from {project}. Ask me about the code and I'll search it."


def search_code(project, query, n=5):
    """Semantic search over the project's indexed symbols."""
    name = _coll_name(project)
    try:
        coll = _client.get_collection(name)
    except Exception:
        return "(no code index yet — run index_codebase first)"
    if coll.count() == 0:
        return "(code index is empty)"
    res = coll.query(query_embeddings=[_embed(query)],
                     n_results=min(n, coll.count()))
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    if not docs:
        return "(no relevant code found)"
    out = []
    for d, m in zip(docs, metas):
        out.append(f"[{m.get('file')}:{m.get('line')}] {d}")
    return "\n\n".join(out)
