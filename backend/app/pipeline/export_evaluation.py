"""Evaluation export: how often the AI analysis' BUY / HOLD / SELL calls were right, per company and overall.

    python backend/app/pipeline/export_evaluation.py            # writes backend/data/evaluation.json
    python backend/app/pipeline/export_evaluation.py --check    # print a summary, write nothing

Everything is merged from files, so a new run only needs new files in the usual places (nothing is typed in):

  Signals  (one call per company and day)
    1. trading logs          backend/engine/notebooks/trading_log_*.json   (RAEM_LOG_DIR overrides the folder)
    2. the session archive   backend/data/session_archive.json.gz          (RAEM_SNAPSHOT overrides the file)
    3. drop-in files         backend/data/evaluation_inputs/*.json[.gz]    session lists, same format as the trading logs
                             backend/data/evaluation_inputs/*.csv          columns trade_date, ticker, final_signal
                                                                           (optional: finalized, filled, imputed)
       Later sources win for the same company and day; rows flagged imputed, or not finalized, are ignored.
  Closing prices
    1. the 30-day price window that every logged session carries (market_stock_data), newest session wins
    2. drop-in files         backend/data/evaluation_inputs/*.csv          columns ticker, date, close  (these win)
    3. the engine price CSVs backend/engine/data/*.csv, only for days AFTER the last logged close and only when
                             the CSV agrees with the logged closes on the last day both have (a dividend-adjusted
                             history is not mixed with unadjusted closes)

Scoring (the rule of the evaluation report, "within the next N trading days", N = 1, 2, 3):
  entry price = close of the call's day, or the last close before it when the market had no bar that day
                (weekend, holiday, no data yet: the call keeps its own date, flagged `carried`)
  BUY  right if some close within N days is above the entry      SELL right if some close is below it
  HOLD right if some close within N days is the same or higher
  Only calls made on or after RAEM_EVAL_FROM (default 2026-09-19, the start of the live runs; "" = every call) are
  listed and scored; earlier replay runs stay in the session archive.
  A call needs three later closes to be scored (the same calls for N = 1, 2, 3); the others are `pending` and
  are scored by a later run, once the prices exist. Returns are rounded to 0.01 % before they are compared.
  Baselines: always-HOLD on the same calls; for BUY / SELL the share of calls on which the price rose / fell.
  Intervals are 95 % Wilson intervals.

The script never stops a refresh: on any error it says so, keeps the old evaluation.json and exits 0.
Standard library only, so the tests run in the same small environment as the rest of the suite.
"""
from __future__ import annotations

import csv
import glob
import gzip
import io
import json
import math
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
DATA = os.path.join(BACKEND, "data")
INPUTS = os.environ.get("RAEM_EVAL_INPUTS") or os.path.join(DATA, "evaluation_inputs")
OUT = os.environ.get("RAEM_EVAL_OUT") or os.path.join(DATA, "evaluation.json")
FROM = os.environ.get("RAEM_EVAL_FROM", "2026-09-19")   # calls before this date are not on the Evaluation page
ENGINE = os.environ.get("RAEM_ENGINE") or os.path.join(BACKEND, "engine")
LOG_DIR = os.environ.get("RAEM_LOG_DIR") or os.path.join(ENGINE, "notebooks")
ARCHIVE = os.environ.get("RAEM_SNAPSHOT") or os.path.join(DATA, "session_archive.json.gz")

SIGNALS = ("BUY", "HOLD", "SELL")
WINDOWS = (1, 2, 3)
NEED = max(WINDOWS)  # later closes a call needs before it is scored
# the research notebooks and the older exports use these short names for two listings
ALIAS = {"ARAMCO": "2222.SR", "MIDEA": "000333.SZ"}
NAMES = {"AAPL": "Apple", "IBM": "IBM", "2222.SR": "Saudi Aramco", "000333.SZ": "Midea Group (000333.SZ)", "0300.HK": "Midea Group (0300.HK)",
         "ABBANK": "AB Bank", "BATBC": "British American Tobacco Bangladesh", "BPML": "Bashundhara Paper Mills", "WALTONHIL": "Walton Hi-Tech Industries"}
