"""Fill storage/dse_sectors_learned.json with the sector of every DSE issue traded on the latest day.

A sector is only on a company's own DSE page, so this reads one page per company, politely (the data layer waits between
requests), and can be stopped and started again: codes that already have a sector are skipped.
Run from the repo folder, on a machine that can reach dsebd.org:

    python backend/tools/build_dse_sectors.py            # all traded issues
    python backend/tools/build_dse_sectors.py --limit 20 # a first try

Afterwards the Stocks page shows the sector chips and the Market page fills its sector breadth table.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import market, stocks   # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="stop after this many companies (0 = all)")
    a = ap.parse_args()
    codes = [r["code"] for r in stocks.listing(refresh=True)["stocks"]]
    known = market._sectors()
    todo = [c for c in codes if c not in known]
    print(f"{len(codes)} traded issues, {len(codes) - len(todo)} with a sector, {len(todo)} to fetch")
    for i, code in enumerate(todo[: a.limit or None], 1):
        try:
            snap = stocks._snapshot(code)
            sector = stocks._find(snap, "Sector") if snap and "error" not in snap else None
        except Exception as e:   # one company failing must not stop the run
            print(f"[{i}] {code}: {e}")
            continue
        stocks.remember_sector(code, sector)
        print(f"[{i}/{len(todo)}] {code}: {sector or 'no sector on the page'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
