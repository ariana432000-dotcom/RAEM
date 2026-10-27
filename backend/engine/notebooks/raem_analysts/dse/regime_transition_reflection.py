# raem_analysts/dse/regime_transition_reflection.py -- shared by abbank-dse, batbc-dse, bpml-dse, waltonhil-dse
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 🔄 REGIME-TRANSITION REFLECTION — RAEM-এর core novelty অংশ
# ================================================================
# এই cell আজকের regime বের করে, সবচেয়ে সাম্প্রতিক past episode-এর regime-এর
# সাথে তুলনা করে। যদি regime বদলে যায়, তাহলে আগের regime-এর RESOLVED
# episodes (যেগুলোতে outcome জানা আছে) থেকে একটা ছোট LLM reflection
# চালিয়ে actionable insight বানায়।
#
# এটাই FinMem/FinAgent-এর সাধারণ episodic memory থেকে RAEM-কে আলাদা করে —
# শুধু "similar episode খুঁজে আনা" না, "regime বদলের সময় outcome-aware
# সতর্কতা তৈরি করা।"

def get_most_recent_episode_regime(company, exclude_date=None):
    """সবচেয়ে সাম্প্রতিক saved episode-এর regime + trade_date রিটার্ন করে।"""
    try:
        all_eps = episodic_memory.get(where={"company": company})
    except Exception:
        return None, None
    if not all_eps["ids"]:
        return None, None

    dated = []
    for m in all_eps["metadatas"]:
        td = m.get("trade_date", "")
        if (not exclude_date) or td < exclude_date:   # strictly EARLIER days only (no same-day / future runs)
            # ✅ CHANGED: same day now can have several episodes (multiple
            # runs, e.g. every 2 hours) — sort by (date, session_id) so the
            # truly LATEST run of that day wins, not an arbitrary one.
            dated.append((td, m.get("session_id", ""), m.get("regime", "")))
    if not dated:
        return None, None
    dated.sort(key=lambda x: (x[0], x[1]))
    return dated[-1][2], dated[-1][0]


def reflect_on_regime_transition(company, previous_regime, new_regime, llm):
    """
    আগের regime-এর RESOLVED (outcome জানা) episodes টেনে এনে LLM দিয়ে
    একটা reflection paragraph বানায় — Trader prompt-এ ইনজেক্ট হবে।
    """
    try:
        resolved = episodic_memory.get(
            where={"$and": [
                {"company": company},
                {"regime": previous_regime},
                {"outcome_status": "RESOLVED"},
            ]}
        )
    except Exception as e:
        return f"(Reflection skipped due to query error: {e})"

    _asof = globals().get("TRADE_DATE")
    if _asof:   # only outcomes known strictly before the as-of date
        _keep = [i for i, m in enumerate(resolved["metadatas"]) if m.get("trade_date", "") < _asof]
        resolved = {"ids": [resolved["ids"][i] for i in _keep],
                    "metadatas": [resolved["metadatas"][i] for i in _keep]}
    if not resolved["ids"]:
        return (
            f"(Regime changed {previous_regime} -> {new_regime}, but no RESOLVED "
            f"outcomes exist yet for {previous_regime} to reflect on.)"
        )

    lines = []
    for meta in resolved["metadatas"]:
        trade_date_val = meta.get("trade_date", "?")
        signal_val = meta.get("final_signal", "?")
        pnl_val = meta.get("pnl_pct", "?")
        outcome_val = meta.get("outcome_label", "?")
        lines.append(f"- {trade_date_val}: Signal={signal_val}, P&L={pnl_val}%, Outcome={outcome_val}")
    history_text = chr(10).join(lines)

    prompt = (
        f"You are a trading reflection agent. The market regime for {company} has just "
        f"transitioned from {previous_regime} to {new_regime}.\n\n"
        f"Here is the outcome history of past decisions made during the {previous_regime} regime:\n"
        f"{history_text}\n\n"
        f"In 3-4 concise sentences, reflect on:\n"
        f"1. Whether decisions in {previous_regime} tended to be profitable or not.\n"
        f"2. What that suggests about decision-making as we now enter a {new_regime} regime.\n"
        f"3. One concrete caution or adjustment for the trader to consider.\n\n"
        f"Be specific and reference the actual P&L numbers above. Do not invent data not shown."
    )

    def _call():
        try:
            response = llm.invoke(prompt)
            return response.content.strip()
        except Exception as e:
            return f"(Reflection LLM call failed: {e})"

    _no_info = (f"(Regime changed {previous_regime} -> {new_regime}; the {len(resolved['metadatas'])} resolved decision(s) in {previous_regime} all show "
                f"0.00% P&L (HOLD records no P&L), so there is no outcome evidence to reflect on.)")
    return gated_outcome_llm(f"reflection:{company}:{previous_regime}->{new_regime}", resolved["metadatas"], _call, _no_info, "Reflection")


# ── ACTUALLY RUN IT ──
print("=" * 60)
print("🔄 REGIME-TRANSITION CHECK")
print("=" * 60)

_today_indicators = market_result.get("indicators_parsed", {})
_today_regime_check = classify_regime(_today_indicators)
_prev_regime, _prev_date = get_most_recent_episode_regime("@@TICKER@@", exclude_date=TRADE_DATE)

regime_transition_reflection = ""

if _prev_regime is None:
    print("   ℹ️  কোনো আগের episode নেই — এটাই প্রথম দিন, reflection প্রযোজ্য না।")
elif _prev_regime == _today_regime_check:
    print(f"   ℹ️  Regime অপরিবর্তিত ({_prev_regime}) — কোনো transition reflection দরকার নেই।")
else:
    print(f"   🔁 Regime পরিবর্তন শনাক্ত: {_prev_regime} ({_prev_date}) -> {_today_regime_check} (আজ)")
    regime_transition_reflection = reflect_on_regime_transition(
        company="@@TICKER@@",
        previous_regime=_prev_regime,
        new_regime=_today_regime_check,
        llm=llm,
    )
    print(f"\n   💭 Reflection:\n   {regime_transition_reflection}")

print("=" * 60)
print()