MISSING = {"", "n/a", "na", "nan", "none", "null"}
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def ticker_of(name) -> str:
    n = str(name or "").strip()
    return ALIAS.get(n.upper(), n)


def truthy(v) -> bool:
    return str(v).strip().lower() in {"true", "1", "yes", "y"}


def wilson(k: int, n: int, z: float = 1.96):
    if n <= 0:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(100 * (c - h), 1), round(100 * (c + h), 1)


# --------------------------------------------------------------------------------------- readers
def read_json(path: str):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def last_signal(txt: str):
    """Last 'FINAL TRANSACTION PROPOSAL: X' (or '**Action**: X', '**Rating**: X') in a decision text."""
    m = re.findall(r"FINAL TRANSACTION PROPOSAL:?\**\s*\**\s*(BUY|SELL|HOLD)", txt, re.I)
    if not m:
        m = re.findall(r"\*\*Action\*\*:\s*(Buy|Sell|Hold)", txt, re.I)
    if not m:
        m = re.findall(r"\*\*Rating\*\*:\s*(Buy|Sell|Hold|Overweight|Underweight)", txt, re.I)
        m = [{"OVERWEIGHT": "BUY", "UNDERWEIGHT": "SELL"}.get(x.upper(), x.upper()) for x in m]
    return m[-1].upper() if m else None


def decision_of(s: dict):
    """(signal, filled) of one session. A finished session gives its final signal; a run that stopped after the
    Trader step is completed from the Trader's own decision (filled=True); a run with no decision gives None."""
    st = s.get("summary_table") or {}
    sig = str(st.get("final_signal") or "").upper()
    if s.get("session_end"):
        if sig in SIGNALS:
            return sig, False
        sig = last_signal(str(s.get("final_decision") or ""))
        return (sig, False) if sig else (None, False)
    for step in reversed(s.get("steps") or []):
        if str(step.get("step", "")).startswith("Trader"):
            o = step.get("output")
            sig = last_signal(o if isinstance(o, str) else json.dumps(o, ensure_ascii=False))
            if sig:
                return sig, True
    return None, False


def window_closes(s: dict) -> dict:
    """{date: close} from the price window a session logged."""
    raw = (s.get("summary_table") or {}).get("market_stock_data")
    if not raw:
        return {}
    lines = [l for l in str(raw).split("\n") if l.strip() and not l.startswith("#")]
    if len(lines) < 2:
        return {}
    rows = list(csv.DictReader(io.StringIO("\n".join(lines))))
    out = {}
    for r in rows:
        d, c = str(r.get("Date", ""))[:10], r.get("Close")
        try:
            if DATE.match(d) and c not in (None, ""):
                out[d] = float(c)
        except ValueError:
            continue
    return out


def load_sessions(info: dict) -> list[dict]:
    """Every session of every source, as plain dicts (a source that cannot be read is skipped and reported)."""
    out: list[dict] = []

    def add(rows, source):
        n = 0
        for s in rows if isinstance(rows, list) else [rows]:
            if isinstance(s, dict) and s.get("session_id") and s.get("company") and s.get("trade_date"):
                if s.get("imputed"):
                    continue
                s = dict(s); s["_source"] = source
                out.append(s); n += 1
        info["sessions"][source] = info["sessions"].get(source, 0) + n

    for p in sorted(glob.glob(os.path.join(LOG_DIR, "trading_log_*.json"))):
        try:
            add(read_json(p), "logs")
        except (OSError, ValueError) as e:
            info["skipped"].append(f"{os.path.basename(p)}: {e}")
    try:
        if os.path.exists(ARCHIVE):
            rows = []
            for r in read_json(ARCHIVE):
                d = r.get("data") if isinstance(r, dict) else None
                if isinstance(d, dict):
                    d = dict(d)
                    d.setdefault("company", r.get("company"))
                    d.setdefault("trade_date", r.get("trade_date"))
                    d.setdefault("session_start", r.get("session_start"))
                    rows.append(d)
            add(rows, "archive")
    except (OSError, ValueError, AttributeError) as e:
        info["skipped"].append(f"{os.path.basename(ARCHIVE)}: {e}")
    for p in sorted(glob.glob(os.path.join(INPUTS, "*.json")) + glob.glob(os.path.join(INPUTS, "*.json.gz"))):
        try:
            add(read_json(p), "input_files")
            info["files"].append(os.path.basename(p))
        except (OSError, ValueError) as e:
            info["skipped"].append(f"{os.path.basename(p)}: {e}")
    return out


