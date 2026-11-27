"""Single agent vs multi agent: accuracy of the two on the SAME dates, for the Evaluation page.

    python backend/app/pipeline/export_agent_comparison.py            # writes backend/data/agent_comparison.json
    python backend/app/pipeline/export_agent_comparison.py --check    # print a summary, write nothing

The multi agent is the RAEM pipeline (analysts, debate, risk check); the single agent is one model call per date with raw data only. The scoring rule is the
Evaluation page's (export_evaluation.py): BUY right if a close within the next 3 trading days is above the entry, SELL if below, HOLD if one is the same or
higher; returns rounded to 0.01 % first, intervals 95 % Wilson. `RAEM_AC_HOLD=first2` brings the older HOLD rule back (HOLD wrong only if the closes of
day 1 and day 2 are both below the entry).

Same dates: only dates on or after RAEM_AC_FROM (default 2026-09-21) on which BOTH agents made a call and three later closes exist are scored.
Dates only one agent has, dates without a price bar and dates still waiting for later closes are counted and left out.
The page shows BUY and SELL calls only (by_signal()["bs"]); the blocks with HOLD stay in the file.
Each multi-agent row keeps its group: "live" (run on the day) or "replay" (run later for a past date).

Inputs (drop-in files, a sub-folder of evaluation_inputs/ so export_evaluation.py does not read them as AI calls):
  evaluation_inputs/agent_comparison/multi_*.csv     multi agent: ticker, trade_date, signal  (optional column: group)
  evaluation_inputs/agent_comparison/single_*.csv    single agent: ticker, trade_date, signal
  evaluation_inputs/agent_comparison/closes*.csv     closing prices: ticker, date, close
  RAEM_AC_MERGED=<csv>                               optional: also read the merged dataset (finalized, not imputed rows) as multi-agent calls
`backend/tools/import_agent_comparison.py` writes these files from the replay logs and market files. The step never stops a refresh: on any error it says
so, keeps the old agent_comparison.json and exits 0. Standard library only.
"""
from __future__ import annotations

import csv
import glob
import json
import math
import os
import sys
from collections import defaultdict

try:
    from app.pipeline import export_evaluation as E
except ImportError:  # run as a script: this folder is on sys.path
    import export_evaluation as E

DATA = E.DATA
INPUTS = E.INPUTS
AC_DIR = os.environ.get("RAEM_AC_INPUTS") or os.path.join(INPUTS, "agent_comparison")
MERGED = os.environ.get("RAEM_AC_MERGED", "")   # off by default: multi_signals.csv already holds the live runs of the merged dataset
OUT = os.environ.get("RAEM_AC_OUT") or os.path.join(DATA, "agent_comparison.json")
HOLD_RULE = os.environ.get("RAEM_AC_HOLD", "any3")
FROM = os.environ.get("RAEM_AC_FROM", "2026-09-21")   # only call dates on or after this one are compared ("" = every date)
WINDOWS = (3,)
NEED = max(WINDOWS)
SIGNALS = E.SIGNALS


# --------------------------------------------------------------------------------------- scoring
def call_right(sig: str, rets: list, w: int) -> bool:
    """rets = changes (%, rounded to 0.01) of the next closes against the entry."""
    if sig == "HOLD" and HOLD_RULE == "first2":
        return not (rets[0] < 0 and rets[1] < 0)
    return E.right(sig, rets[:w])


