# raem_analysts/international/graph_memory.py -- shared by AAPL, IBM, 0300_HK, 2222_SR
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 🕸️ GRAPH MEMORY — graph-RAG over episodic memory (no LLM calls, no new storage)
# ================================================================
# The graph is rebuilt from episode METADATA in ChromaDB on every run (~tens of nodes, instant):
#   Episode -in_regime-> REGIME:<tag>     Episode -in_macro-> MACRO:<tag>
# Each Episode node carries its signal / outcome status / P&L. Retrieval is a multi-hop query
# (same regime AND same macro regime), relaxed to regime-only, then macro-only, if empty.
# The summary is PREPENDED to retrieved_memory_context in the next cell, because
# format_shared_context() keeps only the first 700 chars of memory.
# Ablation switch for the thesis: USE_GRAPH_MEMORY = False reproduces the old behaviour.
import networkx as nx
from collections import defaultdict

USE_GRAPH_MEMORY = True


def build_episode_graph(company: str, as_of: str):
    got = episodic_memory.get(where={"company": company})
    G = nx.MultiDiGraph()
    for ep_id, m in zip(got["ids"], got["metadatas"]):
        if m.get("trade_date", "") >= as_of:          # same leakage guard as the rest of the pipeline
            continue
        ep = f"EP:{ep_id}"
        G.add_node(ep, date=m.get("trade_date"), signal=m.get("final_signal", "N/A"),
                   status=m.get("outcome_status", "PENDING"), pnl=m.get("pnl_pct", "N/A"))
        G.add_edge(ep, f"REGIME:{m.get('regime', 'N/A')}", rel="in_regime")
        G.add_edge(ep, f"MACRO:{m.get('macro_regime', 'N/A')}", rel="in_macro")
        for tp in str(m.get("news_topics", "NONE")).split(","):          # Episode -had_topic-> TOPIC:x (from the news graph)
            if tp and tp != "NONE":
                G.add_edge(ep, f"TOPIC:{tp}", rel="had_topic")
    return G


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _summarise(G, S):
    by_sig = defaultdict(list)
    for ep in S:
        by_sig[G.nodes[ep]["signal"]].append(G.nodes[ep])
    lines = []
    for sig, items in sorted(by_sig.items()):
        res = [i for i in items if i["status"] == "RESOLVED"]
        pnl = [p for p in (_to_float(i["pnl"]) for i in res) if p is not None]
        tail = f", mean P&L {sum(pnl) / len(pnl):+.2f}%" if pnl else ""
        lines.append(f"- {sig}: {len(items)} episodes, {len(res)} resolved{tail}")
    return lines


def graph_rag_context(G, regime: str, macro: str, topics=None) -> str:
    def eps(node):
        return set(G.predecessors(node)) if G.has_node(node) else set()
    r, mc = eps(f"REGIME:{regime}"), eps(f"MACRO:{macro}")
    out = ""
    for label, S in ((f"regime={regime} AND macro={macro}", r & mc),
                     (f"regime={regime} only", r),
                     (f"macro={macro} only", mc)):
        if S:
            out = (f"GRAPH MEMORY (past episodes matching {label}; tiny sample, weak evidence):\n"
                   + "\n".join(_summarise(G, S)))
            break
    # news-topic hop: past episodes that had the same news topics as today (needs episodes saved with news_topics)
    St = set()
    for tp in topics or []:
        St |= eps(f"TOPIC:{tp}")
    if St:
        out += (("\n" if out else "") + f"GRAPH MEMORY (past episodes with the same news topics {', '.join(topics)}; weak evidence):\n"
                + "\n".join(_summarise(G, St)))
    return out


print(f"✅ Graph memory ready (USE_GRAPH_MEMORY={USE_GRAPH_MEMORY}).")

