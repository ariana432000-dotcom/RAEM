# raem_analysts/international/bear_researcher.py -- shared by AAPL, IBM, 0300_HK, 2222_SR
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
from langchain_ollama import ChatOllama
from tradingagents.agents.utils.agent_utils import get_language_instruction

llm_debate = ChatOllama(model="qwen2.5:7b", base_url="http://localhost:11434", num_ctx=8192, temperature=0.2, repeat_penalty=1.15, num_predict=2048)

def create_bear_researcher(llm):
    def bear_node(state):
        investment_debate_state = state["investment_debate_state"]
        history = investment_debate_state.get("history", "")
        bear_history = investment_debate_state.get("bear_history", "")
        current_response = investment_debate_state.get("current_response", "")

        market_research_report = str(state["market_report"])[:500]
        sentiment_report = str(state["sentiment_report"])[:500]
        news_report = str(state["news_report"])[:500]
        # ✅ FIXED: was fundamentals_report[:500], which silently cut off
        # the Fundamentals Analyst's own FINAL TRANSACTION PROPOSAL verdict.
        fundamentals_report = truncate_preserving_verdict(state["fundamentals_report"], 500)
        # ✅ FIXED (numeric hallucination): [:500] truncation can silently cut
        # off the actual RSI/MACD number mid-sentence, leaving the LLM with no
        # ground-truth figure -- which it then invents. HARD DATA below is
        # never truncated, so a real number is always available.
        hard_indicators = format_hard_indicators(state)
        # ✅ NEW: inject retrieved news-RAG context (previously ingested but never used downstream)
        macro_ctx = format_shared_context(state, news=False, macro=True, memory=False)
        news_rag_snippet = str(state.get("news_rag_context") or globals().get("news_rag_context", ""))[:globals().get("NEWS_RAG_CAP", 1600)]

        fv_instr = "IMPORTANT: If the Fundamentals Analyst's own report above includes an explicit FINAL TRANSACTION PROPOSAL (e.g. BUY) that contradicts your bear case, address it directly using the reports' own numbers rather than ignoring it." if extract_final_proposal(state.get("fundamentals_report", "")) else 'IMPORTANT: No analyst verdict is supplied. Judge the stock only from the numbers and reports below.'
        prompt = f"""IMPORTANT: Use ONLY the data in the reports below. Do NOT invent facts or figures.\nIMPORTANT: Do not overstate risks beyond what the reports actually support, and do not ignore genuinely positive figures in the reports if present.\n{fv_instr}\nIMPORTANT: HARD DATA below is the ONLY authoritative source for RSI/MACD/SMA/Bollinger numbers -- if you cite any of these, use the HARD DATA value exactly, never a number from your own memory or estimate.\nYou are a Bear Analyst making the case against investing in the stock.
HARD DATA (ground-truth numeric indicators): {hard_indicators}
News RAG context: {news_rag_snippet}
Macro context: {macro_ctx}
Market report: {market_research_report}
Sentiment report: {sentiment_report}
News report: {news_report}
Fundamentals report: {fundamentals_report}
Debate history: {history}
Last bull argument: {current_response}
Evidence rule: do not just restate the RSI/MACD/SMA numbers already in the debate. Support your case with specific facts from at least TWO other categories (fundamentals incl. the per-year dividend history, news, peer-graph, macro, memory) that the opponent has not already used, and never cite a number you cannot find in the data above.
Present a compelling bear argument with specific risks and weaknesses.
""" + get_language_instruction()

        response = llm.invoke(prompt)
        argument = f"Bear Analyst: {response.content}"

        new_investment_debate_state = {
            "history": history + "\n" + argument,
            "bear_history": bear_history + "\n" + argument,
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": argument,
            "count": investment_debate_state["count"] + 1,
        }
        return {"investment_debate_state": new_investment_debate_state}
    return bear_node

bear_researcher = create_bear_researcher(llm_debate)
print("Bear Researcher ready! ✅")
