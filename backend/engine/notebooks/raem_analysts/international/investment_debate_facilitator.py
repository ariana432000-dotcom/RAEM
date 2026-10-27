# raem_analysts/international/investment_debate_facilitator.py -- shared by AAPL, IBM, 0300_HK, 2222_SR
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# Investment Debate শেষে Facilitator চালাও
# (debate_state এ final_debate_state থাকতে হবে)

facilitator_input = {
    **debate_state,
    "investment_debate_state": final_debate_state,
}

facilitator_result = investment_facilitator(facilitator_input)
investment_facilitator_decision = facilitator_result["investment_facilitator_decision"]

from IPython.display import Markdown
Markdown(investment_facilitator_decision)

log_step(
    "Investment Debate Facilitator",
    {
        "debate_history": final_debate_history[:500],
        "bull_arguments": final_debate_state.get("bull_history", "")[:300],
        "bear_arguments": final_debate_state.get("bear_history", "")[:300],
    },
    investment_facilitator_decision
)

