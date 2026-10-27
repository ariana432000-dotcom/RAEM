"""News sources, the news corpus (RAG) and the rule-based news sentiment of the research notebooks.

Ported from the notebook cells "News Analyst" (web and sector news), "News RAG v2" and "Sentiment Analyst":

* company news, exchange notices, press search (ddgs) and sector news are stored once each, tagged by scope
  (company / web / sector / market), dated, and never returned for a date after the trade date;
* the sentiment score is computed in code from the stored company news with a keyword lexicon; the LLM only explains it.

Every function takes the Chroma collection as an argument (`RAEMMemory().news`), so the module has no globals to patch.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

from . import config

MARKET_TAG = "DSE_MARKET"         # exchange-wide notices (about other firms), never "news about <company>"
SECTOR_TAG = "DSE_SECTOR"         # sector / regulator press news
NEWS_RAG_CAP = 1600               # max characters of news_rag_context put into any one prompt
RAG_MAX_AGE_DAYS = 120            # older chunks are not retrieved
REPLAY_TOLERANCE_DAYS = 3         # a trade date older than this vs today is a replay: live web results are not ingested
_JUNK_PREFIXES = ("no dse", "no web", "error", "n/a", "(no ")

# Rule-based sentiment settings (the notebook's switches)
USE_RULE_SENTIMENT = os.environ.get("RAEM_RULE_SENTIMENT", "1") != "0"   # 0 = the old LLM-only behaviour (thesis ablation)
SENT_MAX_AGE_DAYS = 30
SENT_HALF_LIFE_DAYS = 10.0
SENT_BULL_AT, SENT_BEAR_AT = 6.0, 4.0          # score >= 6 BULLISH, <= 4 BEARISH, else NEUTRAL

# Names the research companies are known by in the press; any other company uses its registered name.
RESEARCH_NAMES = {
    "BATBC": "British American Tobacco Bangladesh Company Limited",
    "ABBANK": "AB Bank Limited",
    "BPML": "Bashundhara Paper Mills Limited",
    "WALTONHIL": "Walton Hi-Tech Industries PLC",
    "AAPL": "Apple Inc.", "IBM": "IBM", "2222.SR": "Saudi Aramco", "0300.HK": "Midea Group",
}
# Regulator / sector searches (they reach the corpus as scope="sector", not the news analyst's prompt)
SECTOR_NEWS_QUERIES = {
    "BATBC": ["Bangladesh cigarette tobacco tax NBR", "Bangladesh budget supplementary duty cigarette price"],
}


def company_name(ticker: str) -> str:
    """The company's name for press searches: RAEM_COMPANY_NAME, a name saved by the site, the research list, else the ticker."""
    env = os.environ.get("RAEM_COMPANY_NAME", "").strip()
    if env:
        return env
    try:
        path = os.environ.get("RAEM_COMPANIES") or str(config.ENGINE.parent / "data" / "companies.json")
        with open(path, encoding="utf-8") as fh:
            ent = (json.load(fh) or {}).get(ticker) or {}
        if ent.get("name"):
            return str(ent["name"])
    except Exception:  # noqa: BLE001
        pass
    return RESEARCH_NAMES.get(ticker, ticker)


def aliases_for(ticker: str) -> list[str]:
    """Upper-case strings that show an item is about this company."""
    name = company_name(ticker)
    out = [ticker.upper(), ticker.split(".")[0].upper()]
    if name and name.upper() != ticker.upper():
        out.append(name.upper())
        words = re.sub(r"\b(LIMITED|LTD|PLC|INC|CORP|CORPORATION|COMPANY|CO|GROUP|HOLDINGS?)\b\.?", "", name, flags=re.I).split()
        if len(words) >= 2:
            out.append(" ".join(words[:3]).upper())
    return [a for a in dict.fromkeys(out) if len(a) >= 2]


# ───────────────────────────── text helpers ─────────────────────────────

def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


