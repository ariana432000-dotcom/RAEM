# raem_analysts/international/trader.py -- shared by AAPL, IBM, 0300_HK, 2222_SR
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
from __future__ import annotations
from langchain_ollama import ChatOllama
import functools
import time
from langchain_core.messages import AIMessage
from tradingagents.agents.schemas import TraderProposal, render_trader_proposal
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)

llm_trader = ChatOllama(model="qwen2.5:7b", base_url="http://localhost:11434", num_ctx=8192, temperature=0.2, repeat_penalty=1.15, num_predict=2048)

def create_trader(llm):
    structured_llm = bind_structured(llm, TraderProposal, "Trader")

    def trader_node(state, name):
        company_name = state["company_of_interest"]
        asset_type = state.get("asset_type", "stock")
        instrument_context = resolve_instrument_context(
            company_name, build_instrument_context(company_name, asset_type)
        )
        investment_plan = state["investment_plan"]

        # ✅ FIXED: the Trader used to re-derive its own judgment purely from raw
        # bull/bear text tails, independently of the Fundamentals Analyst's own
        # verdict and independently of the Investment Debate Facilitator's own
        # winner declaration (that decision was computed in Cell 21 but never
        # actually passed in here). Both are now surfaced explicitly below.
        fund_verdict = extract_final_proposal(state.get("fundamentals_report", ""))
        fv_line = ("HARD FACT (Fundamentals Analyst\'s own verdict): " + fund_verdict + "\n\n") if fund_verdict else ""
        fv_rule_sys = "If the Fundamentals Analyst's own FINAL TRANSACTION PROPOSAL below contradicts the direction you are leaning toward, you must cite at least two specific numbers from the reports that offset it before overriding it -- otherwise defer to it. " if fund_verdict else ""
        # ✅ FIXED (numeric hallucination): give the Trader the same never-
        # truncated ground-truth numbers as the Bear/Bull debators, so it
        # can't inherit or reinforce a hallucinated RSI/MACD figure from the
        # debate text with no way to check it.
        hard_indicators = format_hard_indicators(state)
        # ✅ NEW: inject retrieved news-RAG context
        news_rag_snippet = str(state.get("news_rag_context") or globals().get("news_rag_context", ""))[:globals().get("NEWS_RAG_CAP", 1600)]

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a trading agent analyzing market data to make investment decisions. "
                    "Based on your analysis, provide a specific recommendation to buy, sell, or hold. "
                    "Anchor your reasoning in the analysts' reports and the research plan. "
                    "Do not default to HOLD merely to appear balanced or avoid commitment -- if the "
                    "bear case is clearly stronger and better-supported by the data, recommend SELL; "
                    "if the bull case is clearly stronger, recommend BUY. Reserve HOLD for cases where "
                    "the evidence is genuinely and closely balanced. "
                    "Vague optimism (\"growth potential\", \"long-term prospects\", \"strategic partnerships\") "
                    "is not evidence on its own. "
                    + fv_rule_sys +
                    "Do NOT state share counts, money amounts or a '% of portfolio': position size is computed by code afterwards. "
                    "SELL means exit a long-only holding, so give NO stop-loss and NO entry price for a SELL. Use BDT (৳), never '$'. "
                    "Cite the [F#] id from the FACT SHEET after every number you use."
                    + get_language_instruction()
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Based on a comprehensive analysis by a team of analysts, here is an investment "
                    f"plan tailored for {company_name}. {instrument_context} This plan incorporates "
                    f"insights from current technical market trends, macroeconomic indicators, and "
                    f"social media sentiment. Use this plan as a foundation for evaluating your next "
                    f"trading decision.\n\n"
                    f"{fv_line}"
                    f"HARD DATA (ground-truth numeric indicators -- if the debate below cites "
                    f"RSI/MACD/SMA/Bollinger numbers, verify them against these, not the debate text): "
                    f"{hard_indicators}\n\n"
                    f"NEWS RAG CONTEXT (retrieved from stored news corpus): {news_rag_snippet}\n\n"
                    f"Proposed Investment Plan: {investment_plan}\n\n"
                    f"Leverage these insights to make an informed and strategic decision."
                ),
            },
        ]

        # ✅ FIXED: shared retry helper instead of a copy-pasted loop that
        # could leave `trader_plan` undefined if all attempts rate-limited.
        last_err = None
        trader_plan = None
        for attempt in range(3):
            try:
                trader_plan = invoke_structured_or_freetext(
                    structured_llm, llm, messages, render_trader_proposal, "Trader",
                )
                break
            except Exception as e:
                last_err = e
                if "429" in str(e) or "rate_limit" in str(e).lower():
                    wait_time = 60 * (attempt + 1)
                    print(f"⏳ Rate limit! {wait_time} সেকেন্ড অপেক্ষা...")
                    time.sleep(wait_time)
                else:
                    raise
        if trader_plan is None:
            raise RuntimeError(f"Trader: all retry attempts exhausted, last error: {last_err}")

        # \u2705 NEW: don't trust the Trader's own claim of having cited numbers --
        # verify in code. If it contradicts the Fundamentals hard fact without real
        # numeric support, correct it HERE so the false narrative doesn't cascade
        # into the Risk debate and Portfolio Manager (which both consume this text).
        trader_plan, was_gated, gate_reason = enforce_numeric_evidence_gate(trader_plan, fund_verdict)
        if was_gated:
            print(f"\u26a0\ufe0f  Numeric-evidence gate (Trader): {gate_reason}")
        # code-only check of the RENDERED plan (the structured fields Entry Price / Stop Loss / Position Sizing are rendered here,
        # after the model call): invented sizing, stop-loss on a SELL, wrong labels are cut/corrected in Python.
        trader_plan = guard_postprocess("\n".join(str(m["content"]) for m in messages), trader_plan, "trader_node")
        import re as _re_t
        trader_plan = _re_t.sub(r"(?im)^\*\*Position Sizing\*\*:.*\n?", "", trader_plan)   # sizing is computed by code, not by the model

        return {
            "messages": [AIMessage(content=trader_plan)],
            "trader_investment_plan": trader_plan,
            "sender": name,
        }

    return functools.partial(trader_node, name="Trader")


