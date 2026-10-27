# raem_analysts/dse/macro_regime_analyst.py -- shared by abbank-dse, batbc-dse, bpml-dse, waltonhil-dse
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 🌍 MACRO REGIME ANALYST — DSE-adapted (was US: VIX / 10Y Treasury / DXY)
# ================================================================
# আগে এটা US market indicators ব্যবহার করতো, যেগুলো DSE-ভিত্তিক ট্রেডিং-এর
# জন্য প্রাসঙ্গিক না। এখন DSE-নিজস্ব ডেটা দিয়ে replace করা হয়েছে:
#   - VIX-এর জায়গায়: DSEX index-এর নিজের realized volatility (DSE-তে কোনো
#     implied-vol index নেই, তাই এটাই সবচেয়ে কাছাকাছি প্রক্সি)
#   - 10Y Treasury yield ("rates rising/falling")-এর জায়গায়: DSEX
#     index-এর trend/momentum -- আসলে DSE pipeline-এর জন্য এটা Fed rate-এর
#     চেয়ে বেশি প্রাসঙ্গিক
#   - DXY-এর জায়গায়: USD/BDT exchange rate (Taka strength/weakness প্রক্সি)
#
# ⚠️ UNVERIFIED FROM THIS SANDBOX (network to both dsebd.org and Yahoo
# Finance is blocked here, so neither of these could be test-run):
#   1) DSEX_SYMBOL below assumes DSE's day_end_archive.php returns a row
#      for "DSEX" when queried as an `inst` -- it may not (that endpoint
#      is built for individual securities, not the composite index). If
#      empty, fetch_dsex_snapshot() falls back to all-None cleanly (same
#      pattern as everywhere else in this notebook) -- watch for the
#      [DEBUG] print below when you run this.
#   2) USDBDT_TICKER assumes yfinance covers "USDBDT=X" -- BDT is a thin,
#      managed-float currency and some yfinance FX crosses for it don't
#      resolve. Falls back to None the same way if yf.download() returns
#      an empty frame.
# If either comes back empty, tell me the [DEBUG] output and I'll find a
# working data source for that piece specifically.

%pip install yfinance bdshare -q

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from tradingagents.agents.utils.agent_utils import get_language_instruction

llm_macro = ChatOllama(model="qwen2.5:7b", base_url="http://localhost:11434", num_ctx=8192, temperature=0, num_predict=_cap('macro') or 2048, repeat_penalty=1.15)

DSEX_SYMBOL = "DSEX"        # DSE composite index -- reuses fetch_dse_ohlcv_range from the DSE data layer cell above
USDBDT_TICKER = "USDBDT=X"  # yfinance ticker for USD/BDT -- unverified, see note above


