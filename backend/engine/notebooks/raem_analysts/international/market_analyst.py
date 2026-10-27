# raem_analysts/international/market_analyst.py -- shared by AAPL, IBM, 0300_HK, 2222_SR
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
import re
from datetime import datetime, timedelta
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_indicators,
    get_language_instruction,
    get_stock_data,
)

llm_market = ChatOllama(model="qwen2.5:7b", base_url="http://localhost:11434", num_ctx=8192, temperature=0, num_predict=_cap('market') or 2048, repeat_penalty=1.15)

# ── No bull/bear wording in the market (technical) analysis ──
# This report describes @@TICKER@@'s OWN price/indicator condition, not the market, so it must not call anything
# "bullish" / "bearish". The prompt asks for neutral wording; _neutralise_bull_bear() then enforces it in code,
# because a 7B model does not always obey. (Internal regime tags such as TRENDING_BULL are untouched on purpose:
# episodes already stored in ChromaDB use them as filter keys.)
NEUTRAL_WORDING = True
_BB_MAP = [
    (re.compile(r"\bbull(?:ish)?\s+market\b", re.I), "uptrend"), (re.compile(r"\bbear(?:ish)?\s+market\b", re.I), "downtrend"),
    (re.compile(r"\bbullish\b", re.I), "upward-leaning"), (re.compile(r"\bbearish\b", re.I), "downward-leaning"),
    (re.compile(r"\bbulls\b", re.I), "buyers"), (re.compile(r"\bbears\b", re.I), "sellers"),
]


def _neutralise_bull_bear(text: str) -> str:
    for rx, rep_ in _BB_MAP:
        text = rx.sub(rep_, text)
    return text


def create_market_analyst(llm):
    def market_analyst_node(state):
        current_date = state["trade_date"]
        company = state["company_of_interest"]
        asset_type = state.get("asset_type", "stock")
        instrument_context = resolve_instrument_context(
            company, build_instrument_context(company, asset_type)
        )

        print(f"📈 Fetching market data for {company} on {current_date}...")

        end_dt = datetime.strptime(current_date, "%Y-%m-%d")
        start_dt = end_dt - timedelta(days=30)
        start_date = start_dt.strftime("%Y-%m-%d")

        # ── get_stock_data ──
        print("⚙️  Calling: get_stock_data")
        try:
            stock_data = get_stock_data.invoke({
                "symbol": company,
                "start_date": start_date,
                "end_date": current_date,
            })
            stock_data = str(stock_data)[:2000]
            print(f"   ✅ Got {len(stock_data)} chars")
        except Exception as e:
            stock_data = f"Error: {str(e)}"
            print(f"   ❌ {e}")

        # ── get_indicators (one at a time) ──
        print("⚙️  Calling: get_indicators")
        ind_parts = []
        for ind_name in ["rsi", "macd", "close_50_sma", "boll_ub", "boll_lb"]:
            try:
                val = get_indicators.invoke({
                    "symbol": company,
                    "indicator": ind_name,
                    "curr_date": current_date,
                })
                ind_parts.append(f"{ind_name}: {str(val)[:300]}")
                print(f"      ✅ {ind_name}")
            except Exception as ie:
                ind_parts.append(f"{ind_name}: Error - {str(ie)[:100]}")
                print(f"      ❌ {ind_name}: {ie}")
        indicators_str = "\n".join(ind_parts)

        # ── build indicators dict — latest trading day value extract ──
        import re as _re

        def _extract_indicator_value(raw_str):
            """N/A বাদ দিয়ে সবচেয়ে সাম্প্রতিক trading day-এর value নাও।"""
            for line in raw_str.split("\n"):
                line = line.strip()
                if _re.match(r"\d{4}-\d{2}-\d{2}:", line) and "N/A" not in line:
                    m = _re.search(r":\s*(-?\d+\.?\d+)", line)
                    if m:
                        return float(m.group(1))
            return None

        indicators_dict = {}
        for item in ind_parts:
            if ": " in item:
                k, _, v = item.partition(": ")
                k = k.strip()
                val = _extract_indicator_value(v)
                indicators_dict[k] = val if val is not None else v.strip()[:100]

        # ── LLM writes the report ──
        prompt = f"""You are a trading analyst. Today is {current_date}.
{instrument_context}

Using the REAL data below, write a brief technical analysis report for {company}.
Include RSI, MACD, Bollinger Bands, SMA analysis.
{_len_rule('market')}

WORDING RULES: This is an analysis of {company}'s OWN price and indicators, not of the market. Never use the words
"bullish", "bearish", "bull" or "bear". Describe the condition with neutral terms such as "upward momentum",
"downward momentum", "price above/below the 50-day SMA", "RSI in the overbought / oversold / neutral zone",
"price near the upper/lower Bollinger band". Do not predict the market direction.

At the very end, output a Markdown table with EXACTLY these columns:
| Indicator | Value | Signal | Interpretation |
In the Signal column use only: UPWARD, DOWNWARD, NEUTRAL, OVERBOUGHT, OVERSOLD.

=== STOCK PRICE DATA ===
{stock_data}

=== TECHNICAL INDICATORS ===
{indicators_str}
""" + get_language_instruction()

        messages = [
            SystemMessage(content="You are a senior trading analyst. Write reports based only on provided data. Always end with the required Markdown table."),
            HumanMessage(content=prompt),
        ]

        response = cached_analyst_invoke("market", llm, messages)
        report = response.content
        if NEUTRAL_WORDING:
            report = _neutralise_bull_bear(report)
        print("✅ Market Report ready!")

        all_msgs = list(state.get("messages", [])) + messages + [AIMessage(content=report)]
        return {
            "messages":          all_msgs,
            "market_report":     report,
            "indicators_parsed": indicators_dict,   # ← float dict
            "market_raw_data": {
                "stock_data":  stock_data,
                "indicators":  indicators_dict,    # ← float dict (not raw strings)
            },
        }

    return market_analyst_node


market_analyst = create_market_analyst(llm_market)

market_state = {
    "trade_date": TRADE_DATE,
    "company_of_interest": "@@TICKER@@",
    "asset_type": "stock",
    "messages": [],
}

result = market_analyst(market_state)

log_step(
    "Market Analyst",
    {
        "company":    market_state["company_of_interest"],
        "date":       market_state["trade_date"],
        "tools_used": "get_stock_data, get_indicators",
    },
    {
        "indicators":  result.get("indicators_parsed", {}),   # ← float dict
        "stock_data":  result.get("market_raw_data", {}).get("stock_data", ""),
        "report":      result.get("market_report", ""),
    }
)

market_result = result
from IPython.display import Markdown
Markdown(result["market_report"])
