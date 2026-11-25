"""Put one company's multi-agent replay, single-agent log and closing prices into backend/data/evaluation_inputs/agent_comparison/,
the drop-in folder the "Single vs multi agent" part of the Evaluation page reads (app/pipeline/export_agent_comparison.py).

    python backend/tools/import_agent_comparison.py --ticker BPML \\
        --replay-log  <replay folder>/trading_log_BPML.json \\
        --single-log  <single-agent folder>/replay_single_out/single_agent_log_BPML.json \\
        --market-file <single-agent folder>/bpml_2y_ext.json
    python backend/refresh.py --build-only        # score again and rebuild the page

Run it once per company; the company's old rows in the three files are replaced, the other companies stay. Writes (CSV, UTF-8):
  multi_replay_signals.csv   ticker, trade_date, signal   finished sessions of the replay log (session_end and a BUY / HOLD / SELL signal)
  single_agent_signals.csv   ticker, trade_date, signal   the single agent's call per date (rows with an error are skipped)
  closes.csv                 ticker, date, close          real bars of the market file from --since on (a day with no trade is not a bar); a day that lies inside
                                                          the file's range but has NO row at all is filled from evaluation_inputs/closes_merged_dataset.csv when it has it
The merged dataset itself (signals_merged_dataset.csv) is read in place. Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INPUTS = os.environ.get("RAEM_EVAL_INPUTS") or os.path.join(BACKEND, "data", "evaluation_inputs")
DEST = os.environ.get("RAEM_AC_INPUTS") or os.path.join(INPUTS, "agent_comparison")
SIGNALS = ("BUY", "HOLD", "SELL")


def replace_rows(path: str, header: list, ticker: str, rows: list) -> int:
    """Rewrite path: every row of other tickers stays, the ticker's rows become `rows`."""
    keep = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8-sig", newline="") as fh:
            keep = [r for r in csv.reader(fh)][1:]
        keep = [r for r in keep if r and r[0].upper() != ticker]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(sorted(keep + rows, key=lambda r: (r[0], r[1])))
    return len(rows)


def replay_signals(path: str, ticker: str) -> list:
    out = {}
    for s in json.load(open(path, encoding="utf-8")):
        sig = str((s.get("summary_table") or {}).get("final_signal") or "").upper()
        if s.get("session_end") and sig in SIGNALS and str(s.get("company", ticker)).upper() == ticker:
            out[str(s["trade_date"])[:10]] = sig
    return [[ticker, d, sig] for d, sig in sorted(out.items())]


def single_signals(path: str, ticker: str) -> list:
    out = {}
    for r in json.load(open(path, encoding="utf-8")):
        sig = str(r.get("signal") or "").upper()
        if sig in SIGNALS and not r.get("error") and str(r.get("company", ticker)).upper() == ticker:
            out[str(r["trade_date"])[:10]] = sig
    return [[ticker, d, sig] for d, sig in sorted(out.items())]


def closes(path: str, ticker: str, since: str) -> tuple[list, list]:
    tech = json.load(open(path, encoding="utf-8"))["technical"]
    have = {str(r["date"])[:10] for r in tech}                      # every day with a row, a no-trade row included
    out = {str(r["date"])[:10]: float(r["close"]) for r in tech if r.get("close") and not r.get("no_trade") and str(r["date"])[:10] >= since}
    filled = []
    fill = os.path.join(INPUTS, "closes_merged_dataset.csv")
    if os.path.isfile(fill) and have:
        lo, hi = min(have), max(have)
        with open(fill, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                d = str(r.get("date", ""))[:10]
                if str(r.get("ticker", "")).upper() == ticker and max(lo, since) <= d <= hi and d not in have:
                    try:
                        out[d] = float(r["close"]); filled.append(d)
                    except (TypeError, ValueError):
                        pass
    return [[ticker, d, c] for d, c in sorted(out.items())], sorted(filled)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--replay-log", required=True, help="multi-agent replay session log (trading_log_<T>.json)")
    ap.add_argument("--single-log", required=True, help="single-agent log (single_agent_log_<T>.json)")
    ap.add_argument("--market-file", required=True, help="market file with a technical list (date, close, no_trade)")
    ap.add_argument("--since", default="2026-03-01", help="first date of closes to keep (default 2026-03-01)")
    a = ap.parse_args()
    t = a.ticker.upper()
    for p in (a.replay_log, a.single_log, a.market_file):
        if not os.path.isfile(p):
            sys.exit(f"not found: {p}")
    m = replace_rows(os.path.join(DEST, "multi_replay_signals.csv"), ["ticker", "trade_date", "signal"], t, replay_signals(a.replay_log, t))
    s = replace_rows(os.path.join(DEST, "single_agent_signals.csv"), ["ticker", "trade_date", "signal"], t, single_signals(a.single_log, t))
    rows, filled = closes(a.market_file, t, a.since)
    c = replace_rows(os.path.join(DEST, "closes.csv"), ["ticker", "date", "close"], t, rows)
    print(f"{t}: {m} multi-agent replay signals, {s} single-agent signals, {c} closes -> {os.path.relpath(DEST, os.path.dirname(BACKEND))}")
    if filled:
        print(f"  {len(filled)} close(s) the market file has no row for were filled from closes_merged_dataset.csv: {', '.join(filled)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