def fetch_dsex_snapshot(as_of_date: str, lookback_days: int = 60) -> dict:
    """DSEX index level + realized volatility + short-vs-long-term trend.

    CONFIRMED: DSE's own day_end_archive.php (inst=DSEX) returns nothing --
    that endpoint is built for individual securities, not the composite
    index. Switched to the bdshare library instead, which is
    purpose-built for DSE data and has a dedicated
    get_market_inf_more_data(start, end) function for historical index
    values (DSEX/DS30/DSES) -- this is a different, more promising
    function than bdshare's own get_dsex_data(), which based on its
    published record counts looks like it returns per-constituent-stock
    rows, not an index time series.

    UNVERIFIED: bdshare's exact column names for this function couldn't be
    confirmed from outside a live run (no network to dsebd.org or PyPI
    packages from this sandbox), so this prints the raw columns/rows
    first and searches for anything that looks like a DSEX value column,
    rather than assuming a fixed schema. If the column-matching guesses
    below are wrong, the [DEBUG] output will show the real columns so
    they can be corrected."""
    end_dt = datetime.strptime(as_of_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=lookback_days)

    try:
        from bdshare import get_market_inf_more_data
    except ImportError as e:
        print(f"   [DEBUG] bdshare not installed/importable: {e}")
        return {"latest": None, "realized_vol_annualized_pct": None, "trend": None}

    try:
        raw = get_market_inf_more_data(start_dt.strftime("%Y-%m-%d"), as_of_date)
    except Exception as e:
        print(f"   [DEBUG] bdshare.get_market_inf_more_data error: {str(e)[:300]}")
        return {"latest": None, "realized_vol_annualized_pct": None, "trend": None}

    if raw is None or (hasattr(raw, "empty") and raw.empty):
        print(f"   [DEBUG] bdshare.get_market_inf_more_data returned no rows for "
              f"{start_dt.strftime('%Y-%m-%d')}..{as_of_date}")
        return {"latest": None, "realized_vol_annualized_pct": None, "trend": None}

    print(f"   [DEBUG] bdshare.get_market_inf_more_data columns: {list(raw.columns)}")
    print(f"   [DEBUG] bdshare.get_market_inf_more_data sample row: {raw.iloc[0].to_dict()}")

    # Try to find a DSEX-specific value column/row without assuming a fixed
    # schema: look for a column whose values include "DSEX" (a long-format
    # table with an index-name column), else fall back to any column whose
    # NAME contains "dsex".
    closes = None
    for col in raw.columns:
        try:
            if raw[col].astype(str).str.contains("DSEX", case=False, na=False).any():
                name_col = col
                value_candidates = [c for c in raw.columns if c != name_col
                                     and raw[c].dtype.kind in "if"]
                if value_candidates:
                    subset = raw[raw[name_col].astype(str).str.upper() == "DSEX"]
                    if not subset.empty:
                        closes = subset[value_candidates[0]].astype(float)
                break
        except Exception:
            continue
    if closes is None:
        dsex_cols = [c for c in raw.columns if "dsex" in str(c).lower()]
        if dsex_cols:
            closes = raw[dsex_cols[0]].astype(float)

    if closes is None or len(closes) < 5:
        print(f"   [DEBUG] Could not identify a DSEX value column/row from the columns above -- "
              f"the column-matching guess needs adjusting once the real schema is visible.")
        return {"latest": None, "realized_vol_annualized_pct": None, "trend": None}

    daily_ret = closes.pct_change().dropna()
    realized_vol = float(daily_ret.std() * np.sqrt(252) * 100) if len(daily_ret) > 1 else None
    short_avg = float(closes.tail(5).mean())
    long_avg = float(closes.mean())
    trend = "up" if short_avg > long_avg * 1.005 else ("down" if short_avg < long_avg * 0.995 else "flat")
    return {
        "latest": round(float(closes.iloc[-1]), 2),
        "realized_vol_annualized_pct": round(realized_vol, 2) if realized_vol is not None else None,
        "trend": trend,
    }


def fetch_usdbdt_snapshot(as_of_date: str, lookback_days: int = 30) -> dict:
    """USD/BDT exchange rate via yfinance -- proxy for Taka strength,
    replacing DXY. Falls back to N/A if the ticker returns no data."""
    end_dt = datetime.strptime(as_of_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=lookback_days)
    try:
        df = yf.download(
            USDBDT_TICKER,
            start=start_dt.strftime("%Y-%m-%d"),
            end=(end_dt + timedelta(days=1)).strftime("%Y-%m-%d"),
            progress=False,
        )
        if df.empty:
            print(f"   [DEBUG] yfinance returned no data for {USDBDT_TICKER} -- this ticker "
                  f"may not be covered; a different USD/BDT data source would be needed.")
            return {"latest": None, "avg": None}
        # CONFIRMED bug: newer yfinance versions can return a multi-level
        # column structure even for a single ticker (df["Close"] then comes
        # back as a DataFrame, not a Series, and float(...) on its last row
        # fails with "must be a string or a real number, not 'Series'").
        # Squeeze to a plain Series regardless of which shape came back.
        closes = df["Close"]
        if isinstance(closes, pd.DataFrame):
            closes = closes.iloc[:, 0]
        latest = float(closes.iloc[-1])
        avg = float(closes.mean())
        return {"latest": round(latest, 4), "avg": round(avg, 4)}
    except Exception as e:
        print(f"   [DEBUG] yfinance error for {USDBDT_TICKER}: {str(e)[:200]}")
        return {"latest": None, "avg": None, "error": str(e)[:150]}


