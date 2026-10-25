"""Refresh the website data and rebuild the page.

    python backend/refresh.py                  # all tickers: top up prices, re-export, rebuild
    python backend/refresh.py AAPL IBM         # only these tickers (extra pages are still refreshed)
    python backend/refresh.py --no-top-up      # skip the yfinance price top-up
    python backend/refresh.py --no-extra       # skip the extra pages (AI analysis, memory, backtest...)
    python backend/refresh.py --build-only     # only rebuild the page from the data already exported

Each step runs as its own Python process, so a crash in one model does not take the
web server down when the backend calls this. The backend imports `steps()` and `run()`.
"""
from __future__ import annotations

import os
import subprocess
import sys

BACKEND = os.path.dirname(os.path.abspath(__file__))
FRONTEND = os.path.join(os.path.dirname(BACKEND), "frontend")
WEB = BACKEND  # exporter/ and engine/ live here
ROOT = os.environ.get("RAEM_ENGINE") or os.path.join(BACKEND, "engine")  # forecasting code + its data; fill with tools/sync_engine.py
PY = sys.executable


def all_tickers() -> list[str]:
    sys.path.insert(0, ROOT)
    import predictor_lib as pl  # noqa: E402  (only needs the ticker table)
    return list(pl.TICKERS)


def steps(tickers: list[str] | None = None, extra: bool = True, top_up: bool = True, build_only: bool = False) -> list[tuple[str, list[str]]]:
    """The commands a refresh runs, as (label, argv) pairs."""
    out: list[tuple[str, list[str]]] = []
    if not build_only:
        tks = tickers or all_tickers()
        if top_up:
            code = ("import sys; sys.path.insert(0, r'%s'); import predictor_lib as pl\n"
                    "for t in %r:\n    pl.top_up_data_csv(t, verbose=True)") % (ROOT, tks)
            out.append(("Top up prices (yfinance tickers)", [PY, "-c", code]))
        out.append((f"Forecast {len(tks)} ticker(s)", [PY, "-W", "ignore", os.path.join(WEB, "exporter", "export_ticker.py"), *tks]))
        if extra:
            out.append(("Extra pages", [PY, "-W", "ignore", os.path.join(WEB, "exporter", "export_extra.py")]))
    out.append(("Merge page sources", [PY, os.path.join(FRONTEND, "src", "merge.py")]))
    out.append(("Build page", [PY, os.path.join(FRONTEND, "src", "build.py")]))
    return out


def run(cmds: list[tuple[str, list[str]]], on_line=print) -> bool:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    for label, argv in cmds:
        on_line(f"== {label}")
        p = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        for line in p.stdout:
            on_line(line.rstrip())
        if p.wait() != 0:
            on_line(f"!! {label} failed with exit code {p.returncode}")
            return False
    return True


if __name__ == "__main__":
    args = sys.argv[1:]
    flags = {a for a in args if a.startswith("--")}
    tks = [a for a in args if not a.startswith("--")] or None
    ok = run(steps(tks, extra="--no-extra" not in flags, top_up="--no-top-up" not in flags, build_only="--build-only" in flags))
    sys.exit(0 if ok else 1)