# Quote / directory / price-tracker pages that web search returns as "news"
_DIR_TEXT_RE = re.compile(r"real-?time|price quotes?|stock quotes?|historical data|price (?:history|chart)|stock (?:overview|analysis)|"
                          r"stock news by|current price of|company (?:report|profile|overview)|\bscore \d+/100\b|are available (?:on|at)", re.I)


def is_directory_text(text) -> bool:
    return bool(_DIR_TEXT_RE.search(str(text)))


def is_junk_news(text) -> bool:
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


def split_news_items(text: str) -> list:
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
        except Exception:  # noqa: BLE001
            pass
    return default


def is_replay(trade_date: str) -> bool:
    try:
        return (datetime.now() - datetime.strptime(trade_date, "%Y-%m-%d")).days > REPLAY_TOLERANCE_DAYS
    except Exception:  # noqa: BLE001
        return False


# ───────────────────────────── web and sector news (ddgs) ─────────────────────────────

_DIR_DOMAINS = re.compile(r"amarstock|stocknow|stockanalysis|dseanalyst|investing\.com|tradingview|marketscreener|morningstar|"
                          r"companiesmarketcap|dsebd\.org|dse\.com\.bd|wikipedia|linkedin|facebook|youtube", re.I)
_DIR_TITLE = re.compile(r"stock (?:price|quote|analysis)|share price|live (?:dsex|price|data)|company (?:report|profile|overview)|"
                        r"historical data|price (?:chart|history)|\bscore \d+/100\b|are available (?:on|at)", re.I)
_DATE_IN_TEXT = re.compile(r"\b(\d+\s+(?:minutes?|hours?|days?|weeks?|months?)\s+ago|\d{1,2}(?:st|nd|rd|th)?\s+[A-Z][a-z]{2,8}\.?\s+\d{4}|"
                           r"[A-Z][a-z]{2,8}\.?\s+\d{1,2},\s+\d{4})\b")


def _ddgs():
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS
    return DDGS


def fetch_web_news(ticker: str, log=print, max_results: int = 8) -> str:
    """Dated press coverage of the company from general web search (a supplement to the exchange's own thin feed).

    Directory, quote and price-tracker pages are dropped; each kept item starts with the date written in its snippet
    (ISO date from the news endpoint, else "2 days ago" / "9th Sep 2026"), or "[no date]"."""
    hint = company_name(ticker)
    try:
        DDGS = _ddgs()
    except ImportError as e:
        log(f"web search library not available: {e}")
        return "Error: web search library not available."
    query = f"{hint} {ticker} news"
    try:
        with DDGS() as ddgs:
            results, seen = [], set()
            for q in (f'"{hint}"', f"{ticker} {hint.split()[0]} news"):
                try:
                    for r in ddgs.news(q, timelimit="m", max_results=max_results):
                        href = r.get("url") or r.get("href", "")
                        if href and href not in seen:
                            seen.add(href)
                            results.append({"title": r.get("title", ""), "body": r.get("body", ""), "href": href,
                                            "date": r.get("date", "")})
                except Exception as e:  # noqa: BLE001
                    log(f"ddgs.news failed for '{q}': {str(e)[:120]}")
            if not results:
                log("ddgs.news returned nothing -> falling back to ddgs.text")
                results = list(ddgs.text(query, max_results=max_results))
    except Exception as e:  # noqa: BLE001
        log(f"web search failed: {str(e)[:200]}")
        return f"Error: web search failed ({str(e)[:150]})"
    if not results:
        return "No web search results found."
    lines, dropped = [], 0
    for r in results:
        title, body, href = r.get("title", ""), (r.get("body", "") or "")[:200], r.get("href", "")
        if _DIR_DOMAINS.search(href) or _DIR_TITLE.search(title + " " + body):
            dropped += 1
            continue
        dom = re.sub(r"^https?://(?:www\.)?", "", href).split("/")[0]
        dm = _DATE_IN_TEXT.search(title + " " + (r.get("body", "") or ""))
        nd = str(r.get("date", "") or "")[:10]
        date = nd if re.match(r"\d{4}-\d{2}-\d{2}$", nd) else (dm.group(1) if dm else "no date")
        lines.append(f"- [{date}] {title}: {body} ({dom})")
    if dropped:
        log(f"{dropped} web item(s) dropped (directory / price pages)")
    if not lines:
        return "No dated company-news coverage found (only directory/price pages, which are excluded)."
    return "\n".join(lines)


