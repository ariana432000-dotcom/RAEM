# raem_analysts/international/graph_news.py -- shared by AAPL, IBM, 0300_HK, 2222_SR
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 🕸️ GRAPH-LINKED NEWS + PEER-GRAPH RETRIEVAL  (rule-based: no LLM calls, no new storage)
# ================================================================
# Plain vector news RAG (cell above) only finds chunks that LOOK like the query. This cell adds a
# small knowledge graph over the SAME news_corpus so related news is found through links:
#
#   NEWS chunk -mentions-> TICKER:<symbol>     (company or any peer in GRAPH_PEERS)
#   NEWS chunk -about->    TOPIC:<EARNINGS | DIVIDEND | RATING | MONETARY | TARIFF_TRADE | REGULATION | TECH_AI | OIL | APPLIANCE | CHINA_MACRO | MARKET_RULE>
#   NEWS chunk -by->       REG:<SEC | FED | DOJ_FTC | OPEC | SAMA | PBOC | HKEX_SFC>
#
# Retrieval (starting from COMPANY):
#   1) news that mentions a SECTOR peer or a CORRELATED peer (peers come from graph_result)
#   2) sector / regulator news linked through COMPANY_SECTOR_TOPICS (e.g. Apple <-> tariff / export-control news)
#   3) news sharing a bridge topic with COMPANY's own recent news
# Items already returned as direct company news are excluded. Everything is labelled "NOT news about
# <company>" so a 7B model cannot present it as company news. Topics seen in today's company news are
# stored in `today_news_topics`, and saved into the episode metadata so the episode graph can link
# Episode -had_topic-> TOPIC:x in future runs.
# Ablation switch for the thesis: USE_NEWS_GRAPH = False reproduces the plain vector-RAG behaviour.
import re
import networkx as nx
from datetime import datetime

USE_NEWS_GRAPH = True
NEWS_GRAPH_MAX_ITEMS = 4

TOPIC_PATTERNS = {
    "EARNINGS":     r"\beps\b|earnings|quarterly results|guidance|revenue (?:rose|fell|grew|miss|beat)|profit (?:rose|fell|warning)|annual report|10-q|10-k",
    "DIVIDEND":     r"dividend|buyback|share repurchase|stock split|payout",
    "RATING":       r"price target|upgrade[sd]?|downgrade[sd]?|credit rating|initiates? coverage|outperform|underperform",
    "MONETARY":     r"\bfed\b|fomc|interest rates?|rate (?:cut|hike)|inflation|\bcpi\b|treasury yields?|central bank|\bsama\b|\bpboc\b|\bhkma\b|monetary policy",
    "TARIFF_TRADE": r"tariffs?|export controls?|sanctions?|trade (?:war|deal|tension)|customs|import dut(?:y|ies)",
    "REGULATION":   r"antitrust|\bdoj\b|\bftc\b|\bsec\b|lawsuit|class action|\bprobe\b|investigation|fine[sd]?\b|regulator",
    "TECH_AI":      r"\bai\b|artificial intelligence|generative|chips?\b|semiconductor|cloud|data cent(?:er|re)s?|iphone|quantum|software",
    "OIL":          r"opec|crude|brent|\bwti\b|oil prices?|production cuts?|refiner(?:y|ies)|barrels?|natural gas|lng",
    "APPLIANCE":    r"air[- ]?condition|home appliances?|refrigerator|washing machine|compressor|robotics|e-?commerce|smart home",
    "CHINA_MACRO":  r"china(?:'s)? (?:economy|growth|stimulus|property)|property (?:crisis|sector)|\byuan\b|\brmb\b|\bcsrc\b|hang seng",
    "MARKET_RULE":  r"trading halt|circuit breaker|short[- ]selling|delist|index (?:inclusion|rebalance)|\bmsci\b",
}
REG_PATTERNS = {
    "SEC":       r"\bsec\b|securities and exchange commission",
    "FED":       r"federal reserve|\bfomc\b|\bfed\b",
    "DOJ_FTC":   r"department of justice|\bdoj\b|federal trade commission|\bftc\b",
    "OPEC":      r"\bopec\b",
    "SAMA":      r"saudi central bank|\bsama\b|\bcma\b",
    "PBOC":      r"people'?s bank of china|\bpboc\b|\bcsrc\b",
    "HKEX_SFC":  r"\bhkex\b|securities and futures commission|\bsfc\b|\bhkma\b",
}
BRIDGE_TOPICS = {"MONETARY", "TARIFF_TRADE", "REGULATION", "TECH_AI", "OIL", "APPLIANCE", "CHINA_MACRO", "MARKET_RULE"}   # topics allowed to link news of different firms
COMPANY_SECTOR_TOPICS = {"AAPL": ["TECH_AI", "TARIFF_TRADE", "REGULATION"], "IBM": ["TECH_AI", "REGULATION", "MONETARY"],
                         "2222.SR": ["OIL", "MONETARY", "MARKET_RULE"], "0300.HK": ["APPLIANCE", "CHINA_MACRO", "TARIFF_TRADE"]}   # what moves THIS company besides its own notices
