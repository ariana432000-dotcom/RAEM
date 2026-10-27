# raem_analysts/international/llm_graph_extraction.py -- shared by AAPL, IBM, 0300_HK, 2222_SR
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 🧬 LLM GRAPH EXTRACTION — entity / relation edges for the news graph  (Qwen2.5:7b via Ollama, JSON mode)
# ================================================================
# The rule-based news graph (next cell) only knows what regexes know: fixed tickers, 7 topics, 3 regulators.
# This cell lets the local LLM read each news chunk ONCE and extract:
#     entities   (company / regulator / policy / product / event)        e.g. "SEC", "export controls", "TSMC"
#     relations  (subject -> relation -> object)                           e.g. FED -raises-> interest rates
# and the news graph gets extra edges:
#     NEWS -has_entity-> ENT:<NAME>            ENT:<A> -<relation>-> ENT:<B>           NEWS -mentions-> TICKER:<peer>
# Retrieval then adds two new hops (see llm_graph_candidates):
#     1) entity hop : news sharing a non-generic entity with the company's own news
#     2) relation hop: company news -> entity -> related entity -> news about that entity
#
# Safeguards against a 7B model's hallucinations (all in code, not in the prompt):
#   * an entity is accepted ONLY if its name literally appears in the chunk text (grounding check)
#   * relations must use a fixed verb list and BOTH ends must be accepted entities
#   * generic hubs (NASDAQ, USA, ...) are dropped so they cannot link unrelated news
#   * results are cached on disk by chunk id (ids are content hashes) -> each chunk costs one LLM call, ever
#   * format="json" calls bypass Fact Guard on purpose (there is no prose to fact-check)
# Ablation switch for the thesis: USE_LLM_GRAPH = False reproduces the rule-based news graph exactly.
import os, re, json, hashlib
from langchain_ollama import ChatOllama

USE_LLM_GRAPH = True
LLM_GRAPH_MAX_NEW_CALLS = 40            # max NEW LLM extractions per run (rest come from cache / are skipped)
LLM_GRAPH_CHUNK_CHARS = 900
LLM_GRAPH_CACHE_FILE = "llm_graph_cache.json"
LLM_GRAPH_VERSION = "v1"                # bump to invalidate the cache after changing the prompt / schema
LLM_REL_VERBS = {"raises", "cuts", "regulates", "announces", "affects", "approves", "suspends", "declares", "owned_by", "supplies",
                 "acquires", "sues", "partners_with", "sanctions"}
LLM_ENT_TYPES = {"COMPANY", "REGULATOR", "POLICY", "PRODUCT", "EVENT"}
LLM_ENT_STOP = {"NYSE", "NASDAQ", "NASDAQ 100", "S&P 500", "S&P", "DOW", "DOW JONES", "WALL STREET", "USA", "US", "U S", "UNITED STATES",
                "CHINA", "HONG KONG", "HKEX", "TADAWUL", "SAUDI ARABIA", "SAUDI", "RIYADH", "COMPANY", "THE COMPANY", "SHARE", "SHARES",
                "INVESTORS", "MARKET", "STOCK", "STOCKS", "USD", "SAR", "HKD", "RMB", "NEWS", "BOARD", "BOARD OF DIRECTORS"}
LLM_GRAPH_STATS = {"enabled": USE_LLM_GRAPH, "chunks": 0, "cached": 0, "new_calls": 0, "failed": 0,
                   "entities_ok": 0, "entities_dropped": 0, "relations_ok": 0, "relations_dropped": 0}

llm_graph = ChatOllama(model="qwen2.5:7b", base_url="http://localhost:11434", num_ctx=4096, temperature=0, format="json", repeat_penalty=1.15, num_predict=2048)

_LLM_EXTRACT_PROMPT = """You extract a knowledge graph from ONE short news item about a listed company or the market it trades in.
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


def _llm_graph_cache_load() -> dict:
    try:
        with open(LLM_GRAPH_CACHE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and d.get("_version") == LLM_GRAPH_VERSION else {"_version": LLM_GRAPH_VERSION}
    except Exception:
        return {"_version": LLM_GRAPH_VERSION}


def _llm_graph_cache_save(cache: dict):
    try:
        with open(LLM_GRAPH_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception as e:
        print(f"   ⚠️ LLM-graph cache not saved: {str(e)[:80]}")


def _llm_json(text: str):
    """Parse the model's JSON; tolerate stray text around the object."""
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


