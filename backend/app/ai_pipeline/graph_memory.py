"""Graph memory: graph-RAG over episodic memory (no model calls, no new storage).

Ported from the research notebook (BATBC v10, "GRAPH MEMORY").  The graph is rebuilt from the episode METADATA in Chroma on
every run (tens of nodes, instant):
    Episode -in_regime-> REGIME:<tag>     Episode -in_macro-> MACRO:<tag>     Episode -had_topic-> TOPIC:<tag>
Each Episode node carries its signal, outcome status and P&L.  Retrieval is a multi-hop query (same regime AND same macro
regime), relaxed to regime only, then macro only, when empty; a second hop finds past episodes that had the same news topics
as today (the news graph saves the topics in the episode metadata).  The summary is PREPENDED to the memory context because
debate_data.format_shared_context keeps only its first 700 characters.
Switch for the thesis ablation: RAEM_GRAPH_MEMORY=0 reproduces the old behaviour.
"""
from __future__ import annotations

from collections import defaultdict

from . import config


def enabled() -> bool:
    return config.flag("RAEM_GRAPH_MEMORY", True)


def build_episode_graph(episodic, company: str, as_of: str):
    import networkx as nx
    got = episodic.get(where={"company": company})
    G = nx.MultiDiGraph()
    for ep_id, m in zip(got["ids"], got["metadatas"]):
        if m.get("trade_date", "") >= as_of:          # the same leakage guard as the rest of the pipeline
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
    St = set()        # news-topic hop: past episodes that had the same news topics as today
    for tp in topics or []:
        St |= eps(f"TOPIC:{tp}")
    if St:
        out += (("\n" if out else "") + f"GRAPH MEMORY (past episodes with the same news topics {', '.join(topics)}; weak evidence):\n"
                + "\n".join(_summarise(G, St)))
    return out


def episode_graph_context(memory, company: str, as_of: str, regime: str, macro: str, topics=None, log=print) -> str:
    """The GRAPH MEMORY block for the memory context ('' when off, empty or networkx is missing)."""
    if not enabled():
        return ""
    try:
        G = build_episode_graph(memory.episodic, company, as_of)
        ctx = graph_rag_context(G, regime, macro or "N/A", topics=topics)
        log(f"graph memory: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges -> " + ("context added" if ctx else "no matching episodes"))
        return ctx
    except Exception as e:  # noqa: BLE001
        log(f"graph memory skipped: {str(e)[:120]}")
        return ""