RAG_COMPANY_ALIASES = globals().get("RAG_COMPANY_ALIASES") or {}
for _k, _v in {"AAPL": ["AAPL", "Apple", "iPhone"], "IBM": ["IBM", "International Business Machines", "Red Hat"],
               "2222.SR": ["2222.SR", "Aramco", "Saudi Aramco"], "0300.HK": ["0300.HK", "Midea", "Midea Group"]}.items():
    RAG_COMPANY_ALIASES.setdefault(_k, _v)
RAG_MAX_AGE_DAYS = globals().get("RAG_MAX_AGE_DAYS", 120)          # older chunks are not linked
_JUNK_PREFIXES = globals().get("_JUNK_PREFIXES", ("no ", "error", "n/a", "(no "))
if "_norm" not in globals():
    def _norm(text) -> str:
        return re.sub(r"\s+", " ", str(text)).strip()
_DIR_TEXT_RE = globals().get("_DIR_TEXT_RE") or re.compile(r"real-?time|price quotes?|stock quotes?|historical data|price (?:history|chart)|"
                                                           r"stock (?:overview|analysis)|current price of|company (?:report|profile|overview)", re.I)
if "_is_directory_text" not in globals():
    def _is_directory_text(text) -> bool:
        return bool(_DIR_TEXT_RE.search(str(text)))
if "_is_junk_news" not in globals():
    def _is_junk_news(text) -> bool:
        t = _norm(text).lower().lstrip("- ").strip()
        return len(t) < 25 or t.startswith(tuple(_JUNK_PREFIXES))
_TOPIC_RE = {k: re.compile(v, re.I) for k, v in TOPIC_PATTERNS.items()}
_REG_RE = {k: re.compile(v, re.I) for k, v in REG_PATTERNS.items()}


def _ticker_hits(text: str, universe) -> set:
    # tickers are written in capitals in news ("MSFT", "2222.SR"), so match case-sensitively
    return {t for t in universe if re.search(rf"(?<![A-Za-z0-9]){re.escape(t)}(?![A-Za-z0-9])", text)}


def _age_days(pub: str, as_of: str):
    try:
        return max((datetime.strptime(as_of, "%Y-%m-%d") - datetime.strptime(pub, "%Y-%m-%d")).days, 0)
    except Exception:
        return None