def pick_calls(sessions: list[dict]) -> dict:
    """One call per (ticker, trade_date): a finished run beats an unfinished one, then the run with more steps, then
    the latest run. {(ticker, date): {"signal", "filled", "src"}}"""
    groups = defaultdict(list)
    for s in sessions:
        groups[(ticker_of(s["company"]), str(s["trade_date"])[:10])].append(s)
    calls = {}
    for key, runs in groups.items():
        runs.sort(key=lambda s: (1 if s.get("session_end") else 0, len(s.get("steps") or []), str(s.get("session_id"))))
        for s in reversed(runs):  # best first; fall through to the next run when one has no decision at all
            sig, filled = decision_of(s)
            if sig:
                calls[key] = {"signal": sig, "filled": filled, "src": s["_source"]}
                break
    return calls


def csv_rows(path: str):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def load_inputs(info: dict):
    """Drop-in CSV files: (signal rows, close rows)."""
    sig, closes = [], []
    for p in sorted(glob.glob(os.path.join(INPUTS, "*.csv"))):
        try:
            rows = csv_rows(p)
        except (OSError, csv.Error, UnicodeError) as e:
            info["skipped"].append(f"{os.path.basename(p)}: {e}")
            continue
        cols = {c.strip().lower() for c in (rows[0].keys() if rows else [])}
        name = os.path.basename(p)
        if {"ticker", "date", "close"} <= cols:
            closes += [{k.strip().lower(): v for k, v in r.items()} for r in rows]
            info["files"].append(name)
        elif {"trade_date", "ticker"} <= cols and ({"final_signal"} & cols or {"signal"} & cols):
            sig += [{k.strip().lower(): v for k, v in r.items()} for r in rows]
            info["files"].append(name)
        else:
            info["skipped"].append(f"{name}: not a signals file (trade_date, ticker, final_signal) or a closes file (ticker, date, close)")
    return sig, closes


def engine_closes(ticker: str) -> dict:
    """{date: close} from the engine's price CSV of a ticker ('' when it has none)."""
    key = ticker.replace(".", "_")
    for p in sorted(glob.glob(os.path.join(ENGINE, "data", "*.csv"))):
        if re.search(rf"(^|_){re.escape(key)}_\d+y\.csv$", os.path.basename(p), re.I):
            out = {}
            try:
                for r in csv_rows(p):
                    low = {k.strip().lower(): v for k, v in r.items()}
                    d, c = str(low.get("date", ""))[:10], low.get("close")
                    if DATE.match(d) and c not in (None, ""):
                        out[d] = float(c)
            except (OSError, ValueError, csv.Error):
                return {}
            return out
    return {}


