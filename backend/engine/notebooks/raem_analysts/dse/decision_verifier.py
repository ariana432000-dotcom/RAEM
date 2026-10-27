# raem_analysts/dse/decision_verifier.py -- shared by abbank-dse, batbc-dse, bpml-dse, waltonhil-dse
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 🔍 DECISION VERIFIER / FACT-CHECK AGENT — নতুন Agent
# ================================================================
# Portfolio Manager-এর final decision নেওয়ার পরে চলে। ৯টা agent
# sequentially একে অপরের output-এর উপর নির্ভর করে বলে, শুরুর দিকের
# কোনো hallucination/ভুল ডেটা পুরো chain বেয়ে propagate হয়ে final
# decision পর্যন্ত পৌঁছাতে পারে — এই agent সেটা catch করার জন্য।
#
# কাজ করে দুই ধাপে:
#   1) Rule-based sanity check — RSI/news sentiment বনাম final_signal
#      সরাসরি contradict করছে কিনা
#   2) Numeric contradiction check — এখন DETERMINISTIC (Python regex +
#      tolerance), LLM-এর উপর নির্ভর করে না। ছোট local model (qwen2.5:7b)
#      decimal rounding-কে বারবার ভুলভাবে "contradiction" বলছিল (যেমন
#      68.17 vs 68.16905051254318-কে "genuine difference" বলে দিয়েছিল),
#      তাই এই arithmetic judgment আর LLM-কে দেওয়া হচ্ছে না।
#   3) LLM semantic check — শুধু SIGNAL_CONSISTENT (decision text নিজের
#      logic-এর সাথে নিজেই contradict করছে কিনা)। এটা ADVISORY — একা এটা
#      দিয়ে status FLAGGED হবে না, কারণ ৭B মডেলের এই subjective judgment-ও
#      বারবার over-trigger করছিল (হালকা hedge/caveat ভাষাকেও "inconsistent"
#      বলে দিচ্ছিল)।

import re
from langchain_ollama import ChatOllama

llm_verifier = ChatOllama(model="qwen2.5:7b", base_url="http://localhost:11434", num_ctx=8192, temperature=0, repeat_penalty=1.15, num_predict=2048)


def rule_based_checks(final_signal: str, indicators: dict, news_metrics: dict,
                       fundamentals_report: str = "") -> tuple:
    """সহজ contradiction checks চালায়। রিটার্ন করে (warnings, info_notes) —
    warnings status-কে FLAGGED করে, info_notes শুধু transparency-র জন্য
    দেখানো হয় কিন্তু status বদলায় না।
    """
    warnings = []
    info_notes = []

    fund_verdict = extract_final_proposal(fundamentals_report)
    fund_verdict_upper = fund_verdict.upper() if fund_verdict else ""
    fund_agrees_with_final = bool(fund_verdict) and final_signal in fund_verdict_upper

    def _add(msg: str):
        (info_notes if fund_agrees_with_final else warnings).append(msg)

    rsi = indicators.get("rsi")
    try:
        rsi_val = float(rsi)
        if rsi_val >= 70 and final_signal == "BUY":
            _add(f"RSI={rsi_val} overbought অঞ্চলে (>=70), কিন্তু signal BUY — সম্ভাব্য contradiction।")
        if rsi_val <= 30 and final_signal == "SELL":
            _add(f"RSI={rsi_val} oversold অঞ্চলে (<=30), কিন্তু signal SELL — সম্ভাব্য contradiction।")
    except (TypeError, ValueError):
        pass

    overall_sent = str(news_metrics.get("overall_sentiment", "")).lower()
    if "negative" in overall_sent and final_signal == "BUY":
        _add("News sentiment negative, কিন্তু signal BUY — যাচাই দরকার।")
    if "positive" in overall_sent and final_signal == "SELL":
        _add("News sentiment positive, কিন্তু signal SELL — যাচাই দরকার।")

    if fund_agrees_with_final and info_notes:
        info_notes.append(
            "ℹ️ উপরের technical/sentiment contradiction(গুলো) FLAG করা হয়নি, কারণ "
            "Fundamentals Analyst-এর নিজের HARD verdict (" + fund_verdict + ") ইতিমধ্যে "
            "final signal-এর সাথে align করে -- অর্থাৎ strong fundamentals soft "
            "sentiment/technical-কে ন্যায্যভাবে override করেছে।"
        )

    if fund_verdict:
        if "SELL" in fund_verdict_upper and final_signal == "BUY":
            warnings.append(
                "Fundamentals Analyst নিজে SELL বলেছিল (" + fund_verdict + "), কিন্তু final signal BUY -- "
                "debate/trader/PM chain এই verdict override করেছে, সরাসরি যাচাই দরকার।"
            )
        elif "BUY" in fund_verdict_upper and final_signal == "SELL":
            warnings.append(
                "Fundamentals Analyst নিজে BUY বলেছিল (" + fund_verdict + "), কিন্তু final signal SELL -- "
                "debate/trader/PM chain এই verdict override করেছে, সরাসরি যাচাই দরকার।"
            )

    return warnings, info_notes