def build_news_graph(company: str, as_of: str):
    """Rebuild the news graph from news_corpus (tens/hundreds of chunks -> instant)."""
    universe = set(globals().get("GRAPH_PEERS", [])) | {company}
    aliases = [a.upper() for a in globals().get("RAG_COMPANY_ALIASES", {}).get(company, [company])]
    got = news_corpus.get()
    G = nx.MultiDiGraph()
    for cid, doc, m in zip(got["ids"], got["documents"], got["metadatas"]):
        m = m or {}
        if _is_junk_news(doc) or _is_directory_text(doc):             # empty / error text, or a quote / directory page
            continue
        pub = m.get("pub_date") or m.get("trade_date", "")
        if pub and pub > as_of:                                  # leakage guard: nothing from after the as-of date
            continue
        age = _age_days(pub, as_of) if pub else 0
        if age is not None and age > RAG_MAX_AGE_DAYS:
            continue
        node = f"NEWS:{cid}"
        G.add_node(node, text=doc, pub=pub, age=age or 0, scope=m.get("scope"), source=m.get("source"))
        tick = _ticker_hits(doc, universe)
        # this notebook stores chunks as company_news / global_news, both tagged with the company, so only company_news counts
        if (m.get("company") == company and m.get("source") == "company_news") or any(a in doc.upper() for a in aliases):
            tick.add(company)
        for t in sorted(tick):
            G.add_edge(node, f"TICKER:{t}", rel="mentions")
        for k, rx in _TOPIC_RE.items():
            if rx.search(doc):
                G.add_edge(node, f"TOPIC:{k}", rel="about")
        for k, rx in _REG_RE.items():
            if rx.search(doc):
                G.add_edge(node, f"REG:{k}", rel="by")
    if globals().get("USE_LLM_GRAPH", False):                    # LLM entity/relation edges (cell above); off -> rule-based graph only
        try:
            enrich_graph_with_llm(G, company)
        except Exception as _e:
            print(f"⚠️ LLM graph enrichment skipped: {str(_e)[:120]}")
    return G


def _preds(G, node):
    return set(G.predecessors(node)) if G.has_node(node) else set()


