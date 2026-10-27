"""
Data provider layer — Financial Modeling Prep (price/fundamentals) + Finnhub
(news/sentiment), replacing tradingagents' yfinance-backed dataflow tools.

✅ WHY THIS EXISTS: `agents.py` was calling `tradingagents.agents.utils.agent_utils`'s
get_stock_data / get_fundamentals / get_balance_sheet / get_cashflow /
get_income_statement / get_indicators / get_news / get_global_news — all of
which route (via tradingagents/dataflows/interface.py) to yfinance by default.
This module swaps the DATA SOURCE to FMP + Finnhub while producing text in
the *exact same label/CSV format* tradingagents' yfinance tools did — so
agents.py's existing regex-parsing (label matching, CSV column-4 = Close,
etc.) needed zero changes. Only `_ta_utils()` / `_sentiment_tools()` in
agents.py were repointed to import from here instead.

NOT touched / still imported from the real tradingagents package (unrelated
to the data-source swap, and already legitimate working code, not a
black-box dependency the way the original notebook's was):
  - build_instrument_context, get_language_instruction
  - TraderProposal/PortfolioDecision schemas + bind_structured/
    invoke_structured_or_freetext (structured-output helpers)

Required env vars: FMP_API_KEY, FINNHUB_API_KEY.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Annotated

import numpy as np
import pandas as pd
import requests
from langchain_core.tools import tool

FMP_API_KEY = os.environ.get("FMP_API_KEY", "")
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")

FMP_BASE = "https://financialmodelingprep.com/stable"
FINNHUB_BASE = "https://finnhub.io/api/v1"


def _fmp_get(endpoint: str, params: dict, timeout: int = 15):
    r = requests.get(f"{FMP_BASE}/{endpoint}", params={**params, "apikey": FMP_API_KEY}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _finnhub_get(endpoint: str, params: dict, timeout: int = 15):
    r = requests.get(f"{FINNHUB_BASE}/{endpoint}", params={**params, "token": FINNHUB_API_KEY}, timeout=timeout)
    r.raise_for_status()
    return r.json()


# ── STOCK PRICE (format-compatible with tradingagents' get_YFin_data_online) ─
@tool
def get_stock_data(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Retrieve stock price data (OHLCV) for a given ticker symbol, via FMP."""
    try:
        data = _fmp_get("historical-price-eod/full", {"symbol": symbol, "from": start_date, "to": end_date})
        rows = data if isinstance(data, list) else data.get("historical", [])
        if not rows:
            return f"# No price data returned for {symbol} ({start_date} to {end_date})"
        rows = sorted(rows, key=lambda r: r.get("date", ""))
        # ✅ Header/column shape matches tradingagents' yfinance CSV exactly —
        # Date,Open,High,Low,Close,... — Close stays at column index 4, which
        # is what app/pipeline/memory.py and market_data.py parse by position.
        lines = [
            f"# Stock data for {symbol} from {start_date} to {end_date}",
            f"# Total records: {len(rows)}",
            f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "Date,Open,High,Low,Close,Volume",
        ]
        for r in rows:
            lines.append(f"{r.get('date','')},{r.get('open','')},{r.get('high','')},"
                         f"{r.get('low','')},{r.get('close','')},{r.get('volume','')}")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"Error retrieving stock data for {symbol}: {e}"


def _price_history_df(symbol: str, as_of_date: str, lookback_days: int = 150) -> pd.DataFrame:
    end_dt = datetime.strptime(as_of_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=lookback_days)
    data = _fmp_get("historical-price-eod/full", {
        "symbol": symbol, "from": start_dt.strftime("%Y-%m-%d"), "to": end_dt.strftime("%Y-%m-%d"),
    })
    rows = data if isinstance(data, list) else data.get("historical", [])
    if not rows:
        raise ValueError(f"no price history returned for {symbol}")
    df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    df["close"] = df["close"].astype(float)
    return df


