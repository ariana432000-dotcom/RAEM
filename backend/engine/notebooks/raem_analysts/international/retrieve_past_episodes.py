# raem_analysts/international/retrieve_past_episodes.py -- shared by AAPL, IBM, 0300_HK, 2222_SR
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 🔁 RETRIEVE PAST EPISODES *আগে* Trader চালানোর — RAEM core retrieval
# ================================================================

def _build_where_clause(company: str = None, regime_filter: str = None):
    """একাধিক condition থাকলে $and দিয়ে wrap করে (ChromaDB 1.x requirement),
    একটামাত্র condition থাকলে সরাসরি দেয়, কিছু না থাকলে None।"""
    conditions = []
    if company:
        conditions.append({"company": company})
    if regime_filter:
        conditions.append({"regime": regime_filter})
    if len(conditions) == 0:
        return None
    elif len(conditions) == 1:
        return conditions[0]
    else:
        return {"$and": conditions}


def retrieve_similar_episodes(query_text: str, company: str = None, regime_filter: str = None,
                              n_results: int = 5, as_of: str = None) -> list:
    """Similar past episodes, EXCLUDING same-day runs and anything dated on/after `as_of`
    (default TRADE_DATE) -- otherwise the Trader anchors on its own earlier runs of today."""
    as_of = as_of or globals().get("TRADE_DATE")
    where_clause = _build_where_clause(company, regime_filter)
    results = chroma_query_with_retry(
        episodic_memory,
        query_texts=[query_text],
        n_results=n_results * 4 if as_of else n_results,   # over-fetch, then filter by date
        where=where_clause,
    )
    episodes = []
    if results["ids"] and results["ids"][0]:
        for i in range(len(results["ids"][0])):
            if as_of and results["metadatas"][0][i].get("trade_date", "") >= as_of:
                continue
            episodes.append({
                "id": results["ids"][0][i],
                "document": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "distance": results["distances"][0][i],
            })
    return episodes[:n_results]


# ── বর্তমান market regime বের করা (Cell 8 থেকে market_result already আছে) ──
current_indicators = market_result.get("indicators_parsed", {})
today_regime = classify_regime(current_indicators)

print(f"📊 আজকের regime: {today_regime}")

# ── একই regime-এর past episodes retrieve করা ──
memory_query = f"@@TICKER@@ trading decision in {today_regime} market regime with technical and fundamental analysis"
past_episodes = retrieve_similar_episodes(memory_query, company="@@TICKER@@", regime_filter=today_regime, n_results=3)

if past_episodes:
    memory_lines = []
    for ep in past_episodes:
        outcome_part = ""
        if ep["metadata"].get("outcome_status") == "RESOLVED":
            outcome_part = (
                f", Outcome={ep['metadata'].get('outcome_label', 'N/A')} "
                f"(P&L: {ep['metadata'].get('pnl_pct', 'N/A')}%)"
            )
        else:
            outcome_part = ", Outcome=PENDING (too recent to resolve)"
        memory_lines.append(
            f"- {ep['metadata']['trade_date']}: Signal={ep['metadata']['final_signal']}, "
            f"RSI={ep['metadata']['rsi']}, regime={ep['metadata']['regime']}{outcome_part}"
        )
    retrieved_memory_context = (
        f"\n\nPAST EPISODES IN SIMILAR ({today_regime}) MARKET REGIME:\n"
        + "\n".join(memory_lines)
        + "\nConsider whether similar past decisions were appropriate given what regime this resembles, "
        + "and weigh in their actual outcomes where known.\n"
    )
    # ── Regime-transition reflection (আগের cell-এ তৈরি হয়েছে, এখানে inject) ──
    if regime_transition_reflection:
        retrieved_memory_context += (
            f"\nREGIME-TRANSITION REFLECTION:\n{regime_transition_reflection}\n"
        )
    if 'post_mortem_lessons' in dir() and post_mortem_lessons and not post_mortem_lessons.startswith("(Post-mortem skipped"):
        retrieved_memory_context += (
            f"\nPOST-MORTEM SELF-CRITIQUE (cross-regime, last sessions):\n{post_mortem_lessons}\n"
        )
    if 'macro_result' in dir() and macro_result.get("macro_regime"):
        retrieved_memory_context += (
            f"\nMACRO REGIME CONTEXT: {macro_result.get('macro_regime')} — {macro_result.get('macro_report','')[:300]}\n"
        )
    print(f"🧠 {len(past_episodes)} similar past episode(s) পাওয়া গেছে, Trader prompt-এ যুক্ত হবে।")
else:
    retrieved_memory_context = ""
    if regime_transition_reflection:
        retrieved_memory_context = f"\nREGIME-TRANSITION REFLECTION:\n{regime_transition_reflection}\n"
    if 'post_mortem_lessons' in dir() and post_mortem_lessons and not post_mortem_lessons.startswith("(Post-mortem skipped"):
        retrieved_memory_context += (
            f"\nPOST-MORTEM SELF-CRITIQUE (cross-regime, last sessions):\n{post_mortem_lessons}\n"
        )
    if 'macro_result' in dir() and macro_result.get("macro_regime"):
        retrieved_memory_context += (
            f"\nMACRO REGIME CONTEXT: {macro_result.get('macro_regime')} — {macro_result.get('macro_report','')[:300]}\n"
        )
    print("🧠 এই regime-এ কোনো past episode নেই, কিন্তু regime-transition reflection/post-mortem/macro context থাকতে পারে।")

# ── Graph memory (multi-hop: regime AND macro) — prepended so the 700-char cap in format_shared_context keeps it ──
graph_memory_context = ""
if globals().get("USE_GRAPH_MEMORY", False):
    try:
        _G = build_episode_graph("@@TICKER@@", TRADE_DATE)
        _macro_now = (macro_result or {}).get("macro_regime", "N/A") if "macro_result" in dir() else "N/A"
        graph_memory_context = graph_rag_context(_G, today_regime, _macro_now, topics=globals().get("today_news_topics"))
        print(f"🕸️  Graph memory: {_G.number_of_nodes()} nodes, {_G.number_of_edges()} edges -> "
              + ("context added" if graph_memory_context else "no matching episodes"))
    except Exception as _e:
        print(f"⚠️ Graph memory skipped: {str(_e)[:120]}")
if graph_memory_context:
    retrieved_memory_context = "\n" + graph_memory_context + "\n" + retrieved_memory_context
try:
    session_log["summary_table"]["graph_memory"] = graph_memory_context
except Exception:
    pass

print(retrieved_memory_context or "(empty — no memory yet)")
