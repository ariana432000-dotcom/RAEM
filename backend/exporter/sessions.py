"""Turn the notebooks' trading logs (notebooks/trading_log_*.json/.csv) into the website's AI-analysis format.

Shared by export_extra.py (all companies) and run_notebook.py (one fresh run). Only needs the standard library
and pandas, so it can be imported without loading the forecasting models.
"""
from __future__ import annotations

import glob
import gzip
import json
import math
import os
import re
import sqlite3
import time

# Friendly names for the steps the notebooks log with log_step(). Steps not listed keep their own name,
# so a step added to the notebook later still reaches the website.
STEP_LABELS = {
    "Fundamentals Analyst": "Fundamentals",
    "Market Analyst": "Market",
    "Macro Regime Analyst": "Macro regime",
    "News Analyst": "News",
    "Sentiment Analyst": "Sentiment",
    "Graph Analyst": "Graph analyst",
    "Forecasting Agent": "Forecasting agent",
    "Investment Debate (Bull vs Bear)": "Bull vs bear debate",
    "Investment Debate Facilitator": "Debate facilitator",
    "Post-Mortem Self-Critique": "Post-mortem",
    "Trader Decision": "Trader",
    "Risk Management Debate (3 Rounds)": "Risk debate",
    "Risk Facilitator Decision": "Risk facilitator",
    "Portfolio Manager Final Decision": "Portfolio manager",
    "Prediction Engine": "Prediction engine",
    "Forecast Graph": "7-day forecast",
    "Decision Verifier": "Verifier",
    "Position Sizing (rule + RL overlay)": "Position sizing",
    "10-Day Scenario Estimate": "10-day scenario",
}
SKIP_STEPS = {"LLM Setup"}
LONG_STEPS = {"News"}


def trunc(s, n: int) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[:n].rstrip() + " …[truncated]"


RATINGS = ("Buy", "Overweight", "Hold", "Underweight", "Sell")


def rating(txt) -> str | None:
    """The portfolio manager's rating. The notebooks ask for '**Rating**: X'; other models write 'Rating: X',
    'FINAL TRANSACTION PROPOSAL: X' or just the word."""
    s = str(txt or "")
    m = re.search(r"\*\*Rating\*\*:\s*([A-Za-z ]+)", s)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?im)^[\W_]*rating[\W_]*[:\-]\s*[*_]*\s*([A-Za-z]+)", s) or re.search(r"FINAL TRANSACTION PROPOSAL:\s*[*_]*\s*([A-Za-z]+)", s, re.I)
    word = (m.group(1) if m else s.strip().strip("*_#.: ")).title()
    return word if word in RATINGS else None


