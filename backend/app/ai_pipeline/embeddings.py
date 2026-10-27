"""Embeddings of the RAEM memory (past episodes, news chunks).

"ollama" = nomic-embed-text on Ollama (the original store, raem_chroma_db); "local" = chromadb's built-in MiniLM model, used
when Ollama has no nomic-embed-text; "hash" = word hashing (no model, no download). Vectors of different models cannot share
a collection, so local and hash have their own folders and get a copy of the original store's episodes and news chunks."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

from . import config

EMBED_DOWNLOAD_TIMEOUT = int(os.environ.get("RAEM_EMBED_DOWNLOAD_TIMEOUT", "900"))


class RAEMHashEmbedding:
    """Signed feature hashing of words: memory still works (by shared words) with no model."""

    def __init__(self, dim=384):
        self.dim = dim

    def __call__(self, input):
        import hashlib
        import numpy as np
        out = []
        for doc in input:
            v = np.zeros(self.dim, dtype=np.float32)
            for w in re.findall(r"[a-z0-9]+", str(doc).lower()):
                h = int(hashlib.md5(w.encode()).hexdigest()[:8], 16)
                v[h % self.dim] += 1.0 if h & 0x80000000 else -1.0
            n = float(np.linalg.norm(v))
            out.append((v / n if n else v).tolist())
        return out

    @staticmethod
    def name():
        return "raem-hash"

    def get_config(self):
        return {"dim": self.dim}

    @staticmethod
    def build_from_config(cfg):
        return RAEMHashEmbedding(cfg.get("dim", 384))

    def embed_query(self, input):
        return self(input)

    def is_legacy(self):
        return False


def _hash_function():
    """The hash embedding as a chromadb EmbeddingFunction (the base class moved between chromadb versions)."""
    from chromadb.api.types import EmbeddingFunction
    cls = type("RAEMHash", (RAEMHashEmbedding, EmbeddingFunction), {})
    return cls()


def local_model_ready(timeout: int = EMBED_DOWNLOAD_TIMEOUT) -> bool:
    """chromadb's MiniLM model (about 80 MB) on disk, downloading it now if needed, in a child process with a time limit."""
    code = ("from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2 as M; "
            "M()._download_model_if_not_exists()")
    print("== Memory embeddings: checking chromadb's built-in model (a one-time download of about 80 MB)", flush=True)
    try:
        return subprocess.run([sys.executable, "-c", code], timeout=timeout, capture_output=True).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def embed_mode() -> str:
    """ollama / local / hash. RAEM_EMBED forces ollama or hash; 'local' still needs the model."""
    forced = os.environ.get("RAEM_EMBED")
    if forced in ("ollama", "hash"):
        return forced
    if forced != "local":
        try:
            import urllib.request
            with urllib.request.urlopen(config.OLLAMA_BASE_URL + "/api/tags", timeout=5) as r:
                names = [m.get("name", "") for m in json.load(r).get("models", [])]
            if any(n.startswith(config.EMBED_MODEL) for n in names):
                return "ollama"
        except Exception:
            pass
    return "local" if local_model_ready() else "hash"


def store_path(mode: str) -> str:
    base = config.CHROMA_DB_PATH
    return base if mode == "ollama" else f"{base}_{mode}"


def embedding_function(mode: str):
    from chromadb.utils import embedding_functions as ef
    if mode == "ollama":
        return ef.OllamaEmbeddingFunction(url=config.OLLAMA_EMBED_URL, model_name=config.EMBED_MODEL)
    return ef.DefaultEmbeddingFunction() if mode == "local" else _hash_function()


def copy_memory(mode: str) -> int:
    """Copy what the mode's store lacks from the original store (by id, embedded again), so past episodes still count.
    The original store keeps its contents. Returns the number of rows added."""
    if mode == "ollama" or not os.path.isdir(config.CHROMA_DB_PATH):
        return 0
    import chromadb
    added = 0
    try:
        src, dst = chromadb.PersistentClient(path=config.CHROMA_DB_PATH), chromadb.PersistentClient(path=store_path(mode))
        ef = embedding_function(mode)
        for col in src.list_collections():
            s = src.get_collection(getattr(col, "name", col))
            got = s.get(include=["documents", "metadatas"])
            d = dst.get_or_create_collection(name=s.name, embedding_function=ef, metadata=s.metadata or None)
            have = set(d.get(include=[])["ids"])
            rows = [r for r in zip(got["ids"], got["documents"], got["metadatas"]) if r[0] not in have and r[1]]
            for k in range(0, len(rows), 100):
                part = rows[k:k + 100]
                d.add(ids=[r[0] for r in part], documents=[r[1] for r in part], metadatas=[r[2] or None for r in part])
            added += len(rows)
    except Exception as e:  # a run without past memory still works
        print("Memory copy skipped:", str(e)[:200], flush=True)
    return added
