# raem_analysts/dse/news_rag.py -- shared by abbank-dse, batbc-dse, bpml-dse, waltonhil-dse
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 📰 NEWS RAG v2 — scoped tagging, dedupe, as-of filter, recency weighting
# ================================================================
# What changed vs v1 (audit fixes):
#  1. Exchange-wide DSE notices are tagged company="DSE_MARKET" (v1 tagged them
#     with the stock's own ticker, so other firms' notices came back as
#     "@@TICKER@@ news" -> hallucinated catalysts). They are retrieved separately
#     and labelled "NOT news about <company>".
#  2. web_news (press search) is now ingested (only items that mention the company).
#  3. Item-level chunking on word boundaries (v1 cut mid-word) and the FULL
#     un-truncated news text is used (v1 ingested the 2000/3000-char prompt copy).
#  4. Placeholder / error lines ("No DSE news ...") are never stored or returned.
#  5. Content-hash IDs: the same notice is stored once, not once per run day.
#  6. As-of filter: chunks published after TRADE_DATE are never returned
#     (replay / backtest safety). In replay mode, live web search results are
#     not ingested at all (they cannot be made point-in-time).
#  7. Recency weighting: distance * (1 + 0.5 * min(age,90)/90).
#  8. Legacy v1 chunks have no "scope" field and are ignored (optional purge below).
import re, hashlib
from datetime import datetime

MARKET_TAG = "DSE_MARKET"
SECTOR_TAG = "DSE_SECTOR"       # sector / regulator press news (tobacco tax, NBR, budget)
NEWS_RAG_CAP = 1600            # max chars of news_rag_context injected into any one prompt
RAG_MAX_AGE_DAYS = 120         # older chunks are not retrieved
REPLAY_TOLERANCE_DAYS = 3      # TRADE_DATE older than this vs today => replay mode
RAG_COMPANY_ALIASES = {"@@TICKER@@": ["@@TICKER@@", "British American Tobacco"],
                       "ABBANK": ["ABBANK", "AB Bank"],
                       "BPML": ["BPML", "Bashundhara Paper Mills"],
                       "WALTONHIL": ["WALTONHIL", "Walton Hi-Tech Industries", "Walton"]}
PURGE_LEGACY_NEWS = False      # True => delete v1 chunks (no "scope" metadata) once
_JUNK_PREFIXES = ("no dse", "no web", "error", "n/a", "(no ")


def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


# Quote / directory / price-tracker pages that web search returns as "news" (seen in a real run: finbox, stockanalysis,
# MarketWatch, stocknow snippets came back as @@TICKER@@ "company news"). Applied to web-sourced chunks at ingest, at
# retrieval and in the news graph, so chunks stored by earlier runs (before this filter existed) are ignored too.
_DIR_TEXT_RE = re.compile(r"real-?time|price quotes?|stock quotes?|historical data|price (?:history|chart)|stock (?:overview|analysis)|"
                          r"stock news by|current price of|company (?:report|profile|overview)|\bscore \d+/100\b|are available (?:on|at)", re.I)


def _is_directory_text(text) -> bool:
    return bool(_DIR_TEXT_RE.search(str(text)))


def _is_junk_news(text) -> bool:
    t = _norm(text).lower().lstrip("- ").strip()
    return len(t) < 25 or t.startswith(_JUNK_PREFIXES)


def chunk_text(text: str, chunk_size: int = 600, overlap: int = 60) -> list:
    """Sliding window that breaks on sentence, else word, boundaries (never mid-word)."""
    text = _norm(text)
    if len(text) <= chunk_size:
        return [text] if text else []
    chunks, start = [], 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            cut = text.rfind(". ", start, end)
            if cut <= start + chunk_size * 0.5:
                cut = text.rfind(" ", start, end)
            else:
                cut += 1
            if cut > start:
                end = cut
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
        nxt = text.find(" ", start, end)          # snap start to a word boundary
        if nxt != -1 and nxt < end:
            start = nxt + 1
    return chunks


def _split_news_items(text: str) -> list:
    """get_news / get_global_news / web results are '- item' bullet lists."""
    items, cur = [], []
    for line in str(text).splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("- "):
            if cur:
                items.append(" ".join(cur))
            cur = [s[2:]]
        elif cur:
            cur.append(s)
        else:
            cur = [s]
    if cur:
        items.append(" ".join(cur))
    return [_norm(i) for i in items if _norm(i)]


def _parse_pub_date(item: str, default: str) -> str:
    m = re.match(r"\[([^\]]{4,40})\]", item)
    if m:
        try:
            import pandas as pd
            d = pd.to_datetime(m.group(1), errors="coerce")
            if not pd.isna(d):
                return d.strftime("%Y-%m-%d")
        except Exception:
            pass
    return default


