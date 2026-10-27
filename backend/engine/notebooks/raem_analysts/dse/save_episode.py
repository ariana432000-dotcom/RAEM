# raem_analysts/dse/save_episode.py -- shared by abbank-dse, batbc-dse, bpml-dse, waltonhil-dse
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 💾 SAVE EPISODE TO CHROMADB + RETRIEVE SIMILAR PAST EPISODES
# ================================================================
import re

def build_episode_document(
    company, trade_date, market_result, fundamentals_result,
    news_result, sentiment_result, trader_result, final_decision_text,
    macro_result=None, verification_result=None,
):
    """Returns (document_text, metadata_dict)."""
    indicators = market_result.get("indicators_parsed", {})
    regime = classify_regime(indicators)

    # ── Entry price — পরে outcome backfill-এর জন্য দরকার ──
    raw_stock_data = market_result.get("market_raw_data", {}).get("stock_data", "")
    entry_price = None
    _csv_lines = [l for l in str(raw_stock_data).split("\n") if l.strip() and not l.startswith("#")]
    if len(_csv_lines) > 1:
        for _row in reversed(_csv_lines[1:]):
            _parts = _row.split(",")
            if len(_parts) >= 5 and _parts[4].strip() and _parts[4].strip() not in ("", "Close"):
                try:
                    entry_price = float(_parts[4].strip())
                    break
                except ValueError:
                    pass

    sig_match = re.search(r"\*{0,2}(BUY|HOLD|SELL)\*{0,2}", final_decision_text, re.IGNORECASE)
    final_signal = sig_match.group(1).upper() if sig_match else "N/A"

    fund_metrics = fundamentals_result.get("fund_metrics", {})
    news_metrics = news_result.get("news_metrics", {})
    sentiment_metrics = sentiment_result.get("sentiment_metrics", {})

    macro_regime_tag = (macro_result or {}).get("macro_regime", "N/A")
    verify_status = (verification_result or {}).get("status", "N/A")
    verify_notes = (verification_result or {}).get("notes", "")

    document_text = f"""Trading Episode: {company} on {trade_date}
Market Regime: {regime}
Macro Regime: {macro_regime_tag}
Technical Indicators: RSI={indicators.get('rsi', 'N/A')}, MACD={indicators.get('macd', 'N/A')}, 50-SMA={indicators.get('close_50_sma', 'N/A')}
Fundamentals: P/E={fund_metrics.get('pe_ratio', 'N/A')}, EPS={fund_metrics.get('eps_ttm', 'N/A')}, Market Cap={fund_metrics.get('market_cap', 'N/A')}
News Sentiment: {news_metrics.get('overall_sentiment', 'N/A')} (Pos:{news_metrics.get('positive_count', 0)} Neg:{news_metrics.get('negative_count', 0)} Neu:{news_metrics.get('neutral_count', 0)})
Social Sentiment Score: {sentiment_metrics.get('score', 'N/A')}/10 ({sentiment_metrics.get('overall', 'N/A')})
Final Decision: {final_signal}
Verification Status: {verify_status}
Decision Rationale Summary: {final_decision_text[:400]}
"""

    metadata = {
        "company": company,
        "trade_date": trade_date,
        "regime": regime,
        "macro_regime": macro_regime_tag,
        "verification_status": verify_status,
        "verification_notes": verify_notes[:300],
        "final_signal": final_signal,
        "rsi": str(indicators.get("rsi", "N/A")),
        "macd": str(indicators.get("macd", "N/A")),
        "pe_ratio": str(fund_metrics.get("pe_ratio", "N/A")),
        "news_sentiment": str(news_metrics.get("overall_sentiment", "N/A")),
        "entry_price": str(entry_price) if entry_price is not None else "N/A",
        "outcome_status": "PENDING",   # ← outcome backfill cell পরে এটা RESOLVED করবে
        "exit_price": "N/A",
        "pnl_pct": "N/A",
        "outcome_label": "N/A",
        # news-graph topics seen in today's company news (episode graph: Episode -had_topic-> TOPIC:x)
        "news_topics": ",".join(globals().get("today_news_topics", []) or []) or "NONE",
        # ✅ NEW: which run (of possibly several per day) this episode is from
        "session_id": SESSION_ID,
        # ✅ NEW: Graph Analyst signal, so resolved outcomes can later be used to check the GNN
        "graph_gnn_status": str(globals().get("graph_result", {}).get("metrics", {}).get("gnn_status", "N/A")),
        "graph_gnn_rank": str(globals().get("graph_result", {}).get("metrics", {}).get("gnn_rank", "N/A")),
        "sizing_final_weight": str(globals().get("sizing_result", {}).get("final_weight", "N/A")),
        "sizing_action": str(globals().get("sizing_result", {}).get("action", "N/A")),
        "sizing_rl_status": str(globals().get("sizing_result", {}).get("rl", {}).get("status", "N/A")),
    }

    return document_text.strip(), metadata


def save_episode_to_memory(
    company, trade_date, market_result, fundamentals_result,
    news_result, sentiment_result, trader_result, final_decision_text,
    macro_result=None, verification_result=None,
) -> str:
    """Episode বানিয়ে ChromaDB-তে upsert করে। Returns regime tag।"""
    doc_text, metadata = build_episode_document(
        company, trade_date, market_result, fundamentals_result,
        news_result, sentiment_result, trader_result, final_decision_text,
        macro_result=macro_result, verification_result=verification_result,
    )
    # ✅ CHANGED: pass SESSION_ID so every run (e.g. every 2 hours) creates
    # its own episode instead of overwriting the same day's earlier run.
    episode_id = make_episode_id(company, trade_date, run_id=SESSION_ID)

    episodic_memory.upsert(ids=[episode_id], documents=[doc_text], metadatas=[metadata])

    print(f"💾 Episode saved to memory: {company} | {trade_date} | Regime: {metadata['regime']} | Signal: {metadata['final_signal']}")
    print(f"   Total episodes in memory: {episodic_memory.count()}")
    return metadata["regime"]


# ── ACTUALLY RUN IT ──
current_regime = save_episode_to_memory(
    company="@@TICKER@@",
    trade_date=TRADE_DATE,
    market_result=market_result,
    fundamentals_result=fundamentals_result,
    news_result=news_result,
    sentiment_result=sentiment_result,
    trader_result=trader_result,
    final_decision_text=final_decision_text,
    macro_result=macro_result if 'macro_result' in dir() else None,
    verification_result=verification_result if 'verification_result' in dir() else None,
)

# ── Demo: একই regime-এর সবচেয়ে similar past episode খোঁজা ──
print(f"\n{'='*60}")
print(f"🔍 RETRIEVING SIMILAR PAST EPISODES (Regime: {current_regime})")
print(f"{'='*60}")

query = f"@@TICKER@@ trading decision in {current_regime} market regime with technical and fundamental analysis"
similar = retrieve_similar_episodes(query, company="@@TICKER@@", regime_filter=current_regime, n_results=3)

if similar:
    for ep in similar:
        print(f"\n📅 {ep['metadata']['trade_date']} | Signal: {ep['metadata']['final_signal']} | Distance: {ep['distance']:.3f}")
        print(f"   {ep['document'][:1000]}...")
else:
    print("   (এই regime-এ এখনো কোনো past episode নেই — সম্ভবত এটাই প্রথম episode)")

print(f"\n{'='*60}\n")