def classify_macro_regime(dsex: dict, usdbdt: dict) -> str:
    """
    Rule-based DSE macro regime tag:
    RISK_OFF_HIGH_VOL, RISK_ON_LOW_VOL, DSEX_UPTREND, DSEX_DOWNTREND,
    TAKA_DEPRECIATING, NEUTRAL_MACRO
    """
    vol = dsex.get("realized_vol_annualized_pct")
    if vol is not None:
        if vol >= 25:
            return "RISK_OFF_HIGH_VOL"
        if vol <= 12:
            return "RISK_ON_LOW_VOL"

    trend = dsex.get("trend")
    if trend == "up":
        return "DSEX_UPTREND"
    if trend == "down":
        return "DSEX_DOWNTREND"

    latest = usdbdt.get("latest")
    avg = usdbdt.get("avg")
    if latest is not None and avg is not None and avg != 0 and latest > avg * 1.01:
        return "TAKA_DEPRECIATING"

    return "NEUTRAL_MACRO"


def create_macro_regime_analyst(llm):
    def macro_analyst_node(state):
        current_date = state["trade_date"]
        print(f"🌍 Fetching DSE macro snapshot for {current_date}...")

        dsex = fetch_dsex_snapshot(current_date)
        usdbdt = fetch_usdbdt_snapshot(current_date)
        macro_regime = classify_macro_regime(dsex, usdbdt)
        dsex_ok = (dsex.get('realized_vol_annualized_pct') is not None) or (dsex.get('trend') is not None)
        if not dsex_ok:
            print('   ⚠️  DSEX data UNAVAILABLE (fetch failed) -- regime tag rests on USD/BDT only; missing data is treated as UNKNOWN, not neutral.')
        dq_note = '' if dsex_ok else ('- DATA QUALITY: DSEX data is UNAVAILABLE this run (fetch failed). Missing data is NOT a neutral or risk-off signal: '
                                      'say the DSEX regime is UNKNOWN, base conclusions on USD/BDT only, and do NOT call the market neutral or risk-off because data is missing.\n')

        print(f"   DSEX: {dsex.get('latest')} (vol={dsex.get('realized_vol_annualized_pct')}%, "
              f"trend={dsex.get('trend')})  |  USD/BDT: {usdbdt.get('latest')}")
        print(f"   🏷️  Macro regime: {macro_regime}")

        prompt = f"""You are a macro strategist covering the Dhaka Stock Exchange (DSE). Today is {current_date}.
Using the REAL data below, write a brief (3-4 sentence) macro market regime report for DSE.
Classify whether conditions are risk-on or risk-off for DSE equities, what the DSEX index
trend implies, and what USD/BDT movement (if available) implies for import-heavy /
dollar-liability-heavy sectors.

=== DSE MACRO SNAPSHOT ===
DSEX Index:            latest={dsex.get('latest')}, realized volatility (annualized)={dsex.get('realized_vol_annualized_pct')}%, trend={dsex.get('trend')}
USD/BDT Exchange Rate: latest={usdbdt.get('latest')}, 30d avg={usdbdt.get('avg')}

RULES:
- If a field above shows None/N/A, say "data not available" for it -- do NOT invent a number.
{dq_note}
Rule-based macro regime tag: {macro_regime}
""" + get_language_instruction()

        messages = [
            SystemMessage(content="You are a senior macro strategist for the Dhaka Stock Exchange. Base your report only on the data given; never invent figures."),
            HumanMessage(content=prompt),
        ]
        response = cached_analyst_invoke("macro", llm, messages)
        macro_report = response.content
        print("✅ Macro Regime Report ready!")

        all_msgs = list(state.get("messages", [])) + messages + [AIMessage(content=macro_report)]
        return {
            "messages": all_msgs,
            "macro_report": macro_report,
            "macro_regime": macro_regime,
            "macro_snapshot": {"dsex": dsex, "usdbdt": usdbdt},
            "dsex_data_ok": dsex_ok,
        }
    return macro_analyst_node


macro_regime_analyst = create_macro_regime_analyst(llm_macro)

macro_state = {
    "trade_date": TRADE_DATE,
    "messages": [],
}
macro_result = macro_regime_analyst(macro_state)

log_step(
    "Macro Regime Analyst",
    {"date": TRADE_DATE, "sources": ["DSEX (via DSE day_end_archive.php)", "USD/BDT (yfinance)"]},
    {
        "macro_regime":   macro_result.get("macro_regime"),
        "macro_snapshot": macro_result.get("macro_snapshot"),
        "dsex_data_ok":   macro_result.get("dsex_data_ok"),
        "report":         macro_result.get("macro_report"),
    }
)

from IPython.display import Markdown
Markdown(macro_result["macro_report"])
