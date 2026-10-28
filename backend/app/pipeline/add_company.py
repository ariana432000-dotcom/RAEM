"""Add a company to the site: prices, forecast and page data, for any ticker.

    python app/pipeline/add_company.py --ticker TSLA
    python app/pipeline/add_company.py --ticker 7203.T --name "Toyota Motor"
    python app/pipeline/add_company.py --ticker SQURPHARMA --market dse --name "Square Pharmaceuticals Limited"

Steps (each prints a "== " line the website shows as progress; every step can be repeated safely):
    1. Prices         2 years of daily prices into engine/data/<Name>_<TICKER>_2y.csv (yfinance, or the Dhaka Stock Exchange)
    2. Register       backend/data/companies.json, so forecast_lib and the exporters know the company
    3. Forecast       app/pipeline/export_ticker.py  ->  data/tickers/<TICKER>.json
    4. Page data      app/pipeline/export_extra.py --only <TICKER>  (price table, backtest, FX)
The AI analysis (app/ai_engine/run_pipeline.py) needs none of this: it runs for any ticker, on the page's AI Analysis tab.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
ENGINE = os.environ.get("RAEM_ENGINE") or os.path.join(BACKEND, "engine")
TICKERS_DIR = os.path.join(BACKEND, "data", "tickers")
MIN_ROWS = 260          # about a year of trading days: the models need a training window and a holdout
PY = sys.executable

sys.path.insert(0, ENGINE)
import raem_companies as rc  # noqa: E402

DSE_COLUMNS = ["date", "symbol", "ltp", "high", "low", "open", "close", "ycp", "trade", "value", "volume"]


def step(label: str) -> None:
    print(f"== {label}", flush=True)


def fail(msg: str, code: int = 1):
    print(f"!! {msg}", flush=True)
    sys.exit(code)


# ---------------------------------------------------------------- data sources (replaced by fakes in the tests)

def fetch_international(ticker: str):
    """(prices, info) from yfinance: prices has Open/High/Low/Close/Volume, a tz-aware date index; info is a dict (may be empty)."""
    import yfinance as yf
    tk = yf.Ticker(ticker)
    df = tk.history(period="2y", auto_adjust=True)
    info = {}
    try:
        raw = tk.info or {}
        info = {"name": raw.get("longName") or raw.get("shortName"), "currency": raw.get("currency"),
                "tz": raw.get("exchangeTimezoneName"), "sector": raw.get("sector")}
    except Exception:  # Yahoo's info call fails now and then; the history alone is enough
        pass
    return df, info


def fetch_dse(ticker: str):
    """Two years of daily prices of a Dhaka Stock Exchange code, as a frame with the DSE_COLUMNS (from old.dsebd.org:
    www.dsebd.org and bdshare's dse.com.bd answer 410 Gone)."""
    import dse_archive
    df = dse_archive.history(ticker, date.today() - timedelta(days=740), date.today())
    return _dse_frame(df.reset_index())


def _dse_frame(df):
    import pandas as pd
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    if "symbol" not in df.columns:
        df["symbol"] = ""
    for c in DSE_COLUMNS[2:]:
        df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", ""), errors="coerce") if c in df.columns else float("nan")
    return df[DSE_COLUMNS].dropna(subset=["close"]).sort_values("date").drop_duplicates("date")


# ---------------------------------------------------------------- the steps

def csv_for(ticker: str):
    hits = glob.glob(os.path.join(ENGINE, "data", f"*_{ticker.replace('.', '_')}_2y.csv"))
    return hits[0] if hits else None


def save_prices(ticker: str, market: str, name: str | None):
    """Download and save the price CSV (skipped when it is already there). Returns (market, meta-info dict)."""
    existing = csv_for(ticker)
    saved = rc.load().get(ticker)
    if existing and saved:
        print(f"   prices already saved: {os.path.basename(existing)}", flush=True)
        return saved.get("group", "international"), {"name": saved.get("name"), "currency": saved.get("currency"), "tz": saved.get("tz")}
    if existing:  # a CSV from an earlier try that stopped before the company was registered: keep it, the header tells the market
        print(f"   prices already saved: {os.path.basename(existing)}", flush=True)
        head = open(existing, encoding="utf-8").readline()
        return ("international" if head.startswith("Date,") else "dse"), {}

    tried = []
    for m in (["international", "dse"] if market == "auto" else [market]):
        try:
            if m == "international":
                df, info = fetch_international(ticker)
                rows = 0 if df is None else len(df.dropna(subset=["Close"])) if len(df) else 0
            else:
                df, info = fetch_dse(ticker), {}
                rows = len(df)
        except Exception as e:
            tried.append(f"{m}: {type(e).__name__}: {e}")
            continue
        if rows >= MIN_ROWS:
            slug = rc.file_slug(name or info.get("name") or ticker)
            path = os.path.join(ENGINE, "data", f"{slug}_{ticker.replace('.', '_')}_2y.csv")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            if m == "international":
                df.dropna(subset=["Close"]).to_csv(tmp)          # yfinance layout: Date,Open,High,Low,Close,Volume,Dividends,Stock Splits
                with open(tmp, encoding="utf-8") as fh:
                    first = fh.readline()
                if not first.startswith("Date,"):                 # the index has no name in some yfinance versions
                    body = open(tmp, encoding="utf-8").read().split("\n", 1)[1]
                    open(tmp, "w", encoding="utf-8", newline="\n").write("Date," + first.split(",", 1)[1] + "\n" + body)
            else:
                df.to_csv(tmp, index=False)                      # DSE layout: date,symbol,ltp,high,low,open,close,...
            os.replace(tmp, path)
            print(f"   {rows} trading days saved to {os.path.basename(path)} ({m})", flush=True)
            return m, info
        tried.append(f"{m}: {rows} days of prices (need {MIN_ROWS})")
    fail(f"No usable price history for {ticker}. Tried {'; '.join(tried) or 'nothing'}. "
         "Check the ticker (Yahoo format, e.g. TSLA, 7203.T, 0700.HK) or choose the Dhaka Stock Exchange for a DSE code.")


def register(ticker: str, market: str, name: str | None, info: dict) -> dict:
    saved = rc.load().get(ticker)
    if saved:
        return saved
    meta = rc.meta_for(ticker, market, name or info.get("name"), info.get("currency"), info.get("tz"))
    rc.add(ticker, meta)
    print(f"   registered {ticker}: {meta['name']} ({meta['currency']}, {meta['tz']})", flush=True)
    return meta


def run_script(label: str, argv: list[str]) -> None:
    p = subprocess.Popen(argv, cwd=ENGINE, env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1"),
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    for line in p.stdout:
        print(line.rstrip(), flush=True)
    if p.wait() != 0:
        fail(f"{label} failed (exit code {p.returncode}). Run it again to retry from this step.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--market", default="auto", choices=["auto", "international", "dse"])
    ap.add_argument("--name")
    ap.add_argument("--sector", help="accepted for compatibility (the AI analysis finds the sector itself)")
    a = ap.parse_args(argv)
    try:
        t = rc.normalize(a.ticker)
    except ValueError as e:
        fail(str(e))
    if os.path.exists(os.path.join(TICKERS_DIR, f"{t}.json")):
        step(f"{t} is already on the site")
        return 0

    step(f"Prices for {t}")
    market, info = save_prices(t, a.market, a.name)
    step("Register the company")
    meta = register(t, market, a.name, info)
    step(f"Forecast {t} (several minutes)")
    run_script("The forecast", [PY, "-W", "ignore", os.path.join(HERE, "export_ticker.py"), t])
    if not os.path.exists(os.path.join(TICKERS_DIR, f"{t}.json")):
        fail("The forecast finished without a result file. See the log above.")
    step("Page data")
    run_script("The page data export", [PY, "-W", "ignore", os.path.join(HERE, "export_extra.py"), "--only", t])
    step(f"{t} added")
    return 0


if __name__ == "__main__":
    sys.exit(main())