def fetch_sector_news(ticker: str, log=print, max_results: int = 6) -> str:
    """Sector / regulator news (not passed to the news analyst's prompt): stored as scope="sector"."""
    qs = SECTOR_NEWS_QUERIES.get(ticker, [])
    if not qs:
        return ""
    try:
        DDGS = _ddgs()
    except ImportError:
        return ""
    out, seen = [], set()
    try:
        with DDGS() as ddgs:
            for q in qs:
                try:
                    for r in ddgs.news(q, timelimit="m", max_results=max_results):
                        href = r.get("url") or r.get("href", "")
                        if not href or href in seen or _DIR_DOMAINS.search(href):
                            continue
                        seen.add(href)
                        d = str(r.get("date", "") or "")[:10]
                        if not re.match(r"\d{4}-\d{2}-\d{2}$", d):
                            continue                               # undated sector items cannot be point-in-time filtered
                        dom = re.sub(r"^https?://(?:www\.)?", "", href).split("/")[0]
                        out.append(f"- [{d}] {r.get('title', '')}: {(r.get('body', '') or '')[:200]} ({dom})")
                except Exception as e:  # noqa: BLE001
                    log(f"sector news query failed '{q}': {str(e)[:120]}")
    except Exception as e:  # noqa: BLE001
        log(f"sector news search failed: {str(e)[:120]}")
    return "\n".join(out)


# ───────────────────────────── the corpus ─────────────────────────────

def ingest_news(collection, company: str, trade_date: str, company_news: str, global_news: str,
                web_news: str = "", sector_news: str = "", log=print) -> int:
    """Chunk and store news. Returns the number of NEW chunks (duplicates are skipped)."""
    aliases = aliases_for(company)
    replay = is_replay(trade_date)
    stats = {"junk": 0, "web_offtopic": 0, "web_skipped_replay": 0, "duplicates": 0}
    cand = {}
    for source, text, scope in [("company_news", company_news, "company"), ("web_news", web_news, "web"),
                                ("sector_web", sector_news, "sector"), ("global_news", global_news, "market")]:
        if not text:
            continue
        if scope in ("web", "sector") and replay:
            stats["web_skipped_replay"] += 1          # live web results are not point-in-time
            continue
        for item in split_news_items(text):
            if is_junk_news(item):
                stats["junk"] += 1
                continue
            if scope in ("web", "sector") and is_directory_text(item):
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
                tag_company = SECTOR_TAG
            if scope == "market":
                if mentions:
                    tag_scope = "company"             # an exchange notice that names this company
                else:
                    tag_company = MARKET_TAG          # about someone else / exchange-wide
            pub = _parse_pub_date(item, trade_date)
            for chunk in chunk_text(item):
                cid = hashlib.md5(f"{tag_company}|{tag_scope}|{chunk.lower()}".encode()).hexdigest()[:16]
                cand[cid] = (chunk, {"company": tag_company, "scope": tag_scope, "source": source,
                                     "trade_date": pub, "pub_date": pub, "ingested_on": trade_date})
    if not cand:
        log(f"no ingestible news for {company} | {trade_date}  {stats}")
        return 0
    ids = list(cand)
    try:
        existing = set(collection.get(ids=ids)["ids"])
    except Exception:  # noqa: BLE001
        existing = set()
    new_ids = [i for i in ids if i not in existing]
    stats["duplicates"] = len(ids) - len(new_ids)
    if new_ids:
        collection.upsert(ids=new_ids, documents=[cand[i][0] for i in new_ids], metadatas=[cand[i][1] for i in new_ids])
    log(f"ingested {len(new_ids)} NEW news chunks for {company} | {trade_date}  {stats}  (replay_mode={replay})")
    return len(new_ids)