# --------------------------------------------------------------------------------------- build
def build(info: dict | None = None) -> dict:
    info = info if info is not None else {}
    info.update({"sessions": {}, "skipped": [], "files": []})
    sessions = load_sessions(info)
    calls = pick_calls(sessions)
    sig_rows, close_rows = load_inputs(info)

    # drop-in signal rows win over what the logs and the archive give for the same company and day
    n_rows = 0
    for r in sig_rows:
        if truthy(r.get("imputed")) or ("finalized" in r and not truthy(r.get("finalized"))):
            continue
        sig = str(r.get("final_signal") or r.get("signal") or "").strip().upper()
        d = str(r.get("trade_date", ""))[:10]
        if sig in SIGNALS and DATE.match(d):
            calls[(ticker_of(r.get("ticker")), d)] = {"signal": sig, "filled": truthy(r.get("filled")), "src": "input_files"}
            n_rows += 1
    info["signal_rows"] = n_rows

    # prices: logged windows (oldest session first, so the newest wins), then drop-in closes
    prices: dict[str, dict[str, float]] = defaultdict(dict)
    for s in sorted(sessions, key=lambda s: (str(s["trade_date"]), str(s.get("session_id")))):
        prices[ticker_of(s["company"])].update(window_closes(s))
    n_win = sum(len(v) for v in prices.values())
    n_in = 0
    for r in close_rows:
        d = str(r.get("date", ""))[:10]
        try:
            c = float(r.get("close"))
        except (TypeError, ValueError):
            continue
        if DATE.match(d) and c > 0:
            prices[ticker_of(r.get("ticker"))][d] = c; n_in += 1
    n_eng = 0
    for t in sorted({t for t, _ in calls} | set(prices)):
        have = prices.get(t, {})
        eng = engine_closes(t)
        if not eng or not have:
            continue
        last = max(have)
        common = [d for d in eng if d in have and d <= last]
        # the engine file must agree with the logged closes on the last day both have
        if not common or abs(eng[max(common)] - have[max(common)]) > 0.005 * have[max(common)]:
            continue
        for d, c in eng.items():
            if d > last and c > 0:
                have[d] = c; n_eng += 1
    info["closes"] = {"logged_windows": n_win, "input_files": n_in, "engine_csv": n_eng}
    out = score({k: c for k, c in calls.items() if k[1] >= FROM}, prices, info)
    out["from"] = FROM
    return out


def rnd(x: float) -> float:
    return round(x + 0.0, 2) + 0.0


def right(sig: str, rets: list[float]) -> bool:
    if sig == "BUY":
        return max(rets) > 0
    if sig == "SELL":
        return min(rets) < 0
    return max(rets) >= 0


def stat(k: int, n: int, base_k: int | None = None) -> dict:
    lo, hi = wilson(k, n)
    d = {"n": n, "k": k, "acc": round(100 * k / n, 1) if n else None, "lo": lo, "hi": hi}
    if base_k is not None:
        d["base"] = round(100 * base_k / n, 1) if n else None
    return d