def exact_p(b: int, c: int) -> float:
    """Exact two-sided McNemar test: b and c = dates on which only one of the two agents was right."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def block(rows: list, w: int) -> dict:
    """Overall accuracy of both agents and of always-HOLD on a set of dates, for window w."""
    n = len(rows)
    mk = sum(call_right(r["multi"], r["r"], w) for r in rows)
    sk = sum(call_right(r["single"], r["r"], w) for r in rows)
    hk = sum(call_right("HOLD", r["r"], w) for r in rows)
    b = sum(call_right(r["multi"], r["r"], w) and not call_right(r["single"], r["r"], w) for r in rows)
    c = sum(call_right(r["single"], r["r"], w) and not call_right(r["multi"], r["r"], w) for r in rows)
    return {"n": n, "multi": E.stat(mk, n), "single": E.stat(sk, n), "hold": E.stat(hk, n),
            "only_multi": b, "only_single": c, "p": round(exact_p(b, c), 4)}


def by_signal(rows: list, w: int) -> dict:
    """Per agent and signal: calls and correct; "every": the same signal answered on every date; "bs": BUY + SELL signals only."""
    out = {"multi": {}, "single": {}, "every": {}, "bs": {}}
    for who in ("multi", "single"):
        for sg in SIGNALS:
            rs = [r for r in rows if r[who] == sg]
            out[who][sg] = E.stat(sum(call_right(sg, r["r"], w) for r in rs), len(rs))
        rs = [r for r in rows if r[who] in ("BUY", "SELL")]
        out["bs"][who] = E.stat(sum(call_right(r[who], r["r"], w) for r in rs), len(rs))
    for sg in SIGNALS:
        out["every"][sg] = E.stat(sum(call_right(sg, r["r"], w) for r in rows), len(rows))
    return out


# --------------------------------------------------------------------------------------- readers
def csv_dicts(path: str) -> list:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(fh)]


def sig_of(row: dict) -> str:
    return str(row.get("signal") or row.get("final_signal") or "").upper()


def read_signals(info: dict):
    """({ticker: {date: (signal, group)}} of the multi agent, {ticker: {date: signal}} of the single agent)."""
    multi, single = defaultdict(dict), defaultdict(dict)
    for p in sorted(glob.glob(os.path.join(AC_DIR, "multi_*.csv"))):
        try:
            for r in csv_dicts(p):
                t, d, s = E.ticker_of(r.get("ticker")), r.get("trade_date", "")[:10], sig_of(r)
                if s in SIGNALS and E.DATE.match(d):
                    multi[t][d] = (s, r.get("group") or "replay")
            info["files"].append(os.path.basename(p))
        except (OSError, csv.Error, UnicodeError) as e:
            info["skipped"].append(f"{os.path.basename(p)}: {e}")
    for p in sorted(glob.glob(os.path.join(AC_DIR, "single_*.csv"))):
        try:
            for r in csv_dicts(p):
                t, d, s = E.ticker_of(r.get("ticker")), r.get("trade_date", "")[:10], sig_of(r)
                if s in SIGNALS and E.DATE.match(d):
                    single[t][d] = s
            info["files"].append(os.path.basename(p))
        except (OSError, csv.Error, UnicodeError) as e:
            info["skipped"].append(f"{os.path.basename(p)}: {e}")
    if MERGED and os.path.isfile(MERGED):   # the merged dataset: only the companies the single agent has; a replay call on the same date stays
        try:
            for r in csv_dicts(MERGED):
                t, d, s = E.ticker_of(r.get("ticker")), r.get("trade_date", "")[:10], sig_of(r)
                if t not in single or s not in SIGNALS or not E.DATE.match(d) or E.truthy(r.get("imputed")):
                    continue
                if "finalized" in r and not E.truthy(r.get("finalized")):
                    continue
                multi[t].setdefault(d, (s, "merged"))
            info["files"].append(os.path.basename(MERGED))
        except (OSError, csv.Error, UnicodeError) as e:
            info["skipped"].append(f"{os.path.basename(MERGED)}: {e}")
    return multi, single


def read_closes(info: dict) -> dict:
    prices = defaultdict(dict)
    for p in sorted(glob.glob(os.path.join(AC_DIR, "closes*.csv"))):
        try:
            for r in csv_dicts(p):
                t, d = E.ticker_of(r.get("ticker")), r.get("date", "")[:10]
                try:
                    c = float(r.get("close"))
                except (TypeError, ValueError):
                    continue
                if E.DATE.match(d) and c > 0:
                    prices[t][d] = c
            info["files"].append(os.path.basename(p))
        except (OSError, csv.Error, UnicodeError) as e:
            info["skipped"].append(f"{os.path.basename(p)}: {e}")
    return prices


# --------------------------------------------------------------------------------------- build
def build(info: dict | None = None) -> dict:
    info = info if info is not None else {}
    info.update({"files": [], "skipped": []})
    multi, single = read_signals(info)
    prices = read_closes(info)
    companies = []
    for t in sorted(single):
        md, sd, px = multi.get(t, {}), single[t], prices.get(t, {})
        days = sorted(px)
        common = sorted(d for d in set(md) & set(sd) if d >= FROM)
        rows, waiting, no_bar = [], [], []
        for d in common:
            if d not in px:
                no_bar.append(d)
                continue
            i = days.index(d)
            later = days[i + 1:i + 1 + NEED]
            if len(later) < NEED:
                waiting.append(d)
                continue
            e = px[d]
            rows.append({"date": d, "group": md[d][1], "entry": e, "closes": [px[x] for x in later], "r": [E.rnd((px[x] - e) / e * 100) for x in later],
                         "multi": md[d][0], "single": sd[d]})
        if not rows:
            continue
        merged = [r for r in rows if r["group"] == "merged"]
        groups = {"all": rows, "merged": merged}
        companies.append({
            "ticker": t, "name": E.NAMES.get(t, t), "first": rows[0]["date"], "last": rows[-1]["date"],
            "n": {"all": len(rows), "merged": len(merged), "replay": len(rows) - len(merged)},
            "left_out": {"multi_only": len([d for d in set(md) - set(sd) if d >= FROM]), "single_only": len([d for d in set(sd) - set(md) if d >= FROM]),
                         "waiting": waiting, "no_bar": no_bar},
            "counts": {"multi": len([d for d in md if d >= FROM]), "single": len([d for d in sd if d >= FROM])},
            "overall": {str(w): {g: block(rs, w) for g, rs in groups.items() if rs} for w in WINDOWS},
            "signals": {str(w): {g: by_signal(rs, w) for g, rs in groups.items() if rs} for w in WINDOWS},
            "rows": [[r["date"], r["group"], r["entry"], r["closes"], r["multi"], r["single"]]
                     + [int(call_right(r[who], r["r"], w)) for w in WINDOWS for who in ("multi", "single")] for r in rows]})
    asof = max((c["last"] for c in companies), default="")
    return {"version": 1, "asof": asof, "from": FROM, "hold_rule": HOLD_RULE, "windows": list(WINDOWS), "companies": companies,
            "sources": {"files": info["files"], "skipped": info["skipped"]}}


# --------------------------------------------------------------------------------------- command line
def summary(ac: dict) -> str:
    lines = [f"agent comparison: {len(ac['companies'])} companies, HOLD rule {ac['hold_rule']}"]
    for c in ac["companies"]:
        a, bs = c["overall"]["3"]["all"], c["signals"]["3"]["all"]["bs"]
        lines.append(f"  {c['ticker']:<10} {c['n']['all']} dates ({c['first']} to {c['last']}), BUY + SELL only: multi {bs['multi']['k']}/{bs['multi']['n']}, "
                     f"single {bs['single']['k']}/{bs['single']['n']}; with HOLD: multi {a['multi']['k']}/{a['n']}, single {a['single']['k']}/{a['n']}")
    for x in ac["sources"]["skipped"]:
        lines.append(f"  skipped: {x}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        ac = build({})
        if not ac["companies"]:
            raise ValueError("no company with both multi-agent and single-agent calls (see the file formats in this script's header)")
        print(summary(ac))
        if "--check" in argv:
            return 0
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        tmp = OUT + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:   # LF on every OS, like git stores it
            json.dump(ac, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, OUT)
        print(f"wrote {os.path.relpath(OUT, os.path.dirname(DATA))} ({os.path.getsize(OUT) // 1024} KB)")
    except Exception as e:  # a failed comparison must never stop the refresh: the old file stays
        print(f"!! agent comparison skipped, the saved agent_comparison.json is kept: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
