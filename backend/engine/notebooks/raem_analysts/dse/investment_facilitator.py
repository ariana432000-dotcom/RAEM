# raem_analysts/dse/investment_facilitator.py -- shared by abbank-dse, batbc-dse, bpml-dse, waltonhil-dse
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
from langchain_ollama import ChatOllama
from tradingagents.agents.utils.agent_utils import get_language_instruction

llm_facilitator = ChatOllama(model="qwen2.5:7b", base_url="http://localhost:11434", num_ctx=8192, temperature=0.2, repeat_penalty=1.15, num_predict=2048)

import re

_NUM_RX = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?%?")


def _cited_numbers(text) -> set:
    """Specific numbers in a debater's text (years, list numbers and tiny integers are ignored)."""
    out = set()
    for m in _NUM_RX.finditer(str(text)):
        tok = m.group(0).replace(",", "").lstrip("+")
        core = tok.rstrip("%").lstrip("-")
        if re.fullmatch(r"(19|20)\d{2}", core):
            continue
        if "." not in core and "%" not in tok and len(core) < 3:
            continue
        out.add(tok)
    return out


def compute_debate_evidence(bull_text, bear_text) -> str:
    """Evidence counts from each side's OWN text. The 7B facilitator used to 'list' the numbers each side cited and
    copied the same 12-item list for both (12 vs 12 -> DRAW); counting is now done in code."""
    b, r = _cited_numbers(bull_text), _cited_numbers(bear_text)
    fmt = lambda xs: ", ".join(sorted(xs)[:12]) or "none"
    return (f"- Bull cited {len(b)} distinct specific numbers ({len(b - r)} unique to Bull): {fmt(b - r)}\n"
            f"- Bear cited {len(r)} distinct specific numbers ({len(r - b)} unique to Bear): {fmt(r - b)}\n"
            f"- Cited by BOTH sides ({len(b & r)}): {fmt(b & r)}")


def create_investment_facilitator(llm):
    def facilitator_node(state):
        investment_debate_state = state["investment_debate_state"]
        history = investment_debate_state.get("history", "")
        bull_history = investment_debate_state.get("bull_history", "")
        bear_history = investment_debate_state.get("bear_history", "")
        count = investment_debate_state.get("count", 0)

        # \u2705 FIXED: pull the Fundamentals Analyst's own hard verdict and put it
        # RIGHT NEXT TO the scoring instructions, not buried inside the debate text.
        # Observed failure mode: the local model (Mistral) kept declaring BULL WINS
        # while explicitly penalizing Bear for "not offering a path to recovery" \u2014
        # the exact bias the old prompt already told it not to do. A named-and-scored
        # rubric plus a calibration example gives a small model something concrete to
        # pattern-match instead of a soft "do not X" instruction it keeps ignoring.
        fund_verdict = extract_final_proposal(state.get("fundamentals_report", ""))
        shared_context = format_shared_context(state, news=True, macro=True, memory=False)
        ev_block = compute_debate_evidence(bull_history, bear_history)
        _fv = bool(fund_verdict)
        fv_block = ("HARD FACT (from the Fundamentals Analyst\'s own report, not from either debater\u2019s spin):\n" + fund_verdict + "\n\n") if _fv else ""
        fv_rules = '4. If the HARD FACT above is SELL, the Bull side must cite at least 2 specific numbers that directly offset it (e.g. a concrete path to profitability, contract value, cash runway) to be scored higher than Bear. If Bull cannot, Bear wins by default regardless of tone.\n5. If the HARD FACT above is BUY, apply the same rule symmetrically to the Bear side.' if _fv else '4. No analyst verdict is supplied: decide only from the evidence counts above and the specific numbers each side cites.'
        fv_calib = 'CALIBRATION EXAMPLE (for format/reasoning style only, not this stock\'s data):\nHARD FACT: FINAL TRANSACTION PROPOSAL: SELL\nBull cited: "strong long-term growth story", "expanding partnerships" (0 specific numbers)\nBear cited: net income -$80M, debt $400M, RSI 74 overbought (3 specific numbers)\n-> bull_evidence_count=0, bear_evidence_count=3. Bull did not offset the SELL verdict with numbers.\n-> Evaluation: BEAR WINS. Final Recommendation: SELL (Medium confidence).' if _fv else 'CALIBRATION EXAMPLE (for format/reasoning style only, not this stock\'s data):\nBull cited: "strong long-term growth story", "expanding partnerships" (0 specific numbers)\nBear cited: net income -$80M, debt $400M, RSI 74 overbought (3 specific numbers)\n-> bull_evidence_count=0, bear_evidence_count=3.\n-> Evaluation: BEAR WINS. Final Recommendation: SELL (Medium confidence).'
        fv_tail = ", and how the HARD FACT was handled" if _fv else ""

        prompt = f"""IMPORTANT: Base your evaluation ONLY on the debate history provided below. Do NOT add external knowledge.\nYou are the Investment Debate Facilitator. Your job is to:
1. Review the full debate between Bull and Bear analysts
2. Objectively evaluate which side presented stronger evidence
3. Declare a WINNER with clear reasoning
4. Provide a final investment recommendation

{fv_block}SUPPORTING CONTEXT (use ONLY to fact-check claims made in the debate, e.g. a cited catalyst or notice; do NOT use it to add new arguments, and market-wide notices about other companies are not news about this stock):
{shared_context}

Full Debate History:
{history}

Total arguments made: {count}

SCORING RUBRIC \u2014 do this before declaring a winner:
1. EVIDENCE COUNTS (computed in code from each side's own text -- do NOT recount or re-list them; copy bull_evidence_count and bear_evidence_count from here):
{ev_block}
2. Numbers cited by BOTH sides do not discriminate between them; weigh the numbers unique to each side.
3. Vague phrases ("growth potential", "long-term prospects", "strategic partnerships", "path to recovery", "investor confidence") are NOT evidence \u2014 do not count them, and do not let them substitute for a missing number on either side.
{fv_rules}

IMPORTANT judging rules:
- Do NOT reward a side merely for sounding hopeful, offering a "path to recovery", or proposing adaptability  -  investment risk assessment is about evidence, not morale. A well-evidenced bearish case must NOT be marked weaker just because it "doesn't offer solutions".
- If a side attributes a viewpoint to a source (e.g. "analysts from X believe...") that contradicts what that same source is quoted as saying elsewhere in the debate or reports, treat this as a factual error and count it heavily against that side, regardless of how persuasive the writing is.

{fv_calib}

IMPORTANT: Weighing both sides fairly does not mean defaulting to DRAW or HOLD to seem balanced. If, after fair evaluation, one side is genuinely better supported by the reports, declare that side the winner and recommend BUY or SELL accordingly. Reserve DRAW/HOLD for cases where the evidence is truly and closely matched on both sides.

First show your scoring (bull_evidence_count, bear_evidence_count{fv_tail}), THEN declare:
Declare: BULL WINS / BEAR WINS / DRAW
Then give your final recommendation: BUY / SELL / HOLD with confidence level (High/Medium/Low).
""" + get_language_instruction()

        response = llm.invoke(prompt)
        decision = response.content

        print("=" * 60)
        print("⚖️  INVESTMENT DEBATE FACILITATOR DECISION")
        print("=" * 60)
        print(decision)

        return {
            "investment_debate_state": {
                **investment_debate_state,
                "facilitator_decision": decision,
            },
            "investment_facilitator_decision": decision,
        }
    return facilitator_node


investment_facilitator = create_investment_facilitator(llm_facilitator)
print("Investment Facilitator ready! ✅")
