"""AI-analysis trades for the Backtest page, read from the ChromaDB episodic memory.

    python backend/app/pipeline/export_ai_trades.py            # writes backend/data/ai_trades.json
    python backend/app/pipeline/export_ai_trades.py --check    # print a summary, write nothing

Every finished AI analysis is stored as one episode in the `raem_episodic_memory` collection (company, trade_date, final_signal,
entry_price, exit_price, pnl_pct, outcome_status, session_id, hours_held). This script reads those episodes straight from
chroma.sqlite3 (read-only, standard library only, so ChromaDB itself is not needed) and writes one list of calls per ticker:

    [entry date, exit date, side, entry price, exit price, return %]    side: 1 = BUY, -1 = SELL, 0 = HOLD (no position)

  * only RESOLVED episodes (an exit price exists) are listed; PENDING ones are only counted
  * the exit date is the session's start time plus `hours_held`
  * one call per ticker and day: the newest session wins
  * the return is the episode's own pnl_pct (positive when the call made money; a SELL makes money when the price falls)
  * Midea's episodes are logged as 000333.SZ, a different listing from the site's 0300.HK, so they are not merged

The script never stops a refresh: on any error it says so, keeps the old file and exits 0.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
ENGINE = os.environ.get("RAEM_ENGINE") or os.path.join(BACKEND, "engine")
LOG_DIR = os.environ.get("RAEM_LOG_DIR") or os.path.join(ENGINE, "notebooks")
DB = os.environ.get("RAEM_CHROMA_SQLITE") or os.path.join(LOG_DIR, "raem_chroma_db", "chroma.sqlite3")
OUT = os.environ.get("RAEM_AI_TRADES_OUT") or os.path.join(BACKEND, "data", "ai_trades.json")
COLLECTION = "raem_episodic_memory"
ALIAS = {"ARAMCO": "2222.SR", "APPLE": "AAPL"}   # short names used by the research notebooks
SIDE = {"BUY": 1, "SELL": -1}


def read_episodes(path: str) -> list[dict]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT e.id, m.key, m.string_value, m.int_value, m.float_value FROM embeddings e "
            "JOIN embedding_metadata m ON m.id = e.id JOIN segments s ON s.id = e.segment_id "
            "JOIN collections c ON c.id = s.collection WHERE c.name = ?", (COLLECTION,)).fetchall()
    finally:
        con.close()
    eps: dict[int, dict] = {}
    for i, k, s, iv, fv in rows:
        eps.setdefault(i, {})[k] = s if s is not None else (iv if iv is not None else fv)
    return list(eps.values())


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def exit_date(ep: dict) -> str | None:
    h = num(ep.get("hours_held"))
    try:
        start = datetime.strptime(str(ep.get("session_id")), "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        return None
    return (start + timedelta(hours=h)).strftime("%Y-%m-%d") if h is not None else None


def build(episodes: list[dict]) -> dict:
    best: dict[tuple[str, str], dict] = {}
    pending: dict[str, int] = {}
    for ep in episodes:
        raw = str(ep.get("company") or "").strip()
        tk, day = ALIAS.get(raw.upper(), raw), str(ep.get("trade_date") or "")[:10]
        if not tk or not day or ep.get("entry_price") is None:
            continue
        if str(ep.get("outcome_status")).upper() != "RESOLVED":
            pending[tk] = pending.get(tk, 0) + 1
            continue
        if num(ep.get("exit_price")) is None or num(ep.get("entry_price")) is None or exit_date(ep) is None:
            continue
        old = best.get((tk, day))
        if old is None or str(ep.get("session_id")) > str(old.get("session_id")):
            best[(tk, day)] = ep
    trades: dict[str, list] = {}
    for (tk, day), ep in sorted(best.items()):
        side = SIDE.get(str(ep.get("final_signal")).upper(), 0)
        ret = num(ep.get("pnl_pct")) or 0.0
        trades.setdefault(tk, []).append([day, max(exit_date(ep), day), side, num(ep["entry_price"]), num(ep["exit_price"]), round(ret, 2)])
    return {"source": "ChromaDB raem_episodic_memory", "trades": trades, "pending": pending}


def main(argv: list[str]) -> int:
    try:
        out = build(read_episodes(DB))
        n = sum(len(v) for v in out["trades"].values())
        print(f"AI trades: {n} resolved calls for {len(out['trades'])} tickers, {sum(out['pending'].values())} still pending")
        for tk, v in out["trades"].items():
            print(f"  {tk}: {len(v)} calls, {v[0][0]} to {v[-1][1]}")
        if "--check" not in argv:
            os.makedirs(os.path.dirname(OUT), exist_ok=True)
            with open(OUT, "w", encoding="utf-8", newline="\n") as f:
                json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
            print("wrote", OUT)
    except Exception as e:   # noqa: BLE001  (never stop a refresh)
        print(f"AI trades export skipped, old file kept: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
