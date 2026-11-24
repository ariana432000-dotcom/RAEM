"""The last forecast run of each company, kept as CSV files so the page can load it again (standard library only).

    backend/data/forecasts/<T>_summary.csv   key,value   last close, price data date, daily rows, signal, run time ...
    backend/data/forecasts/<T>_prices.csv    date,close  the whole price history the models used (up to 2 years)
    backend/data/forecasts/<T>_forecast.csv  model,day,date,price,low,high   every model's 7-day path; the row "Reviewed"
                                             is the main forecast with its 10-90% range in low / high

write() is called by export_ticker.py after every run (a failure never stops the run); read() serves
GET /api/forecast/saved/<T>. The newest run overwrites the files, so a reload shows the last run.
"""
from __future__ import annotations

import csv
import os

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIR = os.path.join(BACKEND, "data", "forecasts")


def _p(t: str, kind: str) -> str:
    return os.path.join(DIR, f"{t.replace('.', '_')}_{kind}.csv")


def _atomic(path: str, header: list, rows: list) -> None:
    os.makedirs(DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    os.replace(tmp, path)


def _num(v):
    try:
        v = float(v)
        return None if v != v else v
    except (TypeError, ValueError):
        return None


def write(t: str, out: dict, prices: list) -> None:
    """out = the run's result (the same dict export_ticker saves as JSON); prices = [(date, close), ...] in date order."""
    f, daily = out["forecast"], out.get("daily") or {}
    plan = (out.get("agent") or {}).get("plan") or {}
    last = f["last_price"]
    rows = []
    for m, path in f["paths"].items():
        for i, (d, v) in enumerate(zip(f["dates"], path)):
            rows.append([m, i + 1, d, v, "", ""])
    pr, br = plan.get("path_ret"), plan.get("band_ret")
    if pr:
        for i, (d, r) in enumerate(zip(f["dates"], pr)):
            lo, hi = (last * (1 + r + br[i][0]), last * (1 + r + br[i][1])) if br else ("", "")
            rows.append(["Reviewed", i + 1, d, last * (1 + r), lo, hi])
    _atomic(_p(t, "forecast"), ["model", "day", "date", "price", "low", "high"], rows)
    _atomic(_p(t, "prices"), ["date", "close"], [[d, c] for d, c in prices])
    run = out.get("run_info") or {}
    summary = [["ticker", t], ["company", f.get("company", "")], ["currency", f.get("currency", "")], ["last_close", last],
               ["last_date", f["last_date"]], ["daily_rows", f.get("n_rows", len(prices))], ["history_rows", len(prices)],
               ["models_fitted", sum(1 for m in f["paths"] if m not in ("Ensemble", "Fusion", "Fusion-Top3"))],
               ["failed_models", ",".join((f.get("failed_models") or {}).keys())], ["best_model", f.get("best_model", "")],
               ["main_forecast", "Reviewed" if pr else "Ensemble"], ["direction", f.get("direction", "")],
               ["total_return_pct", f.get("total_return_pct", "")], ["final_price", f.get("final_price", "")],
               ["signal", daily.get("final_signal", "")], ["run_models", ",".join(run.get("models") or [])],
               ["generated_at", run.get("generated_at", "")]]
    _atomic(_p(t, "summary"), ["key", "value"], summary)


def engine_prices(t: str) -> list:
    """[(date, close)] from the company's price CSV in engine/data (yfinance layout Date,...,Close or the exchange's date,...,close)."""
    import glob
    hits = glob.glob(os.path.join(BACKEND, "engine", "data", f"*_{t.replace('.', '_')}_2y.csv"))
    if not hits:
        return []
    rows = []
    with open(hits[0], newline="", encoding="utf-8") as fh:
        r = csv.DictReader(fh)
        cols = {c.lower(): c for c in (r.fieldnames or [])}
        dc, cc = cols.get("date"), cols.get("close")
        if not dc or not cc:
            return []
        for x in r:
            v = _num(x[cc])
            if v is not None and x[dc]:
                rows.append((x[dc][:10], v))
    return sorted(set(rows))


def backfill(t: str, out: dict) -> bool:
    """Write the CSV files from a saved ticker JSON (a run made before the CSV files existed). True when written."""
    try:
        prices = engine_prices(t) or [tuple(h) for h in out.get("history") or []]
        write(t, out, prices)
        return True
    except (OSError, KeyError, ValueError):
        return False


def exists(t: str) -> bool:
    return all(os.path.exists(_p(t, k)) for k in ("summary", "prices", "forecast"))


def read(t: str) -> dict | None:
    """The last saved run, or None when no CSV files exist for this company yet."""
    if not exists(t):
        return None
    with open(_p(t, "summary"), newline="", encoding="utf-8") as fh:
        summary = {r["key"]: r["value"] for r in csv.DictReader(fh)}
    for k in ("last_close", "daily_rows", "history_rows", "models_fitted", "total_return_pct", "final_price"):
        summary[k] = _num(summary.get(k))
    with open(_p(t, "prices"), newline="", encoding="utf-8") as fh:
        prices = [[r["date"], _num(r["close"])] for r in csv.DictReader(fh)]
    paths: dict[str, list] = {}
    dates: list[str] = []
    band: list | None = None
    with open(_p(t, "forecast"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            paths.setdefault(r["model"], []).append(_num(r["price"]))
            if r["model"] == "Reviewed":
                band = (band or []) + [[_num(r["low"]), _num(r["high"])]]
            if len(dates) < int(r["day"]):
                dates.append(r["date"])
    if band and any(b[0] is None for b in band):
        band = None
    return {"summary": summary, "prices": prices, "dates": dates, "paths": paths, "band": band}