def _ent_key(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9&\- ]", " ", str(name))).strip().upper()


def validate_extraction(raw, text: str, stats=None) -> dict:
    """Keep only grounded entities / valid relations. Pure function (no LLM) -> unit-testable."""
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


def extract_graph_from_chunk(text: str):
    """One JSON-mode LLM call. Returns validated dict, or None on a call/parse failure (NOT cached)."""
    prompt = _LLM_EXTRACT_PROMPT.format(verbs=", ".join(sorted(LLM_REL_VERBS)), text=re.sub(r"\s+", " ", text)[:LLM_GRAPH_CHUNK_CHARS])
    try:
        out = llm_graph.invoke(prompt)
    except Exception as e:
        LLM_GRAPH_STATS["failed"] += 1
        print(f"   ⚠️ LLM graph extraction failed: {str(e)[:100]}")
        return None
    raw = _llm_json(str(getattr(out, "content", out)))
    if raw is None:
        LLM_GRAPH_STATS["failed"] += 1
        return None
    return validate_extraction(raw, text, LLM_GRAPH_STATS)


def enrich_graph_with_llm(G, company: str):
    """Add LLM-extracted entity / relation edges to the news graph G (in place)."""
    for k in ("chunks", "cached", "new_calls", "failed", "entities_ok", "entities_dropped", "relations_ok", "relations_dropped"):
        LLM_GRAPH_STATS[k] = 0
    LLM_GRAPH_STATS["enabled"] = True
    cache = _llm_graph_cache_load()
    universe = set(globals().get("GRAPH_PEERS", [])) - {company}
    news_nodes = sorted((n for n in G.nodes if n.startswith("NEWS:")), key=lambda n: G.nodes[n].get("age", 0))   # newest first
    new_calls, dirty = 0, False
    for n in news_nodes:
        text = G.nodes[n].get("text", "")
        cid = n.split(":", 1)[1]
        ck = cid + ":" + hashlib.md5(text.encode("utf-8", "ignore")).hexdigest()[:8]
        LLM_GRAPH_STATS["chunks"] += 1
        res = cache.get(ck)
        if res is not None:
            LLM_GRAPH_STATS["cached"] += 1
        elif new_calls < LLM_GRAPH_MAX_NEW_CALLS:
            res = extract_graph_from_chunk(text)
            new_calls += 1
            if res is not None:
                cache[ck], dirty = res, True
        if not res:
            continue
        for key, typ in res["entities"]:
            if key in universe:                                  # a peer ticker the regex may have missed (e.g. lower-case)
                G.add_edge(n, f"TICKER:{key}", rel="mentions_llm")
            elif key != company:
                G.add_edge(n, f"ENT:{key}", rel="has_entity", etype=typ)
        for s, v, o in res["relations"]:
            if s != company and o != company:
                G.add_edge(f"ENT:{s}", f"ENT:{o}", rel=v, src=n)
    LLM_GRAPH_STATS["new_calls"] = new_calls
    if dirty:
        _llm_graph_cache_save(cache)
    n_ent = sum(1 for x in G.nodes if x.startswith("ENT:"))
    print(f"🧬 LLM graph: {LLM_GRAPH_STATS['chunks']} chunks ({LLM_GRAPH_STATS['cached']} cached, {new_calls} new LLM calls, "
          f"{LLM_GRAPH_STATS['failed']} failed) -> {n_ent} entity nodes | new extractions kept {LLM_GRAPH_STATS['entities_ok']} entities / "
          f"{LLM_GRAPH_STATS['relations_ok']} relations, dropped {LLM_GRAPH_STATS['entities_dropped']} / {LLM_GRAPH_STATS['relations_dropped']} (ungrounded/invalid)")


def llm_graph_candidates(G, comp_news):
    """Extra retrieval hops from LLM edges. Returns [(news_node, weight, why)]."""
    def news_of(ent):
        return [p for p in G.predecessors(ent) if p.startswith("NEWS:")] if G.has_node(ent) else []
    comp_ents = {e for n in comp_news for e in G.successors(n) if e.startswith("ENT:")}
    out = []
    for e in sorted(comp_ents):                                  # hop 1: shared entity
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


print(f"✅ LLM graph extraction ready (USE_LLM_GRAPH={USE_LLM_GRAPH}; max {LLM_GRAPH_MAX_NEW_CALLS} new LLM calls per run, cached on disk).")