# ── TECHNICAL INDICATORS (RSI/MACD/SMA/Bollinger computed locally via pandas) ─
@tool
def get_indicators(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator name, e.g. 'rsi', 'macd', 'close_50_sma', 'boll_ub', 'boll_lb'"],
    curr_date: Annotated[str, "The current trading date you are trading on, YYYY-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"] = 30,
) -> str:
    """Retrieve a single technical indicator for a given ticker symbol, computed
    from FMP historical price data. Call once per indicator."""
    try:
        df = _price_history_df(symbol, curr_date, lookback_days=max(150, look_back_days * 3))
        close = df["close"]
        ind = indicator.strip().lower()

        if ind == "rsi":
            delta = close.diff()
            gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
            avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
            rs = avg_gain / avg_loss.replace(0, np.nan)
            value = (100 - (100 / (1 + rs))).iloc[-1]
        elif ind == "macd":
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            value = (ema12 - ema26).iloc[-1]
        elif ind in ("close_50_sma", "50_sma", "sma_50"):
            value = close.rolling(50).mean().iloc[-1]
        elif ind in ("close_200_sma", "200_sma", "sma_200"):
            value = close.rolling(200).mean().iloc[-1]
        elif ind in ("close_10_ema", "10_ema", "ema_10"):
            value = close.ewm(span=10, adjust=False).mean().iloc[-1]
        elif ind == "boll_ub":
            sma20, std20 = close.rolling(20).mean(), close.rolling(20).std()
            value = (sma20 + 2 * std20).iloc[-1]
        elif ind in ("boll_lb",):
            sma20, std20 = close.rolling(20).mean(), close.rolling(20).std()
            value = (sma20 - 2 * std20).iloc[-1]
        elif ind == "boll":
            value = close.rolling(20).mean().iloc[-1]
        else:
            return f"Indicator '{indicator}' is not supported by the FMP-based get_indicators."

        if pd.isna(value):
            return f"{curr_date}: N/A (insufficient price history for '{indicator}')"
        # ✅ Format matches tradingagents' "YYYY-MM-DD: value" line, which is
        # what agents.py's `_extract()` regex (`\d{4}-\d{2}-\d{2}:`) expects.
        return f"{curr_date}: {round(float(value), 4)}"
    except Exception as e:  # noqa: BLE001
        return f"Error retrieving indicator '{indicator}' for {symbol}: {e}"


# ── FUNDAMENTALS (label:value lines, matching tradingagents' yfinance labels) ─
@tool
def get_fundamentals(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
) -> str:
    """Retrieve comprehensive fundamental data for a given ticker symbol, via FMP."""
    lines = []
    try:
        q = _fmp_get("quote", {"symbol": ticker})
        q = (q[0] if isinstance(q, list) and q else q) or {}
        fields = [
            ("Market Cap", q.get("marketCap")),
            ("PE Ratio (TTM)", q.get("pe")),
            ("EPS (TTM)", q.get("eps")),
            ("52 Week High", q.get("yearHigh")),
            ("52 Week Low", q.get("yearLow")),
            ("50 Day Average", q.get("priceAvg50")),
            ("200 Day Average", q.get("priceAvg200")),
        ]
        lines += [f"{label}: {value}" for label, value in fields if value is not None]
    except Exception as e:  # noqa: BLE001
        lines.append(f"(quote fetch error: {e})")

    try:
        km = _fmp_get("key-metrics-ttm", {"symbol": ticker})
        km = (km[0] if isinstance(km, list) and km else km) or {}
        fields = [
            ("Beta", km.get("beta")),
            ("Dividend Yield", km.get("dividendYieldTTM")),
        ]
        lines += [f"{label}: {value}" for label, value in fields if value is not None]
    except Exception as e:  # noqa: BLE001
        lines.append(f"(key-metrics fetch error: {e})")

    try:
        inc = _fmp_get("income-statement", {"symbol": ticker, "period": "quarter", "limit": 1})
        row = (inc[0] if isinstance(inc, list) and inc else {}) or {}
        if row.get("revenue") is not None:
            lines.append(f"Revenue (TTM): {row['revenue']}")
    except Exception:  # noqa: BLE001
        pass

    if not lines:
        return f"Error retrieving fundamentals for {ticker}: no fields returned"
    header = f"# Company Fundamentals for {ticker}\n"
    return header + "\n".join(lines)


def _financial_statement_csv(ticker: str, endpoint: str, row_map: dict, header_label: str,
                              freq: str = "quarterly", limit: int = 4) -> str:
    """Shared helper: FMP statement endpoint -> CSV text with the SAME row
    labels tradingagents' yfinance CSV used, so agents.py's `extract_numbers`
    (label substring match, per-row) keeps working unchanged."""
    try:
        period = "quarter" if freq.lower().startswith("quarter") else "annual"
        data = _fmp_get(endpoint, {"symbol": ticker, "period": period, "limit": limit})
        rows = data if isinstance(data, list) else []
        if not rows:
            raise ValueError("no data returned")
        rows = sorted(rows, key=lambda r: r.get("date", ""), reverse=True)
        dates = [r.get("date", "") for r in rows]

        lines = [f"# {header_label} data for {ticker} ({freq})",
                 f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ""]
        lines.append("," + ",".join(dates))
        for out_label, fmp_field in row_map.items():
            values = [str(r.get(fmp_field, "")) for r in rows]
            lines.append(f"{out_label}," + ",".join(values))
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"Error retrieving {header_label.lower()} for {ticker}: {e}"