trader = create_trader(llm_trader)

bull_case = final_debate_state.get("bull_history", "")[-1000:]
bear_case = final_debate_state.get("bear_history", "")[-1000:]
# ✅ FIXED: investment_facilitator_decision (Cell 21) used to be computed and
# logged but never actually shown to the Trader — the Trader was silently
# re-deriving its own judgment from raw bull/bear text instead of building on
# the Facilitator's own scored evaluation. Now included explicitly, labeled.
facilitator_summary = f"INVESTMENT DEBATE FACILITATOR'S VERDICT:\n{investment_facilitator_decision}"
balanced_debate_summary = f"BULL CASE:\n{bull_case}\n\nBEAR CASE:\n{bear_case}\n\n{facilitator_summary}"

trader_state = {
    "company_of_interest": "@@TICKER@@",
    "asset_type": "stock",
    # ✅ FIXED: was final_debate_history[:2000] — since Bull always spoke
    # first each round, truncating the combined history from the start
    # systematically kept Bull's opening argument and cut off Bear's later
    # (most refined) rebuttal, biasing the Trader toward BUY. Now Bull and
    # Bear each get an equal, fixed character budget, taken from the END of
    # their own history (their strongest/latest argument), clearly labeled.
    "investment_plan": balanced_debate_summary + retrieved_memory_context,
    # ✅ needed so trader_node can extract the Fundamentals hard verdict directly
    "fundamentals_report": fundamentals_result["fundamentals_report"],
    # ✅ FIXED (numeric hallucination): raw indicator floats for HARD DATA.
    "indicators_parsed": market_result.get("indicators_parsed", {}),
    # ✅ NEW: news RAG context for the Trader
    "news_rag_context": news_rag_context,
    "messages": []
}

trader_result = trader(trader_state)

print("✅ Trader Decision:")
print(trader_result["trader_investment_plan"])


log_step(
    "Trader Decision",
    {
        "investment_plan_from_debate": trader_state["investment_plan"][:500],
        "facilitator_decision": investment_facilitator_decision[:300]
    },
    trader_result.get("trader_investment_plan", "")
)
