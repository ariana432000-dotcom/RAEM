"""News graph: graph-linked news and peer retrieval over the news corpus.

Ported from the research notebook (BATBC v10, "GRAPH-LINKED NEWS + PEER-GRAPH RETRIEVAL" and "LLM GRAPH EXTRACTION").
Plain vector news RAG only finds chunks that LOOK like the query.  This adds a small knowledge graph over the SAME news
corpus, so related news is found through links:

  NEWS chunk -mentions-> TICKER:<symbol>   (the company or any peer)
  NEWS chunk -about->    TOPIC:<TAX_DUTY | TOBACCO | DIVIDEND | EARNINGS | BOARD | RATING | MARKET_RULE>
  NEWS chunk -by->       REG:<NBR | BSEC | BB>
  NEWS chunk -has_entity-> ENT:<NAME>  and  ENT:<A> -<relation>-> ENT:<B>      (extracted by the LLM, see below)

Retrieval, starting from the company:
  1) news that mentions a sector peer or a correlated peer (the peers come from the Graph Analyst);
  2) sector / regulator news tied to the company by COMPANY_SECTOR_TOPICS (BATBC <-> tobacco tax / NBR);
  3) news that shares a bridge topic with the company's own recent news;
  4) the LLM's entity hop (news sharing a non-generic entity with the company's news) and relation hop.
Items already returned as direct company news are excluded.  Everything is labelled "NOT news about <company>" so a small
model cannot present it as company news.  The topics seen in today's company news go into the episode metadata, so the episode
graph can link Episode -had_topic-> TOPIC:x in later runs (graph_memory.py).  Peer episodes (peer_episodes.py) are added too.

LLM extraction: the model reads each news chunk ONCE and returns entities and relations.  Safeguards against a small model's
hallucinations, all in code: an entity is kept only if its name literally appears in the chunk; a relation must use a fixed
verb list and both ends must be kept entities; generic hubs (DSE, Bangladesh ...) are dropped; results are cached on disk by
chunk id and text hash, so a chunk costs one model call, ever; at most RAEM_LLM_GRAPH_MAX_CALLS (default 40) new calls per run.

Switches (0 turns off): RAEM_NEWS_GRAPH (the whole step), RAEM_LLM_GRAPH (only the model's entity / relation edges; off
reproduces the rule-based graph exactly).  Needs networkx.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

from . import config, news_rag

NEWS_GRAPH_MAX_ITEMS = 4

TOPIC_PATTERNS = {
    "TAX_DUTY":    r"\b(?:nbr|vat|supplementary duty|customs|excise|tax(?:es|ation)?|tariff|budget|finance act|sro)\b",
    "TOBACCO":     r"tobacco|cigarette|smoking|\bbidi\b",
    "DIVIDEND":    r"dividend|record date|\bagm\b|bonus share|right share|entitlement",
    "EARNINGS":    r"\beps\b|\bnav\b|\bprofit|earnings|financial statements?|price sensitive|half[- ]year|annual report|quarter",
    "BOARD":       r"board meeting|lr 19",
    "RATING":      r"credit rating",
    "MARKET_RULE": r"circuit breaker|floor price|trading halt|suspend|margin loan|\bbsec\b|lot size",
}
REG_PATTERNS = {
    "NBR":  r"\bnbr\b|national board of revenue",
    "BSEC": r"\bbsec\b|securities and exchange commission",
    "BB":   r"bangladesh bank",
}
BRIDGE_TOPICS = {"TAX_DUTY", "TOBACCO", "MARKET_RULE"}                    # topics allowed to link the news of different firms
COMPANY_SECTOR_TOPICS = {"BATBC": ["TOBACCO", "TAX_DUTY"]}                 # what moves THIS company besides its own notices
_TOPIC_RE = {k: re.compile(v, re.I) for k, v in TOPIC_PATTERNS.items()}
_REG_RE = {k: re.compile(v, re.I) for k, v in REG_PATTERNS.items()}

LLM_REL_VERBS = {"raises", "cuts", "regulates", "announces", "affects", "approves", "suspends", "declares", "owned_by", "supplies"}
LLM_ENT_TYPES = {"COMPANY", "REGULATOR", "POLICY", "PRODUCT", "EVENT"}
LLM_ENT_STOP = {"DSE", "DHAKA STOCK EXCHANGE", "CSE", "BANGLADESH", "DHAKA", "COMPANY", "THE COMPANY", "SHARE", "SHARES",
                "INVESTORS", "MARKET", "STOCK", "STOCKS", "BDT", "TAKA", "NEWS", "BOARD", "BOARD OF DIRECTORS", "PRICE SENSITIVE INFORMATION"}
LLM_GRAPH_CHUNK_CHARS = 900
LLM_GRAPH_VERSION = "v1"                # bump to invalidate the cache after changing the prompt / schema
LLM_GRAPH_STATS = {"enabled": False, "chunks": 0, "cached": 0, "new_calls": 0, "failed": 0,
                   "entities_ok": 0, "entities_dropped": 0, "relations_ok": 0, "relations_dropped": 0}

_LLM_EXTRACT_PROMPT = """You extract a knowledge graph from ONE short news item about a listed company or its stock market.
Return ONLY a JSON object with exactly these keys:
  "entities":  list of {{"name": str, "type": one of COMPANY, REGULATOR, POLICY, PRODUCT, EVENT}}
  "relations": list of {{"subj": str, "rel": one of {verbs}, "obj": str}}