def _is_replay(trade_date: str) -> bool:
    try:
        return (datetime.now() - datetime.strptime(trade_date, "%Y-%m-%d")).days > REPLAY_TOLERANCE_DAYS
    except Exception:
        return False


def ingest_news_to_corpus(company: str, trade_date: str, company_news: str,
                          global_news: str, web_news: str = "", sector_news: str = None) -> int:
    """Chunk + store news. Returns number of NEW chunks added (duplicates skipped)."""
    if sector_news is None:
        sector_news = globals().get("SECTOR_WEB_NEWS", "")
    aliases = [a.upper() for a in RAG_COMPANY_ALIASES.get(company, [company])]
    replay = _is_replay(trade_date)
    stats = {"junk": 0, "web_offtopic": 0, "web_skipped_replay": 0, "duplicates": 0}
    cand = {}

    for source, text, scope in [("company_news", company_news, "company"),
                                ("web_news", web_news, "web"),
                                ("sector_web", sector_news, "sector"),
                                ("global_news", global_news, "market")]:
        if not text:
            continue
        if scope in ("web", "sector") and replay:
            stats["web_skipped_replay"] += 1      # live web results are not point-in-time
            continue
        for item in _split_news_items(text):
            if _is_junk_news(item):
                stats["junk"] += 1
                continue
            if scope in ("web", "sector") and _is_directory_text(item):
                stats["directory"] = stats.get("directory", 0) + 1
                continue
            if scope in ("web", "sector") and re.match(r"\[no date\]", item, re.I):
                stats["undated_web"] = stats.get("undated_web", 0) + 1     # cannot be made point-in-time
                continue
            mentions = any(a in item.upper() for a in aliases)
            if scope == "web" and not mentions:
                stats["web_offtopic"] += 1
                continue
            tag_company, tag_scope = company, scope
            if scope == "sector":
                tag_company = SECTOR_TAG         # industry/regulator news, linked to the company by the news graph
            if scope == "market":
                if mentions:
                    tag_scope = "company"         # exchange notice that names this company
                else:
                    tag_company = MARKET_TAG      # about someone else / exchange-wide
            pub = _parse_pub_date(item, trade_date)
            for chunk in chunk_text(item):
                cid = hashlib.md5(f"{tag_company}|{tag_scope}|{chunk.lower()}".encode()).hexdigest()[:16]
                cand[cid] = (chunk, {"company": tag_company, "scope": tag_scope, "source": source,
                                     "trade_date": pub, "pub_date": pub, "ingested_on": trade_date})

    if not cand:
        print(f"📰 No ingestible news for {company} | {trade_date}  {stats}")
        return 0
    ids = list(cand)
    try:
        existing = set(news_corpus.get(ids=ids)["ids"])
    except Exception:
        existing = set()
    new_ids = [i for i in ids if i not in existing]
    stats["duplicates"] = len(ids) - len(new_ids)
    if new_ids:
        news_corpus.upsert(ids=new_ids,
                           documents=[cand[i][0] for i in new_ids],
                           metadatas=[cand[i][1] for i in new_ids])
    print(f"📰 Ingested {len(new_ids)} NEW chunks for {company} | {trade_date}  {stats}")
    print(f"   Total chunks in news_corpus: {news_corpus.count()}  (replay_mode={replay})")
    return len(new_ids)


def retrieve_relevant_news(query: str, company: str = None, n_results: int = 5,
                           as_of: str = None, scopes: list = None) -> list:
    """Semantic search + as-of filter + junk/dup filter + recency weighting."""
    as_of = as_of or globals().get("TRADE_DATE")
    conds = []
    if company:
        conds.append({"company": company})
    if scopes:
        conds.append({"scope": scopes[0]} if len(scopes) == 1 else {"scope": {"$in": list(scopes)}})
    where = None if not conds else (conds[0] if len(conds) == 1 else {"$and": conds})
    try:
        res = news_corpus.query(query_texts=[query], n_results=max(n_results * 6, 12), where=where)
    except Exception:
        try:
            res = news_corpus.query(query_texts=[query], n_results=n_results, where=where)
        except Exception as e:
            print(f"   ⚠️ news RAG query failed: {str(e)[:150]}")
            return []
    if not (res["ids"] and res["ids"][0]):
        return []
    try:
        ref = datetime.strptime(as_of, "%Y-%m-%d")
    except Exception:
        ref = None
    out, seen = [], set()
    for i in range(len(res["ids"][0])):
        meta, text = res["metadatas"][0][i], res["documents"][0][i]
        if _is_junk_news(text):
            continue
        if meta.get("scope") in ("web", "sector") and _is_directory_text(text):
            continue                                   # stale quote/directory chunk stored by an earlier run
        pub = meta.get("pub_date") or meta.get("trade_date", "")
        if as_of and pub and pub > as_of:
            continue                                   # LEAKAGE GUARD: nothing from after the as-of date
        age = 0
        if ref and pub:
            try:
                age = max((ref - datetime.strptime(pub, "%Y-%m-%d")).days, 0)
            except Exception:
                pass
        if age > RAG_MAX_AGE_DAYS:
            continue
        key = _norm(text).lower()[:120]
        if key in seen:
            continue
        seen.add(key)
        dist = res["distances"][0][i]
        out.append({"text": text, "metadata": meta, "distance": dist, "age_days": age,
                    "score": dist * (1 + 0.5 * min(age, 90) / 90)})
    out.sort(key=lambda c: c["score"])
    return out[:n_results]


