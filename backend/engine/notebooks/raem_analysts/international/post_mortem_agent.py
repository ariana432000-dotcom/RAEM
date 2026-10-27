# raem_analysts/international/post_mortem_agent.py -- shared by AAPL, IBM, 0300_HK, 2222_SR
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 🧾 POST-MORTEM / SELF-CRITIQUE AGENT — নতুন Agent
# ================================================================
# Regime-Transition Reflection (আগের cell) শুধু regime বদলালে চলে এবং
# শুধু আগের regime-এর episode দেখে। এই agent প্রতি session-এ চলে এবং
# কোম্পানির সব RESOLVED episode (যেকোনো regime জুড়ে) থেকে structured
# self-critique বানায় — কোথায় সিস্টেম বেশি ভুল করছে, over-aggressive
# নাকি over-conservative, ইত্যাদি। এই lesson Trader + Portfolio Manager
# দুই জায়গাতেই inject হবে retrieved_memory_context-এর মাধ্যমে।

llm_postmortem = ChatOllama(model="qwen2.5:7b", base_url="http://localhost:11434", num_ctx=8192, temperature=0, repeat_penalty=1.15, num_predict=2048)


def _safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def gather_resolved_episodes(company: str, max_n: int = 15) -> list:
    """কোম্পানির সব RESOLVED episode আনে (regime filter ছাড়া), recent-first sort করে।"""
    try:
        resolved = episodic_memory.get(where={"$and": [
            {"company": company},
            {"outcome_status": "RESOLVED"},
        ]})
    except Exception:
        return []
    if not resolved["ids"]:
        return []
    # ✅ CHANGED: secondary sort by session_id so multiple same-day runs
    # order correctly (most recent run first) instead of arbitrary tie order.
    rows = list(zip(resolved["ids"], resolved["metadatas"]))
    _asof = globals().get("TRADE_DATE")
    if _asof:   # no same-day / future episodes in the post-mortem
        rows = [r for r in rows if r[1].get("trade_date", "") < _asof]
    rows.sort(key=lambda r: (r[1].get("trade_date", ""), r[1].get("session_id", "")), reverse=True)
    return [m for _, m in rows[:max_n]]


def run_post_mortem(company: str, llm) -> str:
    """সব RESOLVED episode থেকে structured self-critique বানায় (cross-regime)।"""
    episodes = gather_resolved_episodes(company)
    if not episodes:
        return "(Post-mortem skipped — no RESOLVED episodes yet.)"

    wins   = [e for e in episodes if _safe_float(e.get("pnl_pct")) > 0]
    losses = [e for e in episodes if _safe_float(e.get("pnl_pct")) < 0]

    lines = []
    for e in episodes:
        lines.append(
            f"- {e.get('trade_date','?')}: Signal={e.get('final_signal','?')}, "
            f"Regime={e.get('regime','?')}, P&L={e.get('pnl_pct','?')}%, "
            f"Outcome={e.get('outcome_label','?')}"
        )
    history_text = chr(10).join(lines)

    prompt = (
        f"You are a trading post-mortem analyst reviewing {company}'s last {len(episodes)} "
        f"RESOLVED trading decisions across all market regimes:\n\n{history_text}\n\n"
        f"Win count: {len(wins)}, Loss count: {len(losses)}\n\n"
        f"In 4-5 concise bullet points, identify:\n"
        f"1. Any pattern in which regimes/signals tend to lose money.\n"
        f"2. Whether the system is systematically too aggressive or too conservative.\n"
        f"3. One concrete process adjustment for future Trader/Portfolio Manager decisions.\n"
        f"Base this ONLY on the data above — do not invent numbers."
    )

    def _call():
        try:
            response = llm.invoke(prompt)
            return response.content.strip()
        except Exception as e:
            return f"(Post-mortem LLM call failed: {e})"

    _sigs = ", ".join(f"{k} x{v}" for k, v in sorted(_Counter(str(e.get('final_signal', '?')) for e in episodes).items()))
    _no_info = (f"(Post-mortem: {len(episodes)} resolved decision(s) ({_sigs}) but every recorded P&L is 0.00% -- HOLD decisions record no P&L -- "
                f"so there is no outcome evidence to draw lessons from. No lessons drawn.)")
    return gated_outcome_llm(f"postmortem:{company}", episodes, _call, _no_info, "Post-mortem")


# ── ACTUALLY RUN IT ──
print("=" * 60)
print("🧾 POST-MORTEM / SELF-CRITIQUE")
print("=" * 60)

post_mortem_lessons = run_post_mortem("@@TICKER@@", llm_postmortem)
print(f"\n💭 Lessons:\n{post_mortem_lessons}")
print("=" * 60)
print()

log_step(
    "Post-Mortem Self-Critique",
    {"company": "@@TICKER@@", "scope": "all RESOLVED episodes, cross-regime"},
    {"lessons": post_mortem_lessons},
)