Rules:
- Use ONLY things explicitly written in the text. Copy names exactly as written. Do not guess or add outside knowledge.
- At most 6 entities and 5 relations. Empty lists are fine.
- Do not include numbers, prices or dates as entities.

NEWS ITEM:
{text}

JSON:"""


def enabled() -> bool:
    return config.flag("RAEM_NEWS_GRAPH", True)


def llm_graph_enabled() -> bool:
    return config.flag("RAEM_LLM_GRAPH", True)


def _max_new_calls() -> int:
    try:
        return int(os.environ.get("RAEM_LLM_GRAPH_MAX_CALLS", "40"))
    except ValueError:
        return 40


# ── LLM entity / relation extraction ─────────────────────────────
def _cache_file() -> Path:
    return Path(os.environ.get("RAEM_LLM_GRAPH_CACHE_FILE") or config.DATA_DIR / "llm_graph_cache.json")


def _cache_load() -> dict:
    try:
        with open(_cache_file(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and d.get("_version") == LLM_GRAPH_VERSION else {"_version": LLM_GRAPH_VERSION}
    except Exception:  # noqa: BLE001
        return {"_version": LLM_GRAPH_VERSION}


def _cache_save(cache: dict) -> None:
    try:
        path = _cache_file()
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001
        print(f"   LLM-graph cache not saved: {str(e)[:80]}")


def _llm_json(text: str):
    """Parse the model's JSON; tolerate stray text (a code fence, a sentence) around the object."""
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:  # noqa: BLE001
                return None
    return None


def _ent_key(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9&\- ]", " ", str(name))).strip().upper()


def validate_extraction(raw, text: str, stats=None) -> dict:
    """Keep only grounded entities and valid relations. A pure function (no model call), so it is unit-testable."""
    stats = stats if stats is not None else {}
    low = re.sub(r"\s+", " ", text).lower()
    ents, rels = {}, []
    if not isinstance(raw, dict):
        return {"entities": [], "relations": []}
    for e in (raw.get("entities") or [])[:8]:
        if not isinstance(e, dict):
            continue
        name, typ = str(e.get("name", "")).strip(), str(e.get("type", "")).strip().upper()
        key = _ent_key(name)
        ok = (len(key) >= 3 and key not in LLM_ENT_STOP and typ in LLM_ENT_TYPES
              and not re.fullmatch(r"[\d\s.,%\-]+", key) and re.sub(r"\s+", " ", name).lower() in low)
        if ok:
            ents[key] = typ
            stats["entities_ok"] = stats.get("entities_ok", 0) + 1
        else:
            stats["entities_dropped"] = stats.get("entities_dropped", 0) + 1
    for r in (raw.get("relations") or [])[:6]:
        if not isinstance(r, dict):
            continue
        s, o, v = _ent_key(r.get("subj", "")), _ent_key(r.get("obj", "")), str(r.get("rel", "")).strip().lower()
        if s in ents and o in ents and s != o and v in LLM_REL_VERBS:
            rels.append([s, v, o])
            stats["relations_ok"] = stats.get("relations_ok", 0) + 1
        else:
            stats["relations_dropped"] = stats.get("relations_dropped", 0) + 1
    return {"entities": [[k, t] for k, t in ents.items()], "relations": rels}