def build_news_rag_context(company: str, as_of: str, query: str):
    """Company news (+ matching web items) first, then market-wide notices, clearly labelled."""
    comp = retrieve_relevant_news(query, company=company, n_results=4, as_of=as_of, scopes=["company", "web"])
    mkt = retrieve_relevant_news(query, company=MARKET_TAG, n_results=2, as_of=as_of, scopes=["market"])
    lines = []
    if comp:
        lines.append(f"COMPANY NEWS RETRIEVED VIA RAG ({company}; published on/before {as_of}; newer items weighted higher):")
        for c in comp:
            lines.append(f"- ({c['metadata'].get('source')}, pub {c['metadata'].get('pub_date')}, "
                         f"{c['age_days']}d old): {c['text'][:240]}")
    else:
        lines.append(f"(No {company}-specific news in the RAG corpus as of {as_of} -- do not invent any.)")
    if mkt:
        lines.append(f"DSE MARKET-WIDE NOTICES (mostly about OTHER companies/funds -- context only, "
                     f"NOT news about {company}; never attribute them to {company}):")
        for c in mkt:
            lines.append(f"- (pub {c['metadata'].get('pub_date')}): {c['text'][:200]}")
    return "\n".join(lines), comp, mkt


def purge_legacy_news_chunks() -> int:
    """Delete v1 chunks (no 'scope' metadata). v1 tagged other firms' notices as this stock."""
    got = news_corpus.get()
    ids = [i for i, m in zip(got["ids"], got["metadatas"]) if "scope" not in (m or {})]
    if ids:
        news_corpus.delete(ids=ids)
    return len(ids)


# ── RUN ──
try:
    _legacy = sum(1 for m in news_corpus.get()["metadatas"] if "scope" not in (m or {}))
    if _legacy:
        if PURGE_LEGACY_NEWS:
            print(f"🧹 Purged {purge_legacy_news_chunks()} legacy v1 news chunks.")
        else:
            print(f"ℹ️  {_legacy} legacy v1 news chunks are ignored by v2 retrieval (set PURGE_LEGACY_NEWS=True to delete).")
except Exception:
    pass

_full = news_result.get("news_raw_full", {}) or {}
_raw = news_result.get("news_raw", {}) or {}
def _pick(k):
    return _full.get(k) or _raw.get(k, "")

if not any(_pick(k) for k in ("company_news", "global_news", "web_news")):
    print("⚠️  news_raw not found -- nothing ingested (the LLM news *report* is deliberately NOT stored as news).")

ingest_news_to_corpus(
    company="@@TICKER@@",
    trade_date=TRADE_DATE,
    company_news=_pick("company_news"),
    global_news=_pick("global_news"),
    web_news=_pick("web_news"),
)

print(f"\n{'='*60}")
print("🔍 NEWS RAG RETRIEVAL — building context for downstream agents")
print(f"{'='*60}")
rag_query = f"{COMPANY} stock catalysts, risks, earnings, orders, and analyst sentiment as of {TRADE_DATE}"
news_rag_context, _rag_comp, _rag_mkt = build_news_rag_context("@@TICKER@@", TRADE_DATE, rag_query)
relevant_chunks = _rag_comp + _rag_mkt
for _c in relevant_chunks:
    _m = _c["metadata"]
    print(f"\n📄 {_m.get('scope')} | {_m.get('source')} | pub {_m.get('pub_date')} | dist {_c['distance']:.3f} | age {_c['age_days']}d")
    print(f"   {_c['text'][:250]}...")
if not relevant_chunks:
    print("   (no relevant chunk found)")

print(f"\n✅ news_rag_context ready ({len(_rag_comp)} company + {len(_rag_mkt)} market chunks, "
      f"{len(news_rag_context)} chars; prompts get the first {NEWS_RAG_CAP}).")
print(f"{'='*60}\n")
