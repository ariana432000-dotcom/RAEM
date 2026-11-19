"""Refresh the AI session lists and the memory section of backend/data/extra.json from what the server already has.

    python backend/tools/refresh_sessions.py      # container start (Dockerfile CMD), before refresh.py --build-only

The page's session lists live in extra.json, and a company's list only changed when it was analysed on the website, so
sessions committed to backend/data/session_archive.json.gz never reached the page. This rebuilds each company's list from
every session the server has: the committed archive snapshot is imported first (INSERT OR IGNORE, so the server's own
sessions are kept), then the trading logs. Nothing is deleted (a row the page already lists is kept even if the server no
longer has that session):
  * the newest session becomes the "latest" entry, as in export_extra.py; when it is the one already shown (a website run,
    with its full debates and model), that entry is kept as it is;
  * a website run's model is filled in from the run database (the website pipeline logs no "LLM Setup" step);
  * the memory section is re-read from the notebooks' memory store (engine/notebooks/raem_chroma_db), read only.
extra.json is only rewritten when something changed. Every part is fail-soft: an error keeps the saved copy.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from app.core import config  # noqa: E402

sys.path.insert(0, str(config.PIPELINE))
os.environ.setdefault("RAEM_DB", str(config.STORAGE / "raem.db"))
import sessions  # noqa: E402

NB = config.ENGINE / "notebooks"


def _clean(v):
    """JSON-safe metadata value (NaN/inf become None), as export_extra.py writes it."""
    if isinstance(v, float):
        return None if not math.isfinite(v) else round(v, 6)
    return v


def website_models() -> list[tuple[str, str, str, str]]:
    """(ticker, started_at, finished_at, model_key) of every finished website analysis."""
    p = os.environ["RAEM_DB"]
    if not os.path.exists(p):
        return []
    c = sqlite3.connect(p, timeout=30)
    try:
        return c.execute("SELECT ticker, started_at, COALESCE(finished_at, ''), model_key FROM runs "
                         "WHERE kind='analysis' AND status='done' AND started_at IS NOT NULL").fetchall()
    except sqlite3.Error:
        return []
    finally:
        c.close()


def refresh_ai(extra: dict) -> int:
    runs = website_models()
    changed = 0
    for t in sorted(extra.get("ai") or {}):
        hist = sessions.sessions_for(str(NB), t)
        if not hist:
            continue
        old = extra["ai"][t] or {}
        new = sessions.entry_from_session(hist[-1][0], hist)
        if (old.get("latest") or {}).get("start") == new["latest"]["start"]:
            new["latest"] = old["latest"]                      # the entry already shown (full debates, model): kept as it is
        # a row the page already lists but the server no longer has (its log and archive row gone) is kept, never dropped
        seen = {str(r.get("start") or "") for r in new["sessions"]}
        kept = [dict(r, signal=r.get("signal") if str(r.get("signal") or "").upper() in ("BUY", "HOLD", "SELL")
                     else sessions.SIGNAL_OF.get(r.get("rating") or ""))
                for r in old.get("sessions") or [] if str(r.get("start") or "") not in seen]
        if kept:
            new["sessions"] = sorted(new["sessions"] + kept, key=lambda r: str(r.get("start") or ""))
        for row in new["sessions"]:
            if row.get("source") in (None, "website") and not row.get("model"):   # None: a kept row from an older export
                m = [k for tk, a, b, k in runs if tk == t and a <= str(row.get("start") or "") <= (b or "9999")]
                if m:
                    row.update(source="website", model=m[0])
        if new != old:
            extra["ai"][t] = new
            changed += 1
    return changed


def refresh_memory(extra: dict) -> int:
    store = NB / "raem_chroma_db"
    if not store.is_dir():
        return 0
    import chromadb
    c = chromadb.PersistentClient(path=str(store))
    mem = extra.setdefault("memory", {})
    changed = 0
    for col in c.list_collections():
        name = getattr(col, "name", col)
        g = c.get_collection(name).get(include=["documents", "metadatas"])
        items = [{"id": i, "doc": sessions.trunc(d, 500), "meta": {k: _clean(v) for k, v in (m or {}).items()}}
                 for i, d, m in zip(g["ids"], g["documents"], g["metadatas"])]
        if not items:
            continue
        if name == "raem_news_corpus":
            if extra.get("news_corpus") != items:
                extra["news_corpus"] = items
                changed += 1
        elif mem.get(name) != items:
            mem[name] = items
            changed += 1
    return changed


def main() -> int:
    if not config.EXTRA.exists():
        print("refresh_sessions: no extra.json yet, nothing to refresh")
        return 0
    extra = json.loads(config.EXTRA.read_text(encoding="utf-8"))
    n_ai = n_mem = 0
    try:
        sessions.import_snapshot()
        sessions.archive_logs(str(NB))
        n_ai = refresh_ai(extra)
    except Exception as e:  # the saved session lists stay
        print("refresh_sessions: session lists skipped:", str(e)[:300])
    try:
        n_mem = refresh_memory(extra)
    except Exception as e:  # the saved memory section stays
        print("refresh_sessions: memory skipped:", str(e)[:300])
    if n_ai or n_mem:
        tmp = str(config.EXTRA) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(extra, fh, ensure_ascii=False)
        os.replace(tmp, config.EXTRA)
    print(f"refresh_sessions: {n_ai} session list(s) and {n_mem} memory section(s) updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