def score(calls: dict, prices: dict, info: dict) -> dict:
    series = {t: sorted(v.items()) for t, v in prices.items()}
    rows = []  # one per call
    for (t, d), c in sorted(calls.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        s = series.get(t) or []
        dates = [x[0] for x in s]
        i = -1
        for j, dd in enumerate(dates):
            if dd <= d:
                i = j
            else:
                break
        if i < 0:
            rows.append({"date": d, "t": t, "sig": c["signal"], "filled": c["filled"], "carried": False, "entry": None, "r": [], "src": c["src"]})
            continue
        e = s[i][1]
        later = [x[1] for x in s[i + 1:i + 1 + NEED]]
        rows.append({"date": d, "t": t, "sig": c["signal"], "filled": c["filled"], "carried": dates[i] != d, "entry": e,
                     "r": [rnd((x - e) / e * 100) for x in later], "src": c["src"]})
    for r in rows:
        r["scored"] = len(r["r"]) >= NEED

    def block(rs: list[dict], w: int) -> dict:
        sc = [r for r in rs if r["scored"]]
        k = sum(right(r["sig"], r["r"][:w]) for r in sc)
        hold = sum(right("HOLD", r["r"][:w]) for r in sc)
        out = stat(k, len(sc), hold)
        out["hold"] = hold
        out["rose"] = sum(right("BUY", r["r"][:w]) for r in sc)
        out["fell"] = sum(right("SELL", r["r"][:w]) for r in sc)
        return out

    def pack(rs: list[dict]) -> dict:
        return {str(w): block(rs, w) for w in WINDOWS}

    def side(rs: list[dict]) -> list[dict]:
        return [r for r in rs if r["sig"] in ("BUY", "SELL")]   # the calls that took a side

    tickers = sorted({r["t"] for r in rows}, key=lambda t: (-sum(1 for r in rows if r["t"] == t and r["scored"]), t))
    companies = []
    for t in tickers:
        rs = [r for r in rows if r["t"] == t]
        companies.append({"ticker": t, "name": NAMES.get(t, t), "calls": len(rs), "scored": sum(r["scored"] for r in rs),
                          "pending": sum(not r["scored"] for r in rs), "carried": sum(r["carried"] for r in rs if r["scored"]),
                          "w": pack(rs), "bs": pack(side(rs)), "signals": {s: pack([r for r in rs if r["sig"] == s]) for s in SIGNALS}})
    sc_rows = [r for r in rows if r["scored"]]

    def sig_block(sig: str, w: int) -> dict:
        rs = [r for r in sc_rows if r["sig"] == sig]
        b = block(rs, w)
        # "if always this signal": what saying the same thing on every scored call would have scored
        every = block(sc_rows, w)
        b["base"] = round(100 * {"BUY": every["rose"], "SELL": every["fell"], "HOLD": every["hold"]}[sig] / every["n"], 1) if every["n"] else None
        return b

    trend = {}
    for w in WINDOWS:
        k = n = hk = 0
        pts = []
        for d in sorted({r["date"] for r in sc_rows}):
            for r in (x for x in sc_rows if x["date"] == d):
                n += 1; k += right(r["sig"], r["r"][:w]); hk += right("HOLD", r["r"][:w])
            pts.append([d, n, k, hk])
        trend[str(w)] = pts
    nofill = [r for r in sc_rows if not r["filled"]]
    overall = pack(rows)
    overall["without_filled"] = {str(w): {"n": len(nofill), "k": sum(right(r["sig"], r["r"][:w]) for r in nofill)} for w in WINDOWS}
    dates = [r["date"] for r in rows]
    return {
        "version": 1,
        "asof": max(dates) if dates else "",
        "first": min(dates) if dates else "",
        "windows": list(WINDOWS), "headline": NEED,
        "calls": len(rows), "scored": len(sc_rows), "pending": len(rows) - len(sc_rows),
        "carried": sum(r["carried"] for r in sc_rows), "filled": sum(r["filled"] for r in sc_rows),
        "overall": overall,
        "bs": pack(side(rows)),
        "companies": companies,
        "signals": {str(w): {s: sig_block(s, w) for s in SIGNALS} for w in WINDOWS},
        "trend": trend,
        "rows": [[r["date"], r["t"], r["sig"], r["entry"], r["r"], int(r["carried"]), int(r["filled"]), int(r["scored"])] for r in rows],
        "sources": {"sessions": info.get("sessions", {}), "signal_rows_from_files": info.get("signal_rows", 0),
                    "closes": info.get("closes", {}), "files": info.get("files", []), "skipped": info.get("skipped", [])},
    }


# --------------------------------------------------------------------------------------- command line
def summary(ev: dict) -> str:
    o = ev["overall"][str(ev["headline"])]
    lines = [f"evaluation: {ev['calls']} calls ({ev['first']} to {ev['asof']}), {ev['scored']} scored, {ev['pending']} waiting for later closes",
             f"  overall within {ev['headline']} days: {o['k']}/{o['n']} = {o['acc']}%  (95% interval {o['lo']}-{o['hi']}%, always HOLD {o['base']}%)"]
    for c in ev["companies"]:
        w = c["w"][str(ev["headline"])]
        lines.append(f"  {c['ticker']:<10} {w['k']:>3}/{w['n']:<3} {w['acc']}%   calls {c['calls']}, pending {c['pending']}")
    s = ev["sources"]
    lines.append(f"  sessions from {s['sessions']}; signal rows from files {s['signal_rows_from_files']}; closes {s['closes']}")
    for x in s["skipped"]:
        lines.append(f"  skipped: {x}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        ev = build({})
        if not ev["calls"]:
            raise ValueError("no calls found in any source")
        print(summary(ev))
        if "--check" in argv:
            return 0
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        tmp = OUT + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:  # LF on every OS, like git stores it
            json.dump(ev, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, OUT)
        print(f"wrote {os.path.relpath(OUT, BACKEND)} ({os.path.getsize(OUT) // 1024} KB)")
    except Exception as e:  # a failed evaluation must never stop the refresh: the old file stays
        print(f"!! evaluation skipped, the saved evaluation.json is kept: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
