# raem_analysts/international/risk_debate.py -- shared by AAPL, IBM, 0300_HK, 2222_SR
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
import time
from langchain_ollama import ChatOllama
from tradingagents.agents.utils.agent_utils import get_language_instruction

# Risk Analysts already defined above (aggressive, conservative, neutral)
# RISK_DEBATE_ROUNDS (default 1) rounds চালাবো

print("=" * 60)
print(f"🏁 RISK MANAGEMENT DEBATE — {globals().get('RISK_DEBATE_ROUNDS', 1)} ROUND(S)")
print("=" * 60)

current_risk_state = {
    "company_of_interest": "@@TICKER@@",
    "asset_type": "stock",
    "fundamentals_report": fundamentals_result["fundamentals_report"],
    "market_report": market_result["market_report"],
    "news_report": news_result["news_report"],
    "sentiment_report": sentiment_result["sentiment_report"],
    # ✅ FIXED (numeric hallucination): this is the REAL 3-round state that
    # feeds the Risk Facilitator and Portfolio Manager -- raw indicator
    # floats must be here, not just in the Cells 30/31 single-round demo state.
    "indicators_parsed": market_result.get("indicators_parsed", {}),
    "trader_investment_plan": trader_result["trader_investment_plan"],
    # ✅ NEW: news RAG context flows to Aggressive/Conservative/Neutral AND
    # (via the pm_state spread later) to the Portfolio Manager too.
    "news_rag_context": news_rag_context,
    "risk_debate_state": {
        "history": "",
        "aggressive_history": "",
        "conservative_history": "",
        "neutral_history": "",
        "latest_speaker": "",
        "current_aggressive_response": "",
        "current_conservative_response": "",
        "current_neutral_response": "",
        "count": 0,
    }
}

# Risk debate length. Audit of a real run: rounds 2-3 only re-worded round 1 (Conservative/Neutral/Aggressive repeated each
# other), and every extra LLM text was another chance to copy or invent a wrong number. 1 round is enough; set 2-3 to re-test.
RISK_DEBATE_ROUNDS = 1
NUM_ROUNDS = RISK_DEBATE_ROUNDS

for round_num in range(1, NUM_ROUNDS + 1):
    print(f"\n{'='*50}")
    print(f"📍 ROUND {round_num}")
    print(f"{'='*50}")

    # Aggressive
    print(f"\n🔴 Round {round_num} — Aggressive Analyst:")
    agg_result = aggressive_debator(current_risk_state)
    current_risk_state["risk_debate_state"] = agg_result["risk_debate_state"]
    print(agg_result["risk_debate_state"]["current_aggressive_response"][:400])

    time.sleep(2)  # rate limit buffer (Ollama-তে dorkar nao hote pare)

    # Conservative
    print(f"\n🔵 Round {round_num} — Conservative Analyst:")
    con_result = conservative_debator(current_risk_state)
    current_risk_state["risk_debate_state"] = con_result["risk_debate_state"]
    print(con_result["risk_debate_state"]["current_conservative_response"][:400])

    time.sleep(2)

    # Neutral
    print(f"\n🟡 Round {round_num} — Neutral Analyst:")
    neu_result = neutral_debator(current_risk_state)
    current_risk_state["risk_debate_state"] = neu_result["risk_debate_state"]
    print(neu_result["risk_debate_state"]["current_neutral_response"][:400])

    time.sleep(2)

final_risk_debate_state = current_risk_state["risk_debate_state"]
print(f"\n✅ Risk Debate Complete! Total arguments: {final_risk_debate_state['count']}")

log_step(
    f"Risk Management Debate ({NUM_ROUNDS} Round(s))",
    {
        "trader_decision": trader_result.get("trader_investment_plan", "")[:300],
        "fundamentals": fundamentals_result.get("fundamentals_report", "")[:200],
        "market": market_result.get("market_report", "")[:200],
    },
    {
        "aggressive": final_risk_debate_state['aggressive_history'][-500:],
        "conservative": final_risk_debate_state['conservative_history'][-500:],
        "neutral": final_risk_debate_state['neutral_history'][-500:]
    }
)

