# raem_analysts/international/macro_regime_analyst.py -- shared by AAPL, IBM, 0300_HK, 2222_SR
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 🌍 MACRO REGIME ANALYST — নতুন Agent
# ================================================================
# এখন পর্যন্ত regime শুধু stock-specific indicator (RSI/MACD/Bollinger)
# দিয়ে classify হতো। এই agent broader market-wide risk regime
# (risk-on/risk-off, rate direction) বের করে — VIX, 10Y Treasury yield,
# আর Dollar Index (DXY) ব্যবহার করে। এটা Bull/Bear debate আর Trader-এর
# context-কে সমৃদ্ধ করবে, এবং episode metadata-তে আলাদা ট্যাগ হিসেবে
# save হবে (existing stock-regime filtering ভাঙবে না)।

%pip install yfinance -q

import yfinance as yf
from datetime import datetime, timedelta
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from tradingagents.agents.utils.agent_utils import get_language_instruction

llm_macro = ChatOllama(model="qwen2.5:7b", base_url="http://localhost:11434", temperature=0, repeat_penalty=1.15, num_predict=2048)

MACRO_TICKERS = {
    "vix": "^VIX",      # fear gauge
    "tnx": "^TNX",      # 10Y treasury yield
    "dxy": "DX-Y.NYB",  # US dollar index
}


def fetch_macro_snapshot(as_of_date: str, lookback_days: int = 30) -> dict:
    """VIX, 10Y yield, DXY-র সাম্প্রতিক মান + lookback period-এর average আনে।"""
    end_dt = datetime.strptime(as_of_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=lookback_days)

    snapshot = {}
    for key, ticker in MACRO_TICKERS.items():
        try:
            df = yf.download(
                ticker,
                start=start_dt.strftime("%Y-%m-%d"),
                end=(end_dt + timedelta(days=1)).strftime("%Y-%m-%d"),
                progress=False,
            )
            if df.empty:
                snapshot[key] = {"latest": None, "avg": None}
                continue
            latest = float(df["Close"].iloc[-1])
            avg = float(df["Close"].mean())
            snapshot[key] = {"latest": round(latest, 2), "avg": round(avg, 2)}
        except Exception as e:
            snapshot[key] = {"latest": None, "avg": None, "error": str(e)[:100]}
    return snapshot


def classify_macro_regime(snapshot: dict) -> str:
    """
    Simple rule-based macro regime tag:
    RISK_OFF_HIGH_VOL, RISK_ON_LOW_VOL, RATES_RISING, RATES_FALLING, NEUTRAL_MACRO
    """
    vix = (snapshot.get("vix") or {}).get("latest")
    tnx = (snapshot.get("tnx") or {}).get("latest")
    tnx_avg = (snapshot.get("tnx") or {}).get("avg")

    if vix is not None:
        if vix >= 25:
            return "RISK_OFF_HIGH_VOL"
        if vix <= 15:
            return "RISK_ON_LOW_VOL"

    if tnx is not None and tnx_avg is not None and tnx_avg != 0:
        if tnx > tnx_avg * 1.03:
            return "RATES_RISING"
        if tnx < tnx_avg * 0.97:
            return "RATES_FALLING"

    return "NEUTRAL_MACRO"


def create_macro_regime_analyst(llm):
    def macro_analyst_node(state):
        current_date = state["trade_date"]
        print(f"🌍 Fetching macro snapshot for {current_date}...")

        snapshot = fetch_macro_snapshot(current_date)
        macro_regime = classify_macro_regime(snapshot)

        print(f"   VIX: {snapshot.get('vix')}  |  10Y: {snapshot.get('tnx')}  |  DXY: {snapshot.get('dxy')}")
        print(f"   🏷️  Macro regime: {macro_regime}")

        prompt = f"""You are a macro strategist. Today is {current_date}.
Using the REAL data below, write a brief (3-4 sentence) macro market regime report.
Classify whether conditions are risk-on or risk-off, whether rates are rising or
falling, and what this implies for equity positioning.

=== MACRO SNAPSHOT ===
VIX (fear gauge):      latest={snapshot.get('vix', {}).get('latest')}, 30d avg={snapshot.get('vix', {}).get('avg')}
10Y Treasury Yield:    latest={snapshot.get('tnx', {}).get('latest')}, 30d avg={snapshot.get('tnx', {}).get('avg')}
US Dollar Index (DXY): latest={snapshot.get('dxy', {}).get('latest')}, 30d avg={snapshot.get('dxy', {}).get('avg')}

Rule-based macro regime tag: {macro_regime}
""" + get_language_instruction()

        messages = [
            SystemMessage(content="You are a senior macro strategist. Base your report only on the data given."),
            HumanMessage(content=prompt),
        ]
        response = llm.invoke(messages)
        macro_report = response.content
        print("✅ Macro Regime Report ready!")

        all_msgs = list(state.get("messages", [])) + messages + [AIMessage(content=macro_report)]
        return {
            "messages": all_msgs,
            "macro_report": macro_report,
            "macro_regime": macro_regime,
            "macro_snapshot": snapshot,
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
    {"date": TRADE_DATE, "tickers": list(MACRO_TICKERS.values())},
    {
        "macro_regime":   macro_result.get("macro_regime"),
        "macro_snapshot": macro_result.get("macro_snapshot"),
        "report":         macro_result.get("macro_report"),
    }
)

from IPython.display import Markdown
Markdown(macro_result["macro_report"])

