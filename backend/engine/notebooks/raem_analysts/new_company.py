"""Make a notebook for any company from an existing one.

    python -m raem_analysts.new_company --ticker TSLA --group international
    python -m raem_analysts.new_company --ticker SQURPHARMA --group dse --name "Square Pharmaceuticals Limited" --sector "Health Care (Pharmaceuticals)"

Run it from the notebooks folder (the one that holds raem_analysts/).  It copies a template notebook of the same group,
gives it a new COMPANY PROFILE and clears the old outputs.  The analyst code is not copied: the new notebook uses the same
shared files as every other notebook of the group, so a fix made once in raem_analysts/ reaches every company.

Options
    --like TICKER     template company (default: AAPL for international, ABBANK for dse)
    --log NAME        name of the trading-log files, trading_log_<NAME>.json (default: the ticker without dots)
    --name TEXT       full company name (dse: used for the web-news search)
    --sector TEXT     dse only: sector line, e.g. "Health Care (Pharmaceuticals)"
    --out FILE        notebook file name (default: <ticker>.ipynb, <ticker>-dse.ipynb for dse)
    --folder DIR      notebooks folder (default: the folder that holds raem_analysts/)
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LIKE = {"international": "AAPL", "dse": "ABBANK"}


def _profile_cell_source(profile: dict) -> str:
    group = profile["group"]
    return "\n".join([
        "# ======================================================================",
        "# COMPANY PROFILE -- the only company-specific part of this notebook.",
        f"# The analysts, debate, trader, risk and verifier code is shared by every {group} notebook: raem_analysts/{group}/",
        "# To change a company-specific value (ticker, log name, peer list, sector text), edit it here.",
        "# ======================================================================",
        f'COMPANY = "{profile["TICKER"]}"',
        "RAEM_PROFILE = " + json.dumps(profile, ensure_ascii=False, indent=4),
        "",
        "import os, sys",
        "sys.path.insert(0, os.getcwd())   # raem_analysts/ sits next to the notebooks",
        "from raem_analysts import use_shared",
    ])


def _lines(text: str) -> list[str]:
    ls = text.split("\n")
    return [x + "\n" for x in ls[:-1]] + [ls[-1]]


def find_template(folder: str, like: str, group: str):
    sys.path.insert(0, folder)
    from raem_analysts import profile_of
    for path in sorted(glob.glob(os.path.join(folder, "*.ipynb"))):
        try:
            nb = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        srcs = ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]
        prof = profile_of(srcs)
        if prof and prof["group"] == group and like in (prof["TICKER"], os.path.basename(path), os.path.basename(path)[:-6]):
            return path, nb, prof
    raise SystemExit(f"No {group} notebook with a COMPANY PROFILE for {like!r} in {folder}. Use --like with one of the existing companies.")


def derive(profile: dict, ticker: str, log: str, name: str | None, sector: str | None) -> dict:
    old_t, old_f, old_l = profile["TICKER"], profile["FILE"], profile["LOG"]
    new = {"group": profile["group"], "TICKER": ticker, "LOG": log, "FILE": ticker.replace(".", "_"), "slots": {}}
    for key, lines in profile.get("slots", {}).items():
        out = []
        for ln in lines:
            ln = ln.replace(f"trading_log_{old_l}", f"trading_log_{log}")
            ln = ln.replace(f"{old_f}_forecasts", f"{new['FILE']}_forecasts").replace(f"{old_t}_forecasts", f"{new['FILE']}_forecasts")
            ln = re.sub(r'^(\s*)"%s":' % re.escape(old_t), r'\1"%s":' % ticker, ln)
            if sector:
                ln = re.sub(r"Sector: [^|\"]+\|", f"Sector: {sector} |", ln)
            if name:
                ln = re.sub(r'(company_hint: str = )"[^"]*"', lambda m: m.group(1) + json.dumps(name), ln)
            out.append(ln)
        new["slots"][key] = out
    return new


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--group", required=True, choices=["international", "dse"])
    ap.add_argument("--like")
    ap.add_argument("--log")
    ap.add_argument("--name")
    ap.add_argument("--sector")
    ap.add_argument("--out")
    ap.add_argument("--folder", default=os.path.dirname(HERE))
    a = ap.parse_args(argv)
    ticker = a.ticker.strip()
    if not re.fullmatch(r"[A-Za-z0-9.\-]+", ticker):
        raise SystemExit("The ticker may only hold letters, digits, dots and dashes.")
    folder = os.path.abspath(a.folder)
    path, nb, prof = find_template(folder, a.like or DEFAULT_LIKE[a.group], a.group)
    log = a.log or re.sub(r"[^A-Za-z0-9]", "", ticker).upper()
    out_name = a.out or (f"{ticker.replace('.', '_')}.ipynb" if a.group == "international" else f"{ticker.lower()}-dse.ipynb")
    out_path = os.path.join(folder, out_name)
    if os.path.exists(out_path):
        raise SystemExit(f"{out_path} already exists; give another --out or delete it first.")
    profile = derive(prof, ticker, log, a.name, a.sector)
    for c in nb["cells"]:
        if c["cell_type"] == "code":
            if "COMPANY PROFILE" in "".join(c["source"])[:200]:
                c["source"] = _lines(_profile_cell_source(profile))
            c["outputs"], c["execution_count"] = [], None
    json.dump(nb, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"Created {out_path}  (copied from {os.path.basename(path)})")
    print("Next steps:")
    print(f"  1. Add {ticker} to TICKERS in backend/engine/predictor_lib.py (name, currency, source) and put its price CSV in engine/data/.")
    print(f"  2. Open {out_name} and check the COMPANY PROFILE cell (peer list for international, sector text for dse).")
    print(f"  3. Run it, or run `python backend/refresh.py {ticker}` to export the forecast for the website.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
