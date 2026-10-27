"""
DSE OHLCV price data + technical indicators.

Built on `bdshare` (https://github.com/rochi88/bdshare, MIT-licensed, PyPI:
`pip install bdshare`) rather than a hand-rolled scraper against dsebd.org.
bdshare is an actively maintained package purpose-built for DSE with its own
retry/backoff and dual-host fallback (dsebd.org + an alt host), so it's a
more reliable foundation than guessing the page structure ourselves -- the
earlier draft of the DSE vendor (dse_fundamentals.py / dse_news.py) had to
guess at dsebd.org's HTML because it couldn't be fetched live for
verification; bdshare's maintainers have already done that work.

Install: pip install bdshare stockstats

Matches the get_stock_data / get_indicators contract from y_finance.py /
interface.py, so this registers as a "dse" vendor for the core_stock_apis /
technical_indicators categories.
"""

from datetime import datetime

import pandas as pd
from bdshare import BDShareError, get_historical_data
from dateutil.relativedelta import relativedelta
from stockstats import wrap

from .errors import NoMarketDataError

# How much extra trailing history to fetch beyond the display window so a
# 200 SMA (or any indicator needing a long warm-up) has enough prior rows to
# compute correctly for the *first* displayed date, not just the last one.
_INDICATOR_WARMUP_DAYS = 250

_COLUMN_MAP = {
    "date": "Date", "open": "Open", "high": "High",
    "low": "Low", "close": "Close", "volume": "Volume",
}


def _fetch_ohlcv(symbol: str, start: str, end: str) -> pd.DataFrame:
    try:
        df = get_historical_data(start, end, symbol.upper())
    except BDShareError as e:
        raise NoMarketDataError(symbol, symbol.upper(), str(e)) from e

    if df is None or df.empty:
        raise NoMarketDataError(symbol, symbol.upper(), f"no rows between {start} and {end}")

    df = df.sort_index(ascending=True).reset_index().rename(columns=_COLUMN_MAP)
    for col in ("Open", "High", "Low", "Close"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def get_stock_data(symbol: str, start_date: str, end_date: str) -> str:
    """OHLCV for a DSE ticker, formatted like the yfinance/alpha_vantage vendors."""
    df = _fetch_ohlcv(symbol, start_date, end_date)
    keep = [c for c in ("Date", "Open", "High", "Low", "Close", "Volume") if c in df.columns]
    out = df[keep].copy()
    for col in ("Open", "High", "Low", "Close"):
        if col in out.columns:
            out[col] = out[col].round(2)

    csv_string = out.to_csv(index=False)
    header = (
        f"# Stock data for {symbol.upper()} (DSE) from {start_date} to {end_date}\n"
        f"# Total records: {len(out)}\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    return header + csv_string


# Condensed versions of the same explanations y_finance.py's
# get_stock_stats_indicators_window uses, so the agent gets equivalent
# guidance regardless of which vendor (yfinance vs dse) served the request.
_INDICATOR_NOTES = {
    "close_50_sma": "50 SMA: medium-term trend indicator; lags price, use for dynamic support/resistance.",
    "close_200_sma": "200 SMA: long-term trend benchmark; confirms golden/death cross setups.",
    "close_10_ema": "10 EMA: responsive short-term average; captures momentum shifts, prone to noise.",
    "macd": "MACD: momentum via EMA differences; watch crossovers and divergence for trend changes.",
    "macds": "MACD Signal: EMA smoothing of MACD; crossovers with MACD line trigger trades.",
    "macdh": "MACD Histogram: gap between MACD and its signal; shows momentum strength/divergence.",
    "rsi": "RSI: overbought/oversold via 70/30 thresholds; watch divergence for reversals.",
    "boll": "Bollinger Middle: 20 SMA basis for the bands; dynamic benchmark for price.",
    "boll_ub": "Bollinger Upper Band: ~2 std dev above middle; potential overbought/breakout zone.",
    "boll_lb": "Bollinger Lower Band: ~2 std dev below middle; potential oversold zone.",
    "atr": "ATR: average true range; use for stop-loss levels and position sizing.",
    "vwma": "VWMA: volume-weighted moving average; confirms trend with volume data.",
    "mfi": "MFI: price+volume momentum; >80 overbought, <20 oversold, confirm with RSI/MACD.",
}


def get_indicators(symbol: str, indicator: str, curr_date: str, look_back_days: int) -> str:
    if indicator not in _INDICATOR_NOTES:
        raise ValueError(
            f"Indicator {indicator} is not supported. Choose from: {list(_INDICATOR_NOTES)}"
        )

    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before = curr_dt - relativedelta(days=look_back_days)
    fetch_start = (before - relativedelta(days=_INDICATOR_WARMUP_DAYS)).strftime("%Y-%m-%d")

    df = _fetch_ohlcv(symbol, fetch_start, curr_date)
    df["Date"] = pd.to_datetime(df["Date"])
    stats = wrap(df)
    stats[indicator]  # trigger stockstats to compute the column
    stats["Date"] = pd.to_datetime(stats["Date"]).dt.strftime("%Y-%m-%d")

    lines = []
    d = curr_dt
    while d >= before:
        ds = d.strftime("%Y-%m-%d")
        row = stats[stats["Date"] == ds]
        value = row[indicator].values[0] if not row.empty else "N/A: Not a trading day (weekend or holiday)"
        lines.append(f"{ds}: {value}")
        d -= relativedelta(days=1)

    return (
        f"## {indicator} values from {before.strftime('%Y-%m-%d')} to {curr_date} (DSE):\n\n"
        + "\n".join(lines) + "\n\n" + _INDICATOR_NOTES[indicator]
    )
