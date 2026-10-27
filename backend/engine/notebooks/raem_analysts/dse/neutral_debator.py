# raem_analysts/dse/neutral_debator.py -- shared by abbank-dse, batbc-dse, bpml-dse, waltonhil-dse
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
import time
from tradingagents.agents.utils.agent_utils import get_language_instruction

def create_neutral_debator(llm):
    def neutral_node(state):
        risk_debate_state = state["risk_debate_state"]
        history = risk_debate_state.get("history", "")
        neutral_history = risk_debate_state.get("neutral_history", "")
        current_aggressive_response = risk_debate_state.get("current_aggressive_response", "")
        current_conservative_response = risk_debate_state.get("current_conservative_response", "")

        market_research_report = str(state["market_report"])[:500]
        sentiment_report = str(state["sentiment_report"])[:500]
        news_report = str(state["news_report"])[:500]
        # ✅ FIXED: was str(...)[:500], silently cutting the Fundamentals
        # Analyst's own FINAL TRANSACTION PROPOSAL verdict (same bug already
        # fixed for Bull/Bear/Trader/PM — was still present here).
        fundamentals_report = truncate_preserving_verdict(state["fundamentals_report"], 500)
        fund_verdict = extract_final_proposal(state.get("fundamentals_report", ""))
        fv_block = ("HARD FACT (Fundamentals Analyst's own verdict — if your case contradicts this, you must cite at least two specific numbers from the reports that offset it): " + fund_verdict) if fund_verdict else "(No analyst verdict is supplied: judge only from the numbers and reports below.)"
        trader_decision = str(state["trader_investment_plan"])[:500]
        # ✅ FIXED (numeric hallucination): never-truncated ground-truth numbers.
        hard_indicators = format_hard_indicators(state)
        shared_context = format_shared_context(state)

        prompt = f"""IMPORTANT: Use ONLY the analyst reports provided. Do NOT invent figures.\nIMPORTANT: HARD DATA below is the ONLY authoritative source for RSI/MACD/SMA/Bollinger numbers -- if you cite any of these, use the HARD DATA value exactly.\nAs the Neutral Risk Analyst, provide a balanced perspective.
Trader decision: {trader_decision}
{fv_block}
HARD DATA (ground-truth numeric indicators): {hard_indicators}
SUPPORTING CONTEXT (news RAG / macro / memory -- use to support or check claims; HARD DATA still governs indicator numbers; market-wide notices about other companies are not news about this stock):
{shared_context}
Market Report: {market_research_report}
Sentiment: {sentiment_report}
News: {news_report}
Fundamentals: {fundamentals_report}
History: {history}
Aggressive argument: {current_aggressive_response}
Conservative argument: {current_conservative_response}
Provide a balanced moderate view. Speak conversationally.
""" + get_language_instruction()

        response = invoke_llm_with_retry(llm, prompt)

        argument = f"Neutral Analyst: {response.content}"

        new_risk_debate_state = {
            "history": history + "\n" + argument,
            "aggressive_history": risk_debate_state.get("aggressive_history", ""),
            "conservative_history": risk_debate_state.get("conservative_history", ""),
            "neutral_history": neutral_history + "\n" + argument,
            "latest_speaker": "Neutral",
            "current_aggressive_response": current_aggressive_response,
            "current_conservative_response": current_conservative_response,
            "current_neutral_response": argument,
            "count": risk_debate_state["count"] + 1,
        }
        return {"risk_debate_state": new_risk_debate_state}
    return neutral_node



neutral_debator = create_neutral_debator(llm_risk)

# (the single-round demo call that used to run here was removed: the real debate runs in the 'Risk Management Debate' cell)