def graph_news_retrieve(G, company: str, sector_peers, corr_peers, max_items: int = NEWS_GRAPH_MAX_ITEMS):
    """Returns (items, company_topics). items = [(score, why, node)]."""
    comp_news = _preds(G, f"TICKER:{company}")
    comp_topics = {t for n in comp_news for t in G.successors(n) if t.startswith("TOPIC:")}
    recent_topics = {t for n in comp_news if G.nodes[n]["age"] <= 7 for t in G.successors(n) if t.startswith("TOPIC:")}
    cand = {}

    def add(node, w, why):
        if node in comp_news:
            return                                              # already shown as direct company news
        if node not in cand or w > cand[node][0]:
            cand[node] = (w, why)

    for p in sector_peers or []:
        for n in _preds(G, f"TICKER:{p}"):
            add(n, 2.0, f"sector peer {p}")
    for p in corr_peers or []:
        for n in _preds(G, f"TICKER:{p}"):
            add(n, 1.5, f"correlated peer {p}")
    for t in COMPANY_SECTOR_TOPICS.get(company, []):
        for n in _preds(G, f"TOPIC:{t}"):
            add(n, 2.5, f"sector/regulatory link {t}")
    for t in comp_topics:
        if t.split(":", 1)[1] in BRIDGE_TOPICS:
            for n in _preds(G, t):
                add(n, 1.0, f"shares topic {t.split(':', 1)[1]} with {company} news")
    if globals().get("USE_LLM_GRAPH", False):                    # entity / relation hops from the LLM-extracted edges
        for n, w, why in llm_graph_candidates(G, comp_news):
            add(n, w, why)

    ranked = sorted(((w * (1 - 0.5 * min(G.nodes[n]["age"], 90) / 90), why, n) for n, (w, why) in cand.items()),
                    key=lambda x: -x[0])
    out, seen = [], set()
    for sc, why, n in ranked:
        key = _norm(G.nodes[n]["text"]).lower()[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append((sc, why, n))
        if len(out) >= max_items:
            break
    return out, sorted(t.split(":", 1)[1] for t in recent_topics)


def peer_episode_lines(peers, as_of: str, limit: int = 3) -> list:
    """Past episodes of peer stocks from episodic_memory (price-based observations stored by the Peer Episodes cell,
    or real decisions if any were ever saved). Only episodes whose OUTCOME was known before as_of; episodes in the
    same market regime as today come first. Silently empty if none exist."""
    peers = [p for p in dict.fromkeys(peers or [])]
    if not peers:
        return []
    try:
        got = episodic_memory.get(where={"company": {"$in": peers}})
    except Exception:
        return []
    try:
        today_reg = classify_regime(market_result.get("indicators_parsed", {}))
    except Exception:
        today_reg = None
    rows = []
    for m in got.get("metadatas", []):
        td = m.get("trade_date", "")
        if not td or td >= as_of or (m.get("outcome_date") or td) >= as_of:
            continue                                              # leakage guard (decision date AND outcome date)
        rows.append(m)
    rows.sort(key=lambda m: m.get("trade_date", ""), reverse=True)
    rows.sort(key=lambda m: 0 if (today_reg and m.get("regime") == today_reg) else 1)      # stable: same regime first, newest first
    out = []
    for m in rows[:limit]:
        c, d, pnl = m.get("company", "?"), m.get("trade_date", ""), m.get("pnl_pct", "N/A")
        if m.get("source") == "PEER_PRICE":
            out.append(f"- {c} {d}: regime {m.get('regime', 'N/A')}, then {pnl}% over the next {m.get('horizon_days', '?')} trading days"
                       f" (price-only observation, not a trade decision)")
        else:
            out.append(f"- {c} {d}: {m.get('final_signal', 'N/A')}" + (f", P&L {pnl}%" if pnl not in ("N/A", None, "") else ""))
    return out


def format_graph_news_block(G, items, peer_eps, company: str) -> str:
    lines = []
    if items:
        lines.append(f"GRAPH-LINKED NEWS (found via peer / sector / regulator links -- NOT news about {company}; "
                     f"context only, never attribute it to {company}):")
        for sc, why, n in items:
            a = G.nodes[n]
            lines.append(f"- ({why}; pub {a['pub']}, {a['age']}d old): {re.sub(r'^\[[^\]]*\]\s*', '', _norm(a['text']))[:200]}")
    if peer_eps:
        lines.append("PEER EPISODES (what peer stocks did after past market regimes -- price observations, not our decisions, weak evidence):")
        lines += peer_eps
    return "\n".join(lines)


_GRAPH_BLOCK_RE = re.compile(r"(?:GRAPH-LINKED NEWS|PEER EPISODES)[^\n]*\n(?:- [^\n]*\n?)*")


def apply_news_graph():
    """Insert the graph block into news_rag_context (before the generic market-wide notices)."""
    global news_rag_context, today_news_topics, news_graph_result
    today_news_topics = []
    news_graph_result = {"enabled": USE_NEWS_GRAPH, "items": 0}
    news_rag_context = _GRAPH_BLOCK_RE.sub("", str(news_rag_context)).rstrip()     # drop any block from an earlier run
    if not USE_NEWS_GRAPH:
        print("🕸️  News graph disabled (USE_NEWS_GRAPH=False) -- plain vector news RAG only.")
        return
    gm = (globals().get("graph_result") or {}).get("metrics") or {}
    sector_peers, corr_peers = gm.get("sector_peers") or [], gm.get("corr_peers") or []
    G = build_news_graph(COMPANY, TRADE_DATE)
    items, today_news_topics = graph_news_retrieve(G, COMPANY, sector_peers, corr_peers)
    peer_eps = peer_episode_lines(list(sector_peers) + list(corr_peers), TRADE_DATE)
    block = format_graph_news_block(G, items, peer_eps, COMPANY)
    ctx = news_rag_context + "\n"
    if block:
        ctx = ctx + block + "\n"
    news_rag_context = ctx.rstrip()
    news_graph_result = {"enabled": True, "nodes": G.number_of_nodes(), "edges": G.number_of_edges(),
                         "items": len(items), "peer_episodes": len(peer_eps), "today_topics": today_news_topics,
                         "peers_used": list(sector_peers) + list(corr_peers),
                         "llm_graph": dict(globals().get("LLM_GRAPH_STATS", {}))}
    print(f"🕸️  News graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges | peers used: "
          f"{news_graph_result['peers_used'] or 'none (graph_result has no peers)'}")
    print(f"   graph-linked news items: {len(items)} | peer episodes: {len(peer_eps)} | today's company-news topics: {today_news_topics or 'none'}")
    for sc, why, n in items:
        print(f"   • [{why}] {_norm(G.nodes[n]['text'])[:150]}")


apply_news_graph()
try:
    log_step("Graph News", {"company": COMPANY, "date": TRADE_DATE}, news_graph_result)
except Exception:
    pass