@tool
def get_income_statement(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """Retrieve income statement data for a given ticker symbol, via FMP."""
    return _financial_statement_csv(ticker, "income-statement", {
        "Total Revenue": "revenue",
        "Gross Profit": "grossProfit",
        "Operating Income": "operatingIncome",
        "Net Income From Continuing": "netIncome",
        "Normalized EBITDA": "ebitda",
    }, "Income Statement", freq)


@tool
def get_balance_sheet(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """Retrieve balance sheet data for a given ticker symbol, via FMP."""
    return _financial_statement_csv(ticker, "balance-sheet-statement", {
        "Total Debt": "totalDebt",
        "Cash And Cash Equivalents": "cashAndCashEquivalents",
        "Net Debt": "netDebt",
    }, "Balance Sheet", freq)


@tool
def get_cashflow(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """Retrieve cash flow statement data for a given ticker symbol, via FMP."""
    return _financial_statement_csv(ticker, "cash-flow-statement", {
        "Free Cash Flow": "freeCashFlow",
        "Repurchase Of Capital Stock": "commonStockRepurchased",
    }, "Cash Flow", freq)


# ── NEWS (free-form text — agents.py embeds this directly into prompts, no parsing) ─
@tool
def get_news(
    ticker: Annotated[str, "Ticker symbol"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Retrieve news data for a given ticker symbol, via Finnhub."""
    try:
        items = _finnhub_get("company-news", {"symbol": ticker, "from": start_date, "to": end_date})
        if not items:
            return f"No company news found for {ticker} between {start_date} and {end_date}."
        items = sorted(items, key=lambda x: x.get("datetime", 0), reverse=True)[:10]
        lines = [f"# News for {ticker} ({start_date} to {end_date})"]
        for it in items:
            dt = datetime.utcfromtimestamp(it.get("datetime", 0)).strftime("%Y-%m-%d")
            lines.append(f"- [{dt}] {it.get('headline','')} ({it.get('source','')}) — {str(it.get('summary',''))[:200]}")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"Error retrieving news for {ticker}: {e}"


@tool
def get_global_news(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int | None, "unused for Finnhub general news"] = None,
    limit: Annotated[int | None, "Max articles to return"] = None,
) -> str:
    """Retrieve global market news, via Finnhub."""
    try:
        items = _finnhub_get("news", {"category": "general"})
        if not items:
            return "No global market news available."
        items = sorted(items, key=lambda x: x.get("datetime", 0), reverse=True)[:(limit or 10)]
        lines = ["# Global market news"]
        for it in items:
            dt = datetime.utcfromtimestamp(it.get("datetime", 0)).strftime("%Y-%m-%d")
            lines.append(f"- [{dt}] {it.get('headline','')} ({it.get('source','')})")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"Error retrieving global news: {e}"


# ── SENTIMENT (free-form text, no parsing downstream) ─────────────────────
@tool
def get_stock_news_sentiment(
    ticker: Annotated[str | None, "ticker symbol"] = None,
    symbol: Annotated[str | None, "ticker symbol (alternate arg name)"] = None,
    curr_date: Annotated[str | None, "current date, unused"] = None,
    start_date: Annotated[str | None, "unused"] = None,
) -> str:
    """Company news sentiment and buzz stats, via Finnhub. Falls back to a
    plain-text notice if the account doesn't have Finnhub's paid
    /news-sentiment endpoint — the Sentiment Analyst then infers sentiment
    from the news report instead (existing behavior, unchanged)."""
    t = ticker or symbol
    if not t:
        return "No ticker provided for sentiment lookup."
    try:
        data = _finnhub_get("news-sentiment", {"symbol": t})
        buzz, sent = data.get("buzz", {}), data.get("sentiment", {})
        return (
            f"companyNewsScore={data.get('companyNewsScore')}, "
            f"bullishPercent={sent.get('bullishPercent')}, bearishPercent={sent.get('bearishPercent')}, "
            f"articlesInLastWeek={buzz.get('articlesInLastWeek')}, buzz={buzz.get('buzz')}"
        )
    except requests.exceptions.HTTPError as e:
        code = getattr(e.response, "status_code", None)
        if code in (401, 403):
            return "Finnhub /news-sentiment requires a paid plan on this API key — not available on free tier."
        return f"Error fetching sentiment for {t}: {e}"
    except Exception as e:  # noqa: BLE001
        return f"Error fetching sentiment for {t}: {e}"
