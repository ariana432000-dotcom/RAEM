# raem_analysts/dse/portfolio_manager.py -- shared by abbank-dse, batbc-dse, bpml-dse, waltonhil-dse
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
from __future__ import annotations
from langchain_ollama import ChatOllama
import functools
import time
from langchain_core.messages import AIMessage
from tradingagents.agents.schemas import PortfolioDecision, render_pm_decision
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)

llm_pm = ChatOllama(model="qwen2.5:7b", base_url="http://localhost:11434", num_ctx=8192, temperature=0.2, repeat_penalty=1.15, num_predict=2048)

# ✅ FIXED: no longer hardcoded to "Aggressive" inside the prompt.
# Set this explicitly per run instead of silently biasing the decision.
INVESTOR_PROFILE = "Balanced"  # e.g. "Conservative" / "Balanced" / "Aggressive"


def create_portfolio_manager(llm):
    structured_llm = bind_structured(llm, PortfolioDecision, "PortfolioManager")

    def pm_node(state):
        company_name = state["company_of_interest"]
        asset_type = state.get("asset_type", "stock")
        instrument_context = resolve_instrument_context(
            company_name, build_instrument_context(company_name, asset_type)
        )

        risk_debate = state["risk_debate_state"]
        # ✅ FIXED: was risk_debate.get("history", "")[:1500] — since
        # Aggressive always spoke first every round, truncating the combined
        # history from the start systematically kept Aggressive's arguments
        # and cut off Conservative/Neutral's later (most refined) rebuttals,
        # biasing the PM toward high-risk/BUY calls. Now each side gets an
        # equal, fixed character budget from the END of its own history
        # (its most refined argument), clearly labeled.
        aggressive_part = risk_debate.get("aggressive_history", "")[-600:]
        conservative_part = risk_debate.get("conservative_history", "")[-600:]
        neutral_part = risk_debate.get("neutral_history", "")[-600:]
        risk_history = (
            f"AGGRESSIVE CASE:\n{aggressive_part}\n\n"
            f"CONSERVATIVE CASE:\n{conservative_part}\n\n"
            f"NEUTRAL CASE:\n{neutral_part}"
        )
        trader_plan = state.get("trader_investment_plan", "")
        investment_plan = state.get("investment_plan", trader_plan)
        past_context = state.get("past_context", "")
        investor_profile = state.get("investor_profile", INVESTOR_PROFILE)

        # ✅ FIXED: risk_facilitator_decision (Cell 33) was computed and printed
        # but never passed into the PM's prompt \u2014 the PM was re-deriving its
        # own judgment straight from the raw debate tails instead of building on
        # the Risk Facilitator's own scored evaluation (which had already, in this
        # notebook's own runs, correctly identified Conservative as most compelling).
        risk_facilitator_summary = state.get("risk_facilitator_decision", "")
        fund_verdict = extract_final_proposal(state.get("fundamentals_report", ""))
        fv_line = ("HARD FACT (Fundamentals Analyst\'s own verdict): " + fund_verdict + "\n\n") if fund_verdict else ""
        fv_tail = ("to override the HARD FACT above, cite at least two specific numbers from the reports; otherwise defer to it." if fund_verdict
                   else "cite at least two specific numbers from the reports for any Buy or Sell call.")
        # ✅ FIXED (numeric hallucination): this is the LAST LLM stage before
        # Decision Verifier -- give it the never-truncated ground-truth
        # numbers too, so it can catch/avoid a hallucinated RSI carried over
        # from the Trader/Risk debate text instead of just repeating it.
        hard_indicators = format_hard_indicators(state)
        # ✅ NEW: inject retrieved news-RAG context
        macro_ctx = format_shared_context(state, news=False, macro=True, memory=False)
        research_verdict = str(state.get("investment_facilitator_decision", ""))[:600]
        news_rag_snippet = str(state.get("news_rag_context") or globals().get("news_rag_context", ""))[:globals().get("NEWS_RAG_CAP", 1600)]

        prompt_text = (
            f"You are the Portfolio Manager making the final investment decision for {company_name}. "
            f"{instrument_context}\n\n"
            f"Investor Profile: {investor_profile}\n"
            f"Review the risk analysts' debate and the trader's proposal, "
            f"then issue a final rating tailored to this investor profile.\n\n"
            f"{fv_line}"
            f"HARD DATA (ground-truth numeric indicators -- if the trader proposal or risk debate "
            f"below cite RSI/MACD/SMA/Bollinger numbers, verify against these, not their text): "
            f"{hard_indicators}\n\n"
            f"NEWS RAG CONTEXT (retrieved from stored news corpus): {news_rag_snippet}\n\n"
            f"MACRO REGIME: {macro_ctx}\n\n"
            f"Investment Debate Facilitator's Verdict (Bull vs Bear research stage):\n{research_verdict}\n\n"
            f"Trader Proposal:\n{trader_plan[:800]}\n\n"
            f"Risk Analysts Debate:\n{risk_history}\n\n"
            f"Risk Facilitator's Verdict (already evaluated which side was most compelling \u2014 do not "
            f"ignore this in favor of re-reading the raw arguments yourself):\n{risk_facilitator_summary[:800]}\n\n"
            f"IMPORTANT: If the underlying analyst reports show confirmed negative signals (e.g. negative news "
            f"sentiment, weak fundamentals, downward earnings revisions), do not let an optimistic trader/risk "
            f"narrative override that evidence  -  a Buy rating should be justified by the reports themselves, "
            f"not by hopeful framing. Vague optimism is not evidence: {fv_tail}\n\n"
        )
        if past_context:
            prompt_text += f"Past context (similar episodes / reflection / post-mortem lessons):\n{str(past_context)[:1200]}\n\n"
        prompt_text += (
            "In the executive summary give only the direction, the main evidence (with [F#] ids) and the time horizon. "
            "Do NOT state share counts, money amounts, '% of portfolio' or stop-loss / take-profit / entry prices: position size is "
            "computed by code afterwards, and SELL means exiting a long-only holding. Use BDT (৳), never '$'.\n\n")
        prompt_text += get_language_instruction()

        messages = [
            {"role": "system", "content": (
            "You are a senior portfolio manager. Issue a final decision. Do not default to HOLD "
            "merely to appear cautious or balanced -- if the evidence (trader proposal + risk debate) "
            "clearly favors one direction, issue BUY or SELL accordingly. Reserve HOLD for cases where "
            "the evidence is genuinely and closely balanced, not as a safe default."
        )},
            {"role": "user", "content": prompt_text},
        ]

        # ✅ FIXED: shared retry helper instead of a copy-pasted loop that
        # could leave `final_decision` undefined if all attempts rate-limited.
        last_err = None
        final_decision = None
        for attempt in range(3):
            try:
                final_decision = invoke_structured_or_freetext(
                    structured_llm, llm, messages, render_pm_decision, "PortfolioManager"
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
        if final_decision is None:
            raise RuntimeError(f"Portfolio Manager: all retry attempts exhausted, last error: {last_err}")

        # \u2705 NEW: same code-verified gate as the Trader stage -- this is the
        # last LLM stage before Decision Verifier, so it's the last chance to
        # correct an unsupported override before it becomes the raw final_signal.
        final_decision, was_gated, gate_reason = enforce_numeric_evidence_gate(final_decision, fund_verdict)
        if was_gated:
            print(f"\u26a0\ufe0f  Numeric-evidence gate (Portfolio Manager): {gate_reason}")
        # code-only check of the RENDERED decision (cuts invented sizing / stop-loss on SELL, fixes RSI/SMA labels).
        final_decision = guard_postprocess(prompt_text, final_decision, "pm_node")

        return {
            "messages": [AIMessage(content=final_decision)],
            "final_trade_decision": final_decision,
        }

    return pm_node


portfolio_manager = create_portfolio_manager(llm_pm)

# ✅ FIXED: was full_risk_state["risk_debate_state"] = neutral_result["risk_debate_state"],
# which is the single-round DEMO debate from Cells 29-31 (1 round, 3 arguments total —
# just used there to sanity-check each debator function works). The REAL 3-round debate
# (Cell 32, 9 arguments) lives in current_risk_state / final_risk_debate_state, and
# that’s what the Risk Facilitator (Cell 33) actually judged. The Portfolio Manager was
# silently deciding on stale, incomplete debate data instead of the full debate — no
# error was thrown because both dicts have the same shape.
pm_state = {
    **current_risk_state,
    "risk_debate_state": final_risk_debate_state,
    # ✅ needed so pm_node can surface the Risk Facilitator's own verdict and
    # the Fundamentals hard fact instead of re-deriving from raw text alone.
    "risk_facilitator_decision": risk_facilitator_decision,
    "fundamentals_report": fundamentals_result["fundamentals_report"],
    "company_of_interest": COMPANY,
    "investor_profile": INVESTOR_PROFILE,
    "investment_plan": trader_result["trader_investment_plan"],
    "trader_investment_plan": trader_result["trader_investment_plan"],
    "past_context": retrieved_memory_context,
    "investment_facilitator_decision": investment_facilitator_decision,
}

pm_result = portfolio_manager(pm_state)

print(f"✅ Final Portfolio Manager Decision (Investor Profile: {INVESTOR_PROFILE}):")
from IPython.display import Markdown, display
display(Markdown(pm_result["final_trade_decision"]))

# ℹ️ Note: the PM decision is logged once, in the "FINAL SUMMARY" cell
# below (via finalize_session + log_step) — not here, to avoid the
# duplicate log entry the old cell 37 used to create.
