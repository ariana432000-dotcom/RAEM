# raem_analysts/international/risk_facilitator.py -- shared by AAPL, IBM, 0300_HK, 2222_SR
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
from langchain_ollama import ChatOllama
from tradingagents.agents.utils.agent_utils import get_language_instruction

llm_risk_facilitator = ChatOllama(model="qwen2.5:7b", base_url="http://localhost:11434", num_ctx=8192, temperature=0.2, repeat_penalty=1.15, num_predict=2048)

def create_risk_facilitator(llm):
    def risk_facilitator_node(state):
        risk_debate_state = state["risk_debate_state"]
        history = risk_debate_state.get("history", "")
        aggressive_history = risk_debate_state.get("aggressive_history", "")
        conservative_history = risk_debate_state.get("conservative_history", "")
        neutral_history = risk_debate_state.get("neutral_history", "")
        count = risk_debate_state.get("count", 0)

        trader_decision = str(state.get("trader_investment_plan", ""))[:500]
        shared_context = format_shared_context(state)

        prompt = f"""IMPORTANT: Base your assessment ONLY on the debate history provided. Do NOT use external knowledge.\nYou are the Risk Management Debate Facilitator. 

Trader's Original Decision: {trader_decision}

SUPPORTING CONTEXT (use ONLY to fact-check claims; not a source of new arguments):
{shared_context}

{MARKET_RULES}

After {count} total arguments across {globals().get('RISK_DEBATE_ROUNDS', 1)} round(s), evaluate the risk debate:

Aggressive Analyst Summary:
{aggressive_history[-800:]}

Conservative Analyst Summary:
{conservative_history[-800:]}

Neutral Analyst Summary:
{neutral_history[-800:]}

Your tasks:
1. Declare which risk perspective was most compelling: AGGRESSIVE / CONSERVATIVE / NEUTRAL
2. Synthesize a final risk assessment
3. Final risk rating: LOW / MEDIUM / HIGH

Do NOT give a position size (shares, money, % of portfolio) or stop-loss / take-profit prices: position sizing is computed by code afterwards.
Judge only what the analysts argued; never add new facts or numbers of your own.

IMPORTANT judging rules:
- Do NOT declare Aggressive most compelling just because it describes upside/growth/resilience  -  "compelling" means best-supported by the actual analyst reports and risk data, not most optimistic.
- If Conservative or Neutral correctly identifies risks that are confirmed by the underlying reports (e.g. negative news sentiment, weak fundamentals), that is strong evidence and must be weighted accordingly, even though it is not the more "exciting" case.

Be specific and data-driven in your assessment.
""" + get_language_instruction()

        response = llm.invoke(prompt)
        decision = response.content

        print("=" * 60)
        print("⚖️  RISK MANAGEMENT FACILITATOR DECISION")
        print("=" * 60)
        print(decision)

        return {
            "risk_debate_state": {
                **risk_debate_state,
                "facilitator_decision": decision,
            },
            "risk_facilitator_decision": decision,
        }
    return risk_facilitator_node


risk_facilitator = create_risk_facilitator(llm_risk_facilitator)
print("Risk Facilitator ready! ✅")

# Run it
risk_facilitator_input = {
    **current_risk_state,
    "risk_debate_state": final_risk_debate_state,
}

risk_facilitator_result = risk_facilitator(risk_facilitator_input)
risk_facilitator_decision = risk_facilitator_result["risk_facilitator_decision"]

from IPython.display import Markdown
Markdown(risk_facilitator_decision)
log_step(
    "Risk Facilitator Decision",
    {
        "trader_decision": trader_result.get("trader_investment_plan", "")[:300],
        "risk_debate_summary": final_risk_debate_state.get("history", "")[:500]
    },
    risk_facilitator_decision
)