def extract_graph_from_chunk(llm, text: str):
    """One model call. Returns the validated dict, or None on a call / parse failure (which is NOT cached)."""
    from .llm import invoke_llm_with_retry
    prompt = _LLM_EXTRACT_PROMPT.format(verbs=", ".join(sorted(LLM_REL_VERBS)), text=re.sub(r"\s+", " ", text)[:LLM_GRAPH_CHUNK_CHARS])
    try:
        out = invoke_llm_with_retry(llm, prompt, max_attempts=1, guard=False)   # JSON, not prose for another agent: the Fact Guard has nothing to check
    except Exception as e:  # noqa: BLE001
        LLM_GRAPH_STATS["failed"] += 1
        print(f"   LLM graph extraction failed: {str(e)[:100]}")
        return None
    raw = _llm_json(str(getattr(out, "content", out)))
    if raw is None:
        LLM_GRAPH_STATS["failed"] += 1
        return None
    return validate_extraction(raw, text, LLM_GRAPH_STATS)


def enrich_graph_with_llm(G, company: str, universe, llm, log=print):
    """Add the model's entity / relation edges to the news graph G (in place)."""
    for k in ("chunks", "cached", "new_calls", "failed", "entities_ok", "entities_dropped", "relations_ok", "relations_dropped"):
        LLM_GRAPH_STATS[k] = 0
    LLM_GRAPH_STATS["enabled"] = True
    cache = _cache_load()
    universe = set(universe or []) - {company}
    news_nodes = sorted((n for n in G.nodes if n.startswith("NEWS:")), key=lambda n: G.nodes[n].get("age", 0))   # newest first
    new_calls, dirty, streak, limit = 0, False, 0, _max_new_calls()
    for n in news_nodes:
        text = G.nodes[n].get("text", "")
        cid = n.split(":", 1)[1]
        ck = cid + ":" + hashlib.md5(text.encode("utf-8", "ignore")).hexdigest()[:8]
        LLM_GRAPH_STATS["chunks"] += 1
        res = cache.get(ck)
        if res is not None:
            LLM_GRAPH_STATS["cached"] += 1
        elif new_calls < limit and streak < 3:        # three failures in a row (billing, a refused key): stop asking
            res = extract_graph_from_chunk(llm, text)
            new_calls += 1
            streak = streak + 1 if res is None else 0
            if res is not None:
                cache[ck], dirty = res, True
        if not res:
            continue
        for key, typ in res["entities"]:
            if key in universe:                          # a peer ticker the regex may have missed (e.g. lower-case)
                G.add_edge(n, f"TICKER:{key}", rel="mentions_llm")
            elif key != company:
                G.add_edge(n, f"ENT:{key}", rel="has_entity", etype=typ)
        for s, v, o in res["relations"]:
            if s != company and o != company:
                G.add_edge(f"ENT:{s}", f"ENT:{o}", rel=v, src=n)
    LLM_GRAPH_STATS["new_calls"] = new_calls
    if dirty:
        _cache_save(cache)
    n_ent = sum(1 for x in G.nodes if x.startswith("ENT:"))
    log(f"LLM graph: {LLM_GRAPH_STATS['chunks']} chunks ({LLM_GRAPH_STATS['cached']} cached, {new_calls} new model calls, "
        f"{LLM_GRAPH_STATS['failed']} failed) -> {n_ent} entity nodes | kept {LLM_GRAPH_STATS['entities_ok']} entities / "
        f"{LLM_GRAPH_STATS['relations_ok']} relations, dropped {LLM_GRAPH_STATS['entities_dropped']} / {LLM_GRAPH_STATS['relations_dropped']} (ungrounded or invalid)")