# ── ✅ NEW: deterministic numeric-contradiction check (no LLM involved) ──
def _extract_cited_number(text: str, label_patterns: list) -> float | None:
    """Decision text-এ label-এর (যেমন RSI, MACD) ঠিক পরে যে সংখ্যাটা লেখা আছে
    সেটা বের করে। প্রথম যেটা মেলে সেটাই নেয়।"""
    for label in label_patterns:
        m = re.search(rf'{label}\s*(?:\([^)]*\))?\s*[:=]?\s*(-?\d+\.?\d*)', text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
    return None


def check_numeric_contradiction(final_decision_text: str, indicators: dict,
                                 fund_metrics: dict) -> list:
    """Decision text-এ cite করা প্রতিটা সংখ্যাকে raw ground-truth-এর সাথে
    tolerance রেখে compare করে। rounding/truncation সবসময় খুব ছোট একটা
    relative difference তৈরি করে, তাই generous tolerance ব্যবহার করলে
    'একই সংখ্যা, কম decimal' বনাম 'সত্যিকারের ভুল সংখ্যা' নির্ভরযোগ্যভাবে
    আলাদা করা যায় — এবং এখানে LLM-এর কোনো ভূমিকা নেই, তাই ভুল arithmetic
    judgment দেওয়ার সুযোগ নেই।
    """
    checks = [
        ("RSI", ["RSI"], indicators.get("rsi")),
        ("MACD", ["MACD"], indicators.get("macd")),
        ("P/E Ratio", ["P/E Ratio", "P/E"], fund_metrics.get("pe_ratio")),
        ("EPS (latest period)", ["EPS \\(TTM\\)", "EPS"], fund_metrics.get("eps_ttm")),
    ]
    mismatches = []
    for label, patterns, raw in checks:
        if raw in (None, "", "N/A"):
            continue
        try:
            raw_val = float(raw)
        except (TypeError, ValueError):
            continue
        cited = _extract_cited_number(final_decision_text, patterns)
        if cited is None:
            continue
        diff = abs(cited - raw_val)
        tolerance = max(0.5, 0.02 * abs(raw_val))  # rounding-tolerant: 2% relative or 0.5 absolute
        if diff > tolerance:
            mismatches.append(
                f"{label}: decision text-এ লেখা {cited}, কিন্তু raw data অনুযায়ী {raw_val} "
                f"(diff={diff:.2f}, tolerance={tolerance:.2f}) — সম্ভাব্য genuine mismatch।"
            )
    return mismatches


def check_fact_semantics(final_decision_text: str, indicators: dict, fund_metrics: dict, market_result: dict) -> list:
    """Code-computed checks for the errors the numeric-presence check cannot see (found in a real run):
    SMA called 'support' while price is below it, an N/A dividend yield cited as 0%, 'no dividend' next to a long dividend
    history, missing DSEX data read as 'neutral', and the GNN rank called a 'sector position'."""
    out, text = [], str(final_decision_text)
    try:
        px = None
        for line in str((market_result.get("market_raw_data") or {}).get("stock_data", "")).splitlines():
            p = [x.strip() for x in line.split(",")]
            if len(p) >= 5 and re.match(r"\d{4}-\d{2}-\d{2}$", p[0]):
                px = float(p[4])
        if px is None:
            px = float(fund_metrics.get("last_trading_price"))
    except Exception:
        px = None
    sma = indicators.get("close_50_sma")
    neg = re.compile(r"\b(not|no|isn't|doesn't|does not|fails?|failed|without|lack|break(?:s|ing)?\s+(?:below|through|above)|if|until|unless)\b", re.I)
    if px is not None and isinstance(sma, (int, float)) and sma:
        for sent in re.split(r"(?<=[.!?])\s+|\n", text):
            sl = sent.lower()
            if ("sma" in sl or "moving average" in sl) and re.search(r"stop[- ]?loss", sl) and px < sma and not re.search(r"\\b(above|over)\\b", sl):
                out.append(f"Stop-loss: placed at the 50-day SMA {sma}, which is ABOVE the price {px} (invalid for a long position): \"{sent.strip()[:110]}\"")
                continue
            if not ("sma" in sl or "moving average" in sl) or neg.search(sl):
                continue
            if re.search(r"\bsupport", sl) and px < sma:
                out.append(f"SMA direction: text calls the 50-day SMA {sma} support, but price {px} is BELOW it (the SMA is resistance): \"{sent.strip()[:110]}\"")
            elif re.search(r"\bresistance", sl) and px > sma:
                out.append(f"SMA direction: text calls the 50-day SMA {sma} resistance, but price {px} is ABOVE it (the SMA is support): \"{sent.strip()[:110]}\"")
    dy = str(fund_metrics.get("dividend_yield", "N/A"))
    if dy in ("N/A", "None", "") and re.search(r"dividend yield[^.\n]{0,25}?\b0(?:\.0+)?\s*%", text, re.I):
        out.append("Dividend yield: the data field is N/A (DSE publishes no yield) but the text cites it as 0%.")
    cash = str(fund_metrics.get("cash_dividend", ""))
    if re.search(r"\d+\s*%", cash) and re.search(r"\b(lack of|no|zero|without|absence of)\s+(?:a\s+)?(?:cash\s+)?dividend", text, re.I):
        out.append(f"Dividend: text claims no dividend, but the DSE history shows payouts ({cash[:60]}).")
    mres = globals().get("macro_result") or {}
    if mres.get("dsex_data_ok") is False and re.search(r"(absence|lack)\s+of[^.\n]{0,80}(volatil|trend|data)[^.\n]{0,100}\bneutral|\bno\s+(?:realized\s+)?volatil[^.\n]{0,80}\bneutral", text, re.I):
        out.append("Macro: DSEX data was UNAVAILABLE, but the text reads that absence as a 'neutral' market (missing data is unknown, not neutral).")
    if re.search(r"sector\s+(?:position|rank)[^.\n]{0,25}\d+(?:st|nd|rd|th)?\s*(?:/|out of)\s*\d+", text, re.I):
        out.append("GNN: the rank is across ALL graph stocks, not a sector position/rank.")
    return out


def llm_signal_consistency_check(final_decision_text: str, llm) -> str:
    """শুধু SIGNAL_CONSISTENT check করে LLM দিয়ে — এটা genuinely subjective/
    semantic একটা প্রশ্ন, তাই এখানেই LLM রাখা হয়েছে। কিন্তু এর ফলাফল
    এখন advisory-only (নিচে run_decision_verifier দেখুন)।"""
    prompt = f"""You are a fact-checking auditor reviewing a trading decision before it is finalized.

=== FINAL DECISION TEXT ===
{final_decision_text[:1200]}

Check ONLY this one thing:

Respond with ONE strict marker line FIRST (machine-parsed), THEN your explanation
— do not paraphrase the marker:
SIGNAL_CONSISTENT: YES or NO

RULES (read carefully before answering):
- HOLD is a legitimate, self-consistent conclusion whenever the inputs are mixed --
  e.g. overbought/oversold technicals pulling one way while fundamentals or news
  sentiment pull the other way. Mentioning a caution/risk factor and then still
  making a call (BUY/HOLD/SELL) is NORMAL trading commentary, not an inconsistency.
- Only answer NO if the decision text's OWN stated reasoning DIRECTLY and STRONGLY
  contradicts its OWN action -- e.g. the text says "this is a clear sell signal" or
  "there is no reason to buy" but then names BUY as the action. A hedge like
  "shows some overbought risk but still has growth potential" followed by BUY is
  CONSISTENT, not a contradiction -- do not flag it.
- Default to YES unless the contradiction is obvious and severe.

Then, in 1-2 short bullet points, if SIGNAL_CONSISTENT is NO, quote the specific
phrase in the decision text whose own reasoning contradicts its own action.

Be concise and only flag REAL, severe discrepancies -- do not invent problems.
"""
    try:
        response = llm.invoke(prompt)
        return response.content.strip()
    except Exception as e:
        return f"(LLM fact-check failed: {e})"


def run_decision_verifier(final_decision_text: str, market_result: dict,
                           fundamentals_result: dict, news_result: dict,
                           sentiment_result: dict, llm) -> dict:
    """Rule-based + deterministic numeric check + LLM semantic check চালায়,
    VERIFIED/FLAGGED status রিটার্ন করে।"""
    indicators = market_result.get("indicators_parsed", {})
    fund_metrics = fundamentals_result.get("fund_metrics", {})
    news_metrics = news_result.get("news_metrics", {})
    sentiment_metrics = sentiment_result.get("sentiment_metrics", {})

    sig_match = re.search(r"\*{0,2}(BUY|HOLD|SELL)\*{0,2}", final_decision_text, re.IGNORECASE)
    final_signal = sig_match.group(1).upper() if sig_match else "N/A"

    # v8: the verdict only reaches the verifier if FUND_NUMBERS_ONLY is off or FUND_VERDICT_TO_VERIFIER is on
    _fr_for_verifier = fundamentals_result.get("fundamentals_report", "")
    if (not FUND_NUMBERS_ONLY) or FUND_VERDICT_TO_VERIFIER:
        _fr_for_verifier = fundamentals_result.get("fundamentals_report_llm") or _fr_for_verifier
    rule_warnings, rule_info_notes = rule_based_checks(
        final_signal, indicators, news_metrics,
        fundamentals_report=_fr_for_verifier,
    )

    # ✅ NEW: numeric contradiction is now code-computed, not LLM-judged
    numeric_mismatches = check_numeric_contradiction(final_decision_text, indicators, fund_metrics)
    numeric_mismatches += check_fact_semantics(final_decision_text, indicators, fund_metrics, market_result)

    llm_notes = llm_signal_consistency_check(final_decision_text, llm)

    llm_call_failed = "(llm fact-check failed" in llm_notes.lower()
    signal_consistent_m = re.search(r"SIGNAL_CONSISTENT:\s*(YES|NO)", llm_notes, re.IGNORECASE)
    signal_inconsistent = bool(signal_consistent_m and signal_consistent_m.group(1).upper() == "NO")
    markers_missing = signal_consistent_m is None

    # ✅ status এখন শুধু hard/reliable signal দিয়ে ঠিক হয়: rule-based
    # contradiction, code-verified numeric mismatch, বা LLM call fail/format
    # miss। LLM-এর SIGNAL_CONSISTENT opinion আর একা status FLAGGED করে না —
    # ৭B মডেলের এই subjective judgment বারবার over-trigger করছিল।
    status = "FLAGGED" if (rule_warnings or numeric_mismatches or llm_call_failed or markers_missing) else "VERIFIED"

    notes_parts = []
    if rule_warnings:
        notes_parts.append("Rule-based: " + " | ".join(rule_warnings))
    if rule_info_notes:
        notes_parts.append("Rule-based (info, non-flagging): " + " | ".join(rule_info_notes))
    if numeric_mismatches:
        notes_parts.append("Numeric check (code, non-LLM): " + " | ".join(numeric_mismatches))
    notes_parts.append(
        "LLM semantic check (advisory, does NOT affect status): " + llm_notes
    )
    notes = "\n".join(notes_parts)

    fundamentals_contradiction = any("Fundamentals Analyst নিজে" in w for w in rule_warnings)
    effective_signal = final_signal
    if fundamentals_contradiction and final_signal != "HOLD":
        effective_signal = "HOLD"
        notes += (
            f"\n\n[LOCKED] AUTO-OVERRIDE: Fundamentals Analyst-এর verdict-এর সাথে সরাসরি contradiction "
            f"পাওয়া গেছে -- raw signal ছিল {final_signal}, কিন্তু effective/actionable signal জোর করে "
            f"HOLD-এ নামিয়ে দেওয়া হলো। কারণ ছাড়া trade না করাই নিরাপদ।"
        )

    return {
        "status": status,
        "notes": notes,
        "final_signal": final_signal,
        "effective_signal": effective_signal,
        "auto_overridden": fundamentals_contradiction and final_signal != "HOLD",
    }


# ── ACTUALLY RUN IT ──
print("=" * 60)
print("🔍 DECISION VERIFIER / FACT-CHECK")
print("=" * 60)

verification_result = run_decision_verifier(
    final_decision_text, market_result, fundamentals_result,
    news_result, sentiment_result, llm_verifier,
)

status_icon = "✅" if verification_result["status"] == "VERIFIED" else "⚠️"
print(f"{status_icon} Verification Status: {verification_result['status']}")
print(f"\n📋 Notes:\n{verification_result['notes']}")
if verification_result["auto_overridden"]:
    print(f"\n[LOCKED] ACTIONABLE SIGNAL: {verification_result['effective_signal']} "
          f"(raw model output was {verification_result['final_signal']}, auto-downgraded)")
else:
    print(f"\n[OK] ACTIONABLE SIGNAL: {verification_result['effective_signal']}")
print("=" * 60)
print()

log_step(
    "Decision Verifier",
    {"final_signal": verification_result["final_signal"]},
    {
        "status": verification_result["status"],
        "notes": verification_result["notes"],
        "effective_signal": verification_result["effective_signal"],
        "auto_overridden": verification_result["auto_overridden"],
    },
)