def sanitize(x):
    """JSON-safe copy: NaN/inf become None (browsers reject NaN in JSON)."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {str(k): sanitize(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [sanitize(v) for v in x]
    return x


def _csv_signals(json_path: str, n: int) -> list:
    """final_signal per session from the matching CSV, when it lines up row for row with the JSON."""
    csv_path = json_path[:-5] + ".csv"
    if not os.path.exists(csv_path):
        return [None] * n
    try:
        import pandas as pd
        df = pd.read_csv(csv_path)
    except Exception:
        return [None] * n
    if len(df) != n or "final_signal" not in df.columns:
        return [None] * n
    return [None if str(v) in ("nan", "None", "") else str(v) for v in df["final_signal"].tolist()]


def sessions_for(nb_dir: str, ticker: str) -> list[tuple[dict, str | None]]:
    """Every session for this company: the trading logs plus the archive, oldest first, with its CSV signal."""
    out = []
    for p in sorted(glob.glob(os.path.join(nb_dir, "trading_log_*.json"))):
        if ".bak" in os.path.basename(p):
            continue
        try:
            sess = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(sess, list):
            continue
        sig = _csv_signals(p, len(sess))
        for s, g in zip(sess, sig):
            if isinstance(s, dict) and s.get("company") == ticker:
                out.append((s, g))
    seen = {str(s.get("session_start", "")) for s, _ in out}
    out.extend(sg for sg in archived(ticker) if str(sg[0].get("session_start", "")) not in seen)
    out.sort(key=lambda sg: str(sg[0].get("session_start", "")))
    return out


# ---- permanent archive ----
# The notebooks keep only the last MAX_DAYS (10) days in trading_log_*.json and drop older sessions on every run.
# Every session seen is therefore also kept in the run database (table `sessions`), so history is never lost.
ARCHIVE_SQL = ("CREATE TABLE IF NOT EXISTS sessions (company TEXT NOT NULL, session_start TEXT NOT NULL, trade_date TEXT, "
               "signal TEXT, data TEXT NOT NULL, archived_at TEXT, PRIMARY KEY (company, session_start))")


def archive_path() -> str:
    return os.environ.get("RAEM_DB") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage", "raem.db")


def _archive(create: bool):
    p = archive_path()
    if not create and not os.path.exists(p):
        return None
    os.makedirs(os.path.dirname(p), exist_ok=True)
    c = sqlite3.connect(p, timeout=30)
    c.execute(ARCHIVE_SQL)
    return c


def _log_sessions(nb_dir: str) -> list[tuple[dict, str | None]]:
    out = []
    for p in sorted(glob.glob(os.path.join(nb_dir, "trading_log_*.json"))):
        if ".bak" in os.path.basename(p):
            continue
        try:
            sess = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if isinstance(sess, list):
            out.extend((s, g) for s, g in zip(sess, _csv_signals(p, len(sess))) if isinstance(s, dict) and s.get("company"))
    return out


def archive_logs(nb_dir: str) -> int:
    """Copy every session in the trading logs into the archive (a newer copy of the same session replaces the old one)."""
    rows = [(s["company"], str(s.get("session_start", "")), s.get("trade_date"), g, json.dumps(sanitize(s), ensure_ascii=False),
             time.strftime("%Y-%m-%d %H:%M:%S")) for s, g in _log_sessions(nb_dir) if s.get("session_start")]
    if not rows:
        return 0
    c = _archive(create=True)
    try:
        c.executemany("INSERT OR REPLACE INTO sessions (company, session_start, trade_date, signal, data, archived_at) VALUES (?,?,?,?,?,?)", rows)
        c.commit()
    finally:
        c.close()
    export_snapshot()
    return len(rows)


def snapshot_path() -> str:
    """A compressed copy of the archive kept with the saved data, so a fresh clone (no database yet) still has the history."""
    return os.environ.get("RAEM_SNAPSHOT") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "session_archive.json.gz")


def export_snapshot() -> int:
    c = _archive(create=False)
    if c is None:
        return 0
    try:
        rows = [{"company": a, "session_start": b, "trade_date": d, "signal": g, "data": json.loads(x)}
                for a, b, d, g, x in c.execute("SELECT company, session_start, trade_date, signal, data FROM sessions ORDER BY company, session_start")]
    finally:
        c.close()
    # deterministic bytes (no gzip timestamp), written only when the content changed, so git sees no change otherwise
    data = gzip.compress(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8"), mtime=0)
    path = snapshot_path()
    if os.path.exists(path) and open(path, "rb").read() == data:
        return len(rows)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    return len(rows)


def import_snapshot() -> int:
    """Load the saved snapshot into the archive (sessions already there are kept)."""
    if not os.path.exists(snapshot_path()):
        return 0
    with gzip.open(snapshot_path(), "rt", encoding="utf-8") as fh:
        rows = json.load(fh)
    c = _archive(create=True)
    try:
        c.executemany("INSERT OR IGNORE INTO sessions (company, session_start, trade_date, signal, data, archived_at) VALUES (?,?,?,?,?,?)",
                      [(r["company"], r["session_start"], r.get("trade_date"), r.get("signal"), json.dumps(r["data"], ensure_ascii=False),
                        time.strftime("%Y-%m-%d %H:%M:%S")) for r in rows])
        c.commit()
    finally:
        c.close()
    return len(rows)


def archived(ticker: str) -> list[tuple[dict, str | None]]:
    c = _archive(create=False)
    if c is None:
        return []
    try:
        return [(json.loads(d), g) for d, g in c.execute("SELECT data, signal FROM sessions WHERE company=? ORDER BY session_start", (ticker,))]
    except Exception:
        return []
    finally:
        c.close()


SIDES = {"bull": "bull", "bear": "bear", "aggressive": "aggressive", "risky": "aggressive", "conservative": "conservative",
         "safe": "conservative", "neutral": "neutral"}
SIGNAL_OF = {"Buy": "BUY", "Overweight": "BUY", "Hold": "HOLD", "Underweight": "SELL", "Sell": "SELL"}


def debate_turns(history, limit: int = 4000) -> list[dict]:
    """'Bull Analyst: ... Bear Analyst: ...' (or Aggressive / Conservative / Neutral) as turns with a round number."""
    parts = re.split(r"(?m)^\s*(Bull|Bear|Aggressive|Risky|Conservative|Safe|Neutral) Analyst:\s*", str(history or ""))
    turns, rnd, first = [], 0, None
    for who, text in zip(parts[1::2], parts[2::2]):
        side = SIDES[who.lower()]
        first = first or side
        if side == first:
            rnd += 1
        if text.strip():
            turns.append({"speaker": f"{who.title()} Analyst", "side": side, "round": rnd, "text": trunc(text.strip(), limit)})
    return turns


def debate_from_steps(raw: dict) -> dict:
    """Debate panels from the logged steps (the notebooks log excerpts: 1500 characters of the bull/bear debate and the
    end of each risk analyst's case). A finished web run replaces this with the full debates (run_notebook.py)."""
    risk = raw.get("risk") if isinstance(raw.get("risk"), dict) else {}
    risk_turns = [{"speaker": f"{k.title()} Analyst", "side": SIDES.get(k, "neutral"), "round": None, "text": trunc(v, 2000)}
                  for k, v in risk.items() if k in SIDES and str(v or "").strip()]
    return {"turns": debate_turns(raw.get("debate")), "facilitator": trunc(raw.get("facilitator"), 6000),
            "risk_turns": risk_turns, "risk_facilitator": trunc(raw.get("risk_facilitator"), 6000),
            "trader": trunc(raw.get("trader"), 4000), "complete": False}


def final_signal(last: dict, raw: dict) -> str | None:
    """BUY / HOLD / SELL: the verifier's effective signal, else the logged final signal, else the rating."""
    ver = raw.get("verifier") if isinstance(raw.get("verifier"), dict) else {}
    for s in (ver.get("effective_signal"), (last.get("summary_table") or {}).get("final_signal")):
        if str(s or "").upper() in ("BUY", "HOLD", "SELL"):
            return str(s).upper()
    return SIGNAL_OF.get(rating(last.get("final_decision")) or "")


def entry_from_session(last: dict, history: list[tuple[dict, str | None]]) -> dict:
    steps, raw = {}, {}
    for st in last.get("steps", []) or []:
        name = st.get("step", "")
        if not name or name in SKIP_STEPS:
            continue
        label = STEP_LABELS.get(name) or ("Risk debate" if name.startswith("Risk Management Debate") else name)  # "(1 Round(s))", "(3 Rounds)"...
        steps[label] = {"time": st.get("timestamp"), "text": trunc(st.get("output"), 3600 if label in LONG_STEPS else 3200)}
        key = {"Bull vs bear debate": "debate", "Debate facilitator": "facilitator", "Risk debate": "risk",
               "Risk facilitator": "risk_facilitator", "Trader": "trader", "Verifier": "verifier"}.get(label)
        if key:
            raw[key] = st.get("output")
    tbl = {k: v for k, v in (last.get("summary_table") or {}).items() if k != "market_stock_data"}
    hist = [{"date": s.get("trade_date"), "start": s.get("session_start"), "signal": g, "rating": rating(s.get("final_decision"))}
            for s, g in history]
    return sanitize({
        "sessions": hist,
        "latest": {"date": last.get("trade_date"), "start": last.get("session_start"), "end": last.get("session_end"),
                   "final_decision": trunc(last.get("final_decision"), 3500), "rating": rating(last.get("final_decision")),
                   "signal": final_signal(last, raw), "verifier": raw.get("verifier") if isinstance(raw.get("verifier"), dict) else None,
                   "debate": debate_from_steps(raw), "steps": steps, "summary": tbl},
    })


def ai_entry(nb_dir: str, ticker: str, since: str | None = None) -> dict | None:
    """The website entry for one company: its session history and the newest session in full.

    With `since` ("YYYY-MM-DD HH:MM:SS"), only a session that started at or after that time counts as the newest,
    which is how a fresh notebook run checks that it really wrote a new session."""
    hist = sessions_for(nb_dir, ticker)
    if not hist:
        return None
    last = hist[-1][0]
    if since and str(last.get("session_start", "")) < since:
        return None
    return entry_from_session(last, hist)


def notebook_map(nb_dir: str) -> dict[str, str]:
    """Ticker -> notebook file, read from each notebook's `COMPANY = "..."` line. Backup copies are ignored."""
    out = {}
    for p in sorted(glob.glob(os.path.join(nb_dir, "*.ipynb"))):
        name = os.path.basename(p)
        if ".bak" in name or name in ("Forecast.ipynb", "agent.ipynb"):
            continue
        try:
            nb = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        for c in nb.get("cells", []):
            if c.get("cell_type") != "code":
                continue
            m = re.search(r'^\s*COMPANY\s*=\s*["\']([^"\']+)["\']', "".join(c.get("source", [])), re.M)
            if m:
                out.setdefault(m.group(1), name)
                break
    return out
