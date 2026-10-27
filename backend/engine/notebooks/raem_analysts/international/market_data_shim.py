# raem_analysts/international/market_data_shim.py -- shared by AAPL, IBM, 0300_HK, 2222_SR
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 📡 MARKET-DATA SHIM -- same interface as the DSE data layer, backed by yfinance
# ================================================================
import pandas as pd
from datetime import datetime, timedelta

_YF_OHLCV_CACHE = {}
_YF_SECTOR_CACHE = {}


def fetch_dse_ohlcv_range(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Daily OHLCV for `symbol` between start_date and end_date (inclusive), ascending, tz-naive Date index."""
    key = (symbol, start_date, end_date)
    if key in _YF_OHLCV_CACHE:
        return _YF_OHLCV_CACHE[key]
    empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    try:
        import yfinance as yf
        end_excl = (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        df = yf.Ticker(symbol).history(start=start_date, end=end_excl, auto_adjust=False)
        if df is None or df.empty:
            df = empty
        else:
            df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
            df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
            df = df[~df.index.duplicated(keep="last")].sort_index()
    except Exception as e:
        print(f"   [WARN] yfinance OHLCV fetch failed for {symbol}: {str(e)[:100]}")
        df = empty
    _YF_OHLCV_CACHE[key] = df
    return df


def fetch_dse_company_snapshot(symbol: str) -> dict:
    """Only the 'Sector' key is used by the v11 cells (peer-graph sector edges)."""
    if symbol in _YF_SECTOR_CACHE:
        return _YF_SECTOR_CACHE[symbol]
    sector = "Unknown"
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).info or {}
        sector = (info.get("sector") or info.get("industry") or "Unknown").strip() or "Unknown"
    except Exception:
        pass
    _YF_SECTOR_CACHE[symbol] = {"Sector": sector}
    return _YF_SECTOR_CACHE[symbol]


print("✅ yfinance market-data shim ready (fetch_dse_ohlcv_range / fetch_dse_company_snapshot)")

