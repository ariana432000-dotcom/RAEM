"""Analyst cache: an identical analyst prompt on the same day is answered from disk, not from the model.

Ported from the research notebook (BATBC v10, "ANALYST CACHE").  Only temperature-0 calls are cached.  The key holds
everything the answer depends on: company, trade date, provider, model, temperature, output cap and a hash of the whole
prompt text (which already carries every fetched price, headline and fundamental).  Any change gives a different key,
so the analyst runs fresh.  Only the model's text is cached; numbers, the fact sheet and the regime are recomputed in
code on every run.  Switch it off for the runs you report in the thesis with RAEM_ANALYST_CACHE=0.

A cloud model that takes no temperature (newer Claude models) is never cached, because its answers are not repeatable.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from . import config

VERSION = "v10"
MAX_ENTRIES = 200
STATS = {"hits": 0, "misses": 0}


def enabled() -> bool:
    return config.flag("RAEM_ANALYST_CACHE", True)


def cache_file() -> Path:
    return Path(os.environ.get("RAEM_ANALYST_CACHE_FILE") or config.DATA_DIR / "analyst_cache.json")


def reset_stats() -> None:
    STATS.update(hits=0, misses=0)


def _load() -> dict:
    try:
        with open(cache_file(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and isinstance(d.get("entries"), dict) else {"entries": {}}
    except Exception:  # noqa: BLE001
        return {"entries": {}}


def _save(d: dict) -> None:
    try:
        ents = d.get("entries", {})
        if len(ents) > MAX_ENTRIES:
            keep = sorted(ents.items(), key=lambda kv: kv[1].get("ts", 0), reverse=True)[:MAX_ENTRIES]
            d["entries"] = dict(keep)
        path = cache_file()
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001
        print(f"   analyst cache not saved: {str(e)[:80]}")


def is_deterministic(llm) -> bool:
    try:
        return float(getattr(llm, "temperature", None)) == 0.0
    except (TypeError, ValueError):
        return False


def make_key(name: str, llm, messages, company: str = "", trade_date: str = "") -> str:
    texts = "\x1f".join(str(getattr(m, "content", m.get("content") if isinstance(m, dict) else m)) for m in (
        messages if isinstance(messages, (list, tuple)) else [messages]))
    parts = [VERSION, name, company, trade_date, os.environ.get("RAEM_LLM_PROVIDER", ""),
             getattr(llm, "model", None) or getattr(llm, "model_name", "") or "", getattr(llm, "temperature", ""),
             getattr(llm, "num_ctx", ""), getattr(llm, "num_predict", "") or getattr(llm, "max_tokens", ""),
             hashlib.sha256(texts.encode("utf-8", "ignore")).hexdigest()]
    return hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:32]


class _Cached:
    """What a cache hit returns: the same .content the agents read from a model answer."""

    def __init__(self, content: str):
        self.content = content


def cached_invoke(name: str, llm, messages, invoke, company: str = "", trade_date: str = "", log=print):
    """`invoke(messages)` for the analyst `name`, answered from the cache when an identical call was made before."""
    if not (enabled() and is_deterministic(llm)):
        return invoke(messages)
    key = make_key(name, llm, messages, company, trade_date)
    cache = _load()
    hit = cache["entries"].get(key)
    if hit and str(hit.get("text", "")).strip():
        STATS["hits"] += 1
        log(f"cached analyst output: {name} (saved {hit.get('saved_at', '?')}; identical data + prompt + model, no LLM call)")
        return _Cached(hit["text"])
    STATS["misses"] += 1
    out = invoke(messages)
    text = str(getattr(out, "content", out))
    if text.strip() and not text.lstrip().startswith(("Error", "(")):
        cache["entries"][key] = {"text": text, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"), "ts": time.time(), "agent": name}
        _save(cache)
    return out