def llm_graph_candidates(G, comp_news):
    """Extra retrieval hops from the model's edges. [(news_node, weight, why)]."""
    def news_of(ent):
        return [p for p in G.predecessors(ent) if p.startswith("NEWS:")] if G.has_node(ent) else []
    comp_ents = {e for n in comp_news for e in G.successors(n) if e.startswith("ENT:")}
    out = []
    for e in sorted(comp_ents):                                  # hop 1: a shared entity
        for n in news_of(e):
            out.append((n, 1.8, f"shares entity {e[4:].title()} with company news [LLM-extracted]"))
    for e in sorted(comp_ents):                                  # hop 2: entity -relation-> entity -> news
        for _, e2, d in G.out_edges(e, data=True):
            if e2.startswith("ENT:") and d.get("rel") != "has_entity":
                for n in news_of(e2):
                    out.append((n, 2.0, f"{e[4:].title()} {d.get('rel')} {e2[4:].title()} [LLM-extracted relation]"))
        for e1, _, d in G.in_edges(e, data=True):
            if e1.startswith("ENT:") and d.get("rel") != "has_entity":
                for n in news_of(e1):
                    out.append((n, 2.0, f"{e1[4:].title()} {d.get('rel')} {e[4:].title()} [LLM-extracted relation]"))
    return out


# ── the rule-based news graph ────────────────────────────────────
def _ticker_hits(text: str, universe) -> set:
    # DSE notices write tickers in capitals ("SHARPIND: Board Meeting ..."), so match case-sensitively
    return {t for t in universe if re.search(rf"(?<![A-Za-z0-9]){re.escape(t)}(?![A-Za-z0-9])", text)}


def _age_days(pub: str, as_of: str):
    try:
        return max((datetime.strptime(as_of, "%Y-%m-%d") - datetime.strptime(pub, "%Y-%m-%d")).days, 0)
    except Exception:  # noqa: BLE001
        return None


def build_news_graph(collection, company: str, as_of: str, universe=None, llm=None, log=print):
    """Rebuild the news graph from the news corpus (tens or hundreds of chunks, so it is instant)."""
    import networkx as nx
    universe = set(universe or []) | {company}
    aliases = [a.upper() for a in news_rag.aliases_for(company)]
    got = collection.get()
    G = nx.MultiDiGraph()
    for cid, doc, m in zip(got["ids"], got["documents"], got["metadatas"]):
        m = m or {}
        if "scope" not in m or news_rag.is_junk_news(doc):
            continue
        if m.get("scope") in ("web", "sector") and news_rag.is_directory_text(doc):
            continue                                              # a quote / directory page, not news
        pub = m.get("pub_date") or m.get("trade_date", "")
        if pub and pub > as_of:                                  # leakage guard: nothing from after the as-of date
            continue
        age = _age_days(pub, as_of) if pub else 0
        if age is not None and age > news_rag.RAG_MAX_AGE_DAYS:
            continue
        node = f"NEWS:{cid}"
        G.add_node(node, text=doc, pub=pub, age=age or 0, scope=m.get("scope"), source=m.get("source"))
        tick = _ticker_hits(doc, universe)
        if m.get("company") == company or any(a in doc.upper() for a in aliases):
            tick.add(company)
        for t in sorted(tick):
            G.add_edge(node, f"TICKER:{t}", rel="mentions")
        for k, rx in _TOPIC_RE.items():
            if rx.search(doc):
                G.add_edge(node, f"TOPIC:{k}", rel="about")
        for k, rx in _REG_RE.items():
            if rx.search(doc):
                G.add_edge(node, f"REG:{k}", rel="by")
    LLM_GRAPH_STATS["enabled"] = False
    if llm is not None and llm_graph_enabled():                  # the model's entity / relation edges; off -> the rule-based graph only
        try:
            enrich_graph_with_llm(G, company, universe, llm, log=log)
        except Exception as e:  # noqa: BLE001
            log(f"LLM graph enrichment skipped: {str(e)[:120]}")
    return G


def _preds(G, node):
    return set(G.predecessors(node)) if G.has_node(node) else set()


