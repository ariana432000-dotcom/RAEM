"""SQLite store for every run (forecast, AI analysis, refresh).

A finished run is kept with its full result, so asking again for the same company, date and model
returns the stored result instead of running everything again. The file lives in backend/storage/
(override with RAEM_DB) and needs no database server.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import config

DB_PATH = Path(os.environ.get("RAEM_DB") or config.STORAGE / "raem.db")
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,          -- forecast | analysis | refresh
    ticker      TEXT NOT NULL,
    run_date    TEXT NOT NULL,          -- YYYY-MM-DD, the key for same-date reuse
    model_key   TEXT NOT NULL,          -- forecast: sorted model keys; analysis: LLM model name
    params      TEXT,
    status      TEXT NOT NULL,          -- queued | running | held | done | failed
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT,
    step        TEXT,
    log         TEXT,
    error       TEXT,
    summary     TEXT,
    result      TEXT
);
CREATE INDEX IF NOT EXISTS ix_runs_key ON runs(kind, ticker, run_date, model_key, status);
CREATE INDEX IF NOT EXISTS ix_runs_created ON runs(created_at);
"""
JSON_COLS = ("params", "summary", "result")


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def set_path(path) -> None:
    global DB_PATH
    DB_PATH = Path(path)
    os.environ["RAEM_DB"] = str(DB_PATH)  # the session archive (exporter/sessions.py) uses the same file


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB_PATH), timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


@contextmanager
def _db():
    """One short-lived connection per call, committed and closed (no file handles left open)."""
    with _lock:
        c = _conn()
        try:
            yield c
            c.commit()
        finally:
            c.close()


def init() -> None:
    """Create the table; a run left 'running' by a stopped server is put on hold, so the next request resumes it."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _db() as c:
        c.executescript(SCHEMA)
        c.execute("UPDATE runs SET status='held', error='The server stopped during this run. Run it again to continue where it stopped.' "
                  "WHERE status='running'")
    try:  # keep every notebook session, even after the notebooks prune their own logs
        import sys
        sys.path.insert(0, str(config.BACKEND / "exporter"))
        from sessions import archive_logs, import_snapshot
        os.environ.setdefault("RAEM_DB", str(DB_PATH))
        import_snapshot()
        archive_logs(str(config.ENGINE / "notebooks"))
    except Exception as e:
        print("session archive skipped:", e)


def _row(r: sqlite3.Row | None, with_result: bool = True) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS:
        if k == "result" and not with_result:
            d.pop("result", None)
            continue
        d[k] = json.loads(d[k]) if d.get(k) else None
    d["log"] = d["log"].split("\n") if d.get("log") else []
    return d


def create(kind: str, ticker: str, run_date: str, model_key: str, params: dict) -> dict:
    rid = uuid.uuid4().hex[:12]
    with _db() as c:
        c.execute("INSERT INTO runs (id, kind, ticker, run_date, model_key, params, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
                  (rid, kind, ticker, run_date, model_key, json.dumps(params), "queued", now()))
    return get(rid)


def update(rid: str, **fields) -> None:
    if not fields:
        return
    vals = []
    for k, v in fields.items():
        if k in JSON_COLS and v is not None:
            v = json.dumps(v, ensure_ascii=False)
        elif k == "log" and isinstance(v, list):
            v = "\n".join(v[-300:])
        vals.append(v)
    sets = ", ".join(f"{k}=?" for k in fields)
    with _db() as c:
        c.execute(f"UPDATE runs SET {sets} WHERE id=?", (*vals, rid))


def get(rid: str, with_result: bool = True) -> dict | None:
    with _db() as c:
        return _row(c.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone(), with_result)


def find(kind: str, ticker: str, run_date: str, model_key: str, statuses=("done",), with_result: bool = True) -> dict | None:
    """Newest run with this exact key and one of these statuses."""
    q = f"SELECT * FROM runs WHERE kind=? AND ticker=? AND run_date=? AND model_key=? AND status IN ({','.join('?' * len(statuses))}) " \
        "ORDER BY created_at DESC LIMIT 1"
    with _db() as c:
        return _row(c.execute(q, (kind, ticker, run_date, model_key, *statuses)).fetchone(), with_result)


def next_queued() -> dict | None:
    with _db() as c:
        return _row(c.execute("SELECT * FROM runs WHERE status='queued' ORDER BY created_at, rowid LIMIT 1").fetchone())


def queued_before(rid: str) -> int:
    with _db() as c:
        r = c.execute("SELECT created_at, rowid FROM runs WHERE id=?", (rid,)).fetchone()
        if r is None:
            return 0
        return c.execute("SELECT COUNT(*) FROM runs WHERE status IN ('queued','running') AND (created_at < ? OR (created_at = ? AND rowid < ?))",
                         (r["created_at"], r["created_at"], r["rowid"])).fetchone()[0]


def list_runs(kind: str | None = None, ticker: str | None = None, limit: int = 50, statuses: tuple = ()) -> list[dict]:
    q, args = "SELECT * FROM runs WHERE 1=1", []
    if kind:
        q += " AND kind=?"
        args.append(kind)
    if ticker:
        q += " AND ticker=?"
        args.append(ticker)
    if statuses:
        q += f" AND status IN ({','.join('?' * len(statuses))})"
        args.extend(statuses)
    q += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    args.append(max(1, min(int(limit), 500)))
    with _db() as c:
        return [_row(r, with_result=False) for r in c.execute(q, args).fetchall()]


def latest_done(run_date: str | None, kind: str) -> dict[str, dict]:
    """The newest finished run per company for one date (or any date when None), with results."""
    out: dict[str, dict] = {}
    with _db() as c:
        if run_date:
            rows = c.execute("SELECT * FROM runs WHERE kind=? AND run_date=? AND status='done' ORDER BY finished_at DESC", (kind, run_date)).fetchall()
        else:
            rows = c.execute("SELECT * FROM runs WHERE kind=? AND status='done' ORDER BY finished_at DESC LIMIT 400", (kind,)).fetchall()
    for r in rows:
        d = _row(r)
        out.setdefault(d["ticker"], d)
    return out


def active_count() -> int:
    with _db() as c:
        return c.execute("SELECT COUNT(*) FROM runs WHERE status IN ('queued','running')").fetchone()[0]