def retrieve_relevant_news(collection, query: str, company: str | None = None, n_results: int = 5,
                           as_of: str | None = None, scopes: list | None = None) -> list:
    """Semantic search + as-of filter + junk/duplicate filter + recency weighting."""
    conds = []
    if company:
        conds.append({"company": company})
    if scopes:
        conds.append({"scope": scopes[0]} if len(scopes) == 1 else {"scope": {"$in": list(scopes)}})
    where = None if not conds else (conds[0] if len(conds) == 1 else {"$and": conds})
    try:
        res = collection.query(query_texts=[query], n_results=max(n_results * 6, 12), where=where)
    except Exception:  # noqa: BLE001
        try:
            res = collection.query(query_texts=[query], n_results=n_results, where=where)
        except Exception:  # noqa: BLE001
            return []
    if not (res["ids"] and res["ids"][0]):
        return []
    try:
        ref = datetime.strptime(as_of, "%Y-%m-%d") if as_of else None
    except Exception:  # noqa: BLE001
        ref = None
    out, seen = [], set()
    for i in range(len(res["ids"][0])):
        meta, text = res["metadatas"][0][i], res["documents"][0][i]
        if is_junk_news(text):
            continue
        if meta.get("scope") in ("web", "sector") and is_directory_text(text):
            continue                                   # a quote/directory chunk stored by an earlier run
        pub = meta.get("pub_date") or meta.get("trade_date", "")
        if as_of and pub and pub > as_of:
            continue                                   # leakage guard: nothing from after the as-of date
        age = 0
        if ref and pub:
            try:
                age = max((ref - datetime.strptime(pub, "%Y-%m-%d")).days, 0)
            except Exception:  # noqa: BLE001
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


def build_news_rag_context(collection, company: str, as_of: str, query: str):
    """Company news (and matching web items) first, then exchange-wide notices, clearly labelled."""
    comp = retrieve_relevant_news(collection, query, company=company, n_results=4, as_of=as_of, scopes=["company", "web"])
    mkt = retrieve_relevant_news(collection, query, company=MARKET_TAG, n_results=2, as_of=as_of, scopes=["market"])
    lines = []
    if comp:
        lines.append(f"COMPANY NEWS RETRIEVED VIA RAG ({company}; published on/before {as_of}; newer items weighted higher):")
        for c in comp:
            lines.append(f"- ({c['metadata'].get('source')}, pub {c['metadata'].get('pub_date')}, "
                         f"{c['age_days']}d old): {c['text'][:240]}")
    else:
        lines.append(f"(No {company}-specific news in the RAG corpus as of {as_of} -- do not invent any.)")
    if mkt:
        lines.append(f"EXCHANGE-WIDE NOTICES (mostly about OTHER companies/funds -- context only, "
                     f"NOT news about {company}; never attribute them to {company}):")
        for c in mkt:
            lines.append(f"- (pub {c['metadata'].get('pub_date')}): {c['text'][:200]}")
    return "\n".join(lines), comp, mkt


# ───────────────────────────── rule-based news sentiment ─────────────────────────────
# A transparent keyword lexicon scores every stored company-news item IN CODE (as-of filtered, recency weighted); the LLM
# only explains the result.  Limits: a lexicon is crude (negation is handled only for a few phrases), exchange notices are
# mostly factual (-> many NEUTRAL) and Bangla text is not scored.  A screen, not ground truth.

_SENT_POS = re.compile(r"\b(?:dividend (?:declared|announced|recommended|increase[sd]?)|(?:cash|stock|bonus) dividend|bonus shares?|"
                       r"profit (?:rose|rises|grew|growth|increase[sd]?|up)|(?:higher|increased|improved|rising|strong) (?:profit|earnings|eps|revenue|sales)|"
                       r"eps (?:rose|increased|up|improved)|record (?:profit|high|earnings)|upgrade[sd]?|surge[sd]?|rall(?:y|ied)|"
                       r"expansion|new (?:order|contract)|growth)\b", re.I)