def graph_news_retrieve(G, company: str, sector_peers, corr_peers, max_items: int = NEWS_GRAPH_MAX_ITEMS, use_llm: bool = False):
    """(items, company_topics). items = [(score, why, node)]."""
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
    if use_llm:                                                  # entity / relation hops from the model's edges
        for n, w, why in llm_graph_candidates(G, comp_news):
            add(n, w, why)

    ranked = sorted(((w * (1 - 0.5 * min(G.nodes[n]["age"], 90) / 90), why, n) for n, (w, why) in cand.items()),
                    key=lambda x: -x[0])
    out, seen = [], set()
    for sc, why, n in ranked:
        key = news_rag._norm(G.nodes[n]["text"]).lower()[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append((sc, why, n))
        if len(out) >= max_items:
            break
    return out, sorted(t.split(":", 1)[1] for t in recent_topics)


def peer_episode_lines(episodic, peers, as_of: str, today_regime=None, limit: int = 3) -> list:
    """Past episodes of the peer stocks from episodic memory (the price observations stored by peer_episodes.py, or real
    decisions if any were ever saved). Only episodes whose OUTCOME was known before as_of; episodes in the same market regime
    as today come first. Silently empty if none exist."""
    peers = [p for p in dict.fromkeys(peers or [])]
    if not peers:
        return []
    try:
        got = episodic.get(where={"company": {"$in": peers}})
    except Exception:  # noqa: BLE001
        return []
    rows = []
    for m in got.get("metadatas", []):
        td = m.get("trade_date", "")
        if not td or td >= as_of or (m.get("outcome_date") or td) >= as_of:
            continue                                              # leakage guard (decision date AND outcome date)
        rows.append(m)
    rows.sort(key=lambda m: m.get("trade_date", ""), reverse=True)
    rows.sort(key=lambda m: 0 if (today_regime and m.get("regime") == today_regime) else 1)      # stable: same regime first, newest first
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
            body = _LEAD_TAG_RE.sub("", news_rag._norm(a["text"]))[:200]
            lines.append(f"- ({why}; pub {a['pub']}, {a['age']}d old): {body}")
    if peer_eps:
        lines.append("PEER EPISODES (what peer stocks did after past market regimes -- price observations, not our decisions, weak evidence):")
        lines += peer_eps
    return "\n".join(lines)


_LEAD_TAG_RE = re.compile(r"^\[[^\]]*\]\s*")
_GRAPH_BLOCK_RE = re.compile(r"(?:GRAPH-LINKED NEWS|PEER EPISODES)[^\n]*\n(?:- [^\n]*\n?)*")


def run_news_graph(memory, company: str, as_of: str, news_rag_context: str, sector_peers=None, corr_peers=None,
                   universe=None, today_regime=None, llm=None, log=print) -> dict:
    """Insert the graph block into the news RAG context (before the exchange-wide notices).

    Returns {"context": new news_rag_context, "cap": the character cap to use for it, "topics": today's company-news topics,
    "result": the stats that are logged}."""
    ctx_in = _GRAPH_BLOCK_RE.sub("", str(news_rag_context or "")).rstrip()      # drop any block from an earlier run
    out = {"context": ctx_in, "cap": news_rag.NEWS_RAG_CAP, "topics": [], "result": {"enabled": enabled(), "items": 0}}
    if not enabled():
        log("news graph is off (RAEM_NEWS_GRAPH=0): plain vector news RAG only")
        return out
    sector_peers, corr_peers = list(sector_peers or []), list(corr_peers or [])
    try:
        G = build_news_graph(memory.news, company, as_of, universe=universe, llm=llm, log=log)
    except ImportError:
        log("news graph skipped: networkx is not installed")
        out["result"] = {"enabled": False, "items": 0, "reason": "networkx is not installed"}
        return out
    use_llm = llm is not None and llm_graph_enabled()
    items, topics = graph_news_retrieve(G, company, sector_peers, corr_peers, use_llm=use_llm)
    peer_eps = peer_episode_lines(memory.episodic, sector_peers + corr_peers, as_of, today_regime)
    block = format_graph_news_block(G, items, peer_eps, company)
    ctx = ctx_in + "\n"
    if block:
        i = ctx.find("EXCHANGE-WIDE NOTICES")
        ctx = (ctx[:i] + block + "\n" + ctx[i:]) if i >= 0 else (ctx + block + "\n")
        out["cap"] = max(news_rag.NEWS_RAG_CAP, 2200)
    out["context"], out["topics"] = ctx.rstrip(), topics
    out["result"] = {"enabled": True, "nodes": G.number_of_nodes(), "edges": G.number_of_edges(), "items": len(items),
                     "peer_episodes": len(peer_eps), "today_topics": topics, "peers_used": sector_peers + corr_peers,
                     "llm_graph": dict(LLM_GRAPH_STATS)}
    log(f"news graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges | peers used: {out['result']['peers_used'] or 'none'}")
    log(f"graph-linked news items: {len(items)} | peer episodes: {len(peer_eps)} | today's company-news topics: {topics or 'none'}")
    for sc, why, n in items:
        log(f"  [{why}] {news_rag._norm(G.nodes[n]['text'])[:150]}")
    return out
