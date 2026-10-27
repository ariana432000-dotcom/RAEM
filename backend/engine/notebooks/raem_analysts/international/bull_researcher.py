# raem_analysts/international/bull_researcher.py -- shared by AAPL, IBM, 0300_HK, 2222_SR
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
from tradingagents.agents.utils.agent_utils import get_language_instruction

def create_bull_researcher(llm):
    def bull_node(state):
        investment_debate_state = state["investment_debate_state"]
        history = investment_debate_state.get("history", "")
        bull_history = investment_debate_state.get("bull_history", "")
        current_response = investment_debate_state.get("current_response", "")

        market_research_report = str(state["market_report"])[:500]
        sentiment_report = str(state["sentiment_report"])[:500]
        news_report = str(state["news_report"])[:500]
        # ✅ FIXED: was fundamentals_report[:500], which silently cut off
        # the Fundamentals Analyst's own FINAL TRANSACTION PROPOSAL verdict.
        fundamentals_report = truncate_preserving_verdict(state["fundamentals_report"], 500)
        # ✅ FIXED (numeric hallucination): see Bear node above for full
        # explanation -- HARD DATA is never truncated, unlike market_report.
        hard_indicators = format_hard_indicators(state)
        # ✅ NEW: inject retrieved news-RAG context (previously ingested but never used downstream)
        macro_ctx = format_shared_context(state, news=False, macro=True, memory=False)
        news_rag_snippet = str(state.get("news_rag_context") or globals().get("news_rag_context", ""))[:globals().get("NEWS_RAG_CAP", 1600)]

        fv_instr = "IMPORTANT: If the Fundamentals Analyst's own report above includes an explicit FINAL TRANSACTION PROPOSAL (e.g. SELL) that contradicts your bull case, you must directly address it with specific counter-evidence from the reports  -  do not dismiss it with vague statements about 'long-term prospects' or 'investor confidence' alone." if extract_final_proposal(state.get("fundamentals_report", "")) else 'IMPORTANT: No analyst verdict is supplied. Judge the stock only from the numbers and reports below.'
        prompt = f"""IMPORTANT: Use ONLY the data in the reports below. Do NOT invent facts or figures.\nIMPORTANT: If a source is cited in the reports as reporting NEGATIVE news (a miss, downgrade, guidance cut, etc.), do NOT reframe that same source as expressing optimism or "potential for recovery" unless the reports explicitly say so  -  that is a factual misattribution, not bullish spin.\n{fv_instr}\nIMPORTANT: HARD DATA below is the ONLY authoritative source for RSI/MACD/SMA/Bollinger numbers -- if you cite any of these, use the HARD DATA value exactly, never a number from your own memory or estimate.\nYou are a Bull Analyst advocating for investing in the stock.
HARD DATA (ground-truth numeric indicators): {hard_indicators}
News RAG context: {news_rag_snippet}
Macro context: {macro_ctx}
Market report: {market_research_report}
Sentiment report: {sentiment_report}
News report: {news_report}
Fundamentals report: {fundamentals_report}
Debate history: {history}
Last bear argument: {current_response}
Evidence rule: do not just restate the RSI/MACD/SMA numbers already in the debate. Support your case with specific facts from at least TWO other categories (fundamentals incl. the per-year dividend history, news, peer-graph, macro, memory) that the opponent has not already used, and never cite a number you cannot find in the data above.
Present a compelling bull argument with specific growth opportunities and strengths.
""" + get_language_instruction()

        response = llm.invoke(prompt)
        argument = f"Bull Analyst: {response.content}"

        new_investment_debate_state = {
            "history": history + "\n" + argument,
            "bull_history": bull_history + "\n" + argument,
            "bear_history": investment_debate_state.get("bear_history", ""),
            "current_response": argument,
            "count": investment_debate_state["count"] + 1,
        }
        return {"investment_debate_state": new_investment_debate_state}
    return bull_node



bull_researcher = create_bull_researcher(llm_debate)

debate_state = {
    "company_of_interest": "@@TICKER@@",
    "trade_date": TRADE_DATE,
    "asset_type": "stock",
    # ✅ Real analyst reports
    "fundamentals_report": fundamentals_result["fundamentals_report"],
    "market_report": market_result["market_report"],
    "news_report": news_result["news_report"],
    "sentiment_report": sentiment_result["sentiment_report"],
    # ✅ FIXED (numeric hallucination): raw indicator floats, so Bear/Bull
    # nodes always have ground-truth RSI/MACD even if market_report gets
    # truncated mid-sentence.
    "indicators_parsed": market_result.get("indicators_parsed", {}),
    # ✅ NEW: news RAG context, so Bull/Bear can use retrieved news chunks
    "news_rag_context": news_rag_context,
    "investment_debate_state": {
        "history": "",
        "bear_history": "",
        "bull_history": "",
        "current_response": "",
        "count": 0,
    }
}

# Round 1: Bull
print("=" * 50)
print("Round 1 - Bull Analyst:")
print("=" * 50)
bull_result = bull_researcher(debate_state)
state_r1 = {**debate_state, **bull_result}
print(bull_result["investment_debate_state"]["bull_history"])

# Round 1: Bear
print("\n" + "=" * 50)
print("Round 1 - Bear Analyst:")
print("=" * 50)
bear_result = bear_researcher(state_r1)
state_r2 = {**debate_state, **bear_result}
print(bear_result["investment_debate_state"]["bear_history"])

# Round 2: Bull
print("\n" + "=" * 50)
print("Round 2 - Bull Analyst:")
print("=" * 50)
bull_result2 = bull_researcher(state_r2)
state_r3 = {**debate_state, **bull_result2}
print(bull_result2["investment_debate_state"]["bull_history"])

# Round 2: Bear
print("\n" + "=" * 50)
print("Round 2 - Bear Analyst:")
print("=" * 50)
bear_result2 = bear_researcher(state_r3)
print(bear_result2["investment_debate_state"]["bear_history"])

print("\n✅ Debate Complete!")
print("Total rounds:", bear_result2["investment_debate_state"]["count"])

final_debate_history = bear_result2["investment_debate_state"]["history"]
final_debate_state = bear_result2["investment_debate_state"]
log_step(
    "Investment Debate (Bull vs Bear)",
    {
        "fundamentals_report": fundamentals_result.get("fundamentals_report", "")[:300],
        "market_report": market_result.get("market_report", "")[:300],
        "news_report": news_result.get("news_report", "")[:300],
        "sentiment_report": sentiment_result.get("sentiment_report", "")[:300],
    },
    final_debate_history[:1500]
)