_SENT_NEG = re.compile(r"\b(?:net loss|loss(?:es)?|profit (?:fell|falls|dropped|decline[sd]?|down)|(?:lower|decreased|declined|falling|weak) (?:profit|earnings|eps|revenue|sales)|"
                       r"eps (?:fell|decreased|down|declined)|no dividend|omit(?:ted)? dividend|downgrade[sd]?|penalt(?:y|ies)|fine[sd]?|"
                       r"suspend(?:ed|s)?|delist(?:ed|ing)?|default(?:ed)?|probe|investigation|fraud|plunge[sd]?|slump(?:ed)?|"
                       r"tax (?:hike|increase)|duty (?:hike|increase)|ban(?:ned)?|warning|circuit breaker (?:hit|lower))\b", re.I)


def score_text_sentiment(text: str):
    """(label, pos_hits, neg_hits) for one chunk. Pure function."""
    p, n = len(_SENT_POS.findall(text)), len(_SENT_NEG.findall(text))
    if p > n:
        return "POSITIVE", p, n
    if n > p:
        return "NEGATIVE", p, n
    return "NEUTRAL", p, n


def rule_based_news_sentiment(collection, company: str, as_of: str) -> dict:
    """Aggregate chunk-level sentiment into a 1-10 score. Returns {"n": 0} when there is nothing to score."""
    try:
        got = collection.get()
    except Exception:  # noqa: BLE001
        return {"n": 0}
    items, seen = [], set()
    for doc, m in zip(got.get("documents", []), got.get("metadatas", [])):
        m = m or {}
        if "scope" not in m or m.get("company") != company:     # company-tagged news only; legacy untagged chunks are ignored
            continue
        if is_junk_news(doc) or is_directory_text(doc):
            continue
        pub = m.get("pub_date") or m.get("trade_date", "")
        if pub and pub > as_of:                                  # leakage guard
            continue
        try:
            age = max((datetime.strptime(as_of, "%Y-%m-%d") - datetime.strptime(pub, "%Y-%m-%d")).days, 0) if pub else 0
        except Exception:  # noqa: BLE001
            age = 0
        if age > SENT_MAX_AGE_DAYS:
            continue
        key = re.sub(r"\s+", " ", doc).lower()[:120]
        if key in seen:
            continue
        seen.add(key)
        items.append((doc, age))
    if not items:
        return {"n": 0}
    pos = neg = neu = 0
    num = den = 0.0
    for doc, age in items:
        label, _p, _n = score_text_sentiment(doc)
        w = 0.5 ** (age / SENT_HALF_LIFE_DAYS)
        s = 1.0 if label == "POSITIVE" else (-1.0 if label == "NEGATIVE" else 0.0)
        pos, neg, neu = pos + (label == "POSITIVE"), neg + (label == "NEGATIVE"), neu + (label == "NEUTRAL")
        num, den = num + w * s, den + w
    net = num / den if den else 0.0                              # -1 .. +1
    score = round(min(10.0, max(1.0, 5.5 + 4.5 * net)), 1)
    n = len(items)
    overall = "BULLISH" if score >= SENT_BULL_AT else ("BEARISH" if score <= SENT_BEAR_AT else "NEUTRAL")
    confidence = "High" if n >= 8 else ("Medium" if n >= 3 else "Low")
    text = (f"RULE-BASED NEWS SENTIMENT (computed in code from {n} company news item(s) in the last {SENT_MAX_AGE_DAYS} days; "
            f"keyword lexicon, recency weighted): {pos} positive, {neg} negative, {neu} neutral. "
            f"Sentiment score {score}/10 ({overall}), data confidence {confidence}. "
            f"Use exactly this score for the company-news row of the table; do not invent other scores.")
    return {"n": n, "pos": pos, "neg": neg, "neu": neu, "score": score, "overall": overall, "confidence": confidence, "text": text}
