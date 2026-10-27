# raem_analysts/international/news_rag.py -- shared by AAPL, IBM, 0300_HK, 2222_SR
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 📰 NEWS RAG — chunk করে news_corpus-এ store + retrieval demo
# ================================================================

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list:
    """সহজ sliding-window chunking — sentence boundary respect করার চেষ্টা করে।"""
    text = text.strip()
    if len(text) <= chunk_size:
        return [text] if text else []

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if end < len(text):
            last_period = chunk.rfind(". ")
            if last_period > chunk_size * 0.5:
                chunk = chunk[:last_period + 1]
                end = start + last_period + 1
        chunks.append(chunk.strip())
        start = end - overlap
    return [c for c in chunks if c]


def ingest_news_to_corpus(company: str, trade_date: str, company_news: str, global_news: str) -> int:
    """News text chunk করে news_corpus collection-এ store করে। Returns chunks added."""
    all_chunks, all_metadatas, all_ids = [], [], []

    for source_name, text in [("company_news", company_news), ("global_news", global_news)]:
        if not text or text.startswith("Error"):
            continue
        chunks = chunk_text(text, chunk_size=500, overlap=50)
        for idx, chunk in enumerate(chunks):
            chunk_id = hashlib.md5(f"{company}_{trade_date}_{source_name}_{idx}".encode()).hexdigest()[:16]
            all_chunks.append(chunk)
            all_metadatas.append({"company": company, "trade_date": trade_date, "source": source_name})
            all_ids.append(chunk_id)

    if all_chunks:
        news_corpus.upsert(ids=all_ids, documents=all_chunks, metadatas=all_metadatas)
        print(f"📰 Ingested {len(all_chunks)} news chunks for {company} | {trade_date}")
        print(f"   Total chunks in news_corpus: {news_corpus.count()}")
    return len(all_chunks)


def retrieve_relevant_news(query: str, company: str = None, n_results: int = 5) -> list:
    """Semantic search দিয়ে news corpus থেকে relevant chunks বের করে।"""
    where_clause = {"company": company} if company else None
    results = chroma_query_with_retry(news_corpus, query_texts=[query], n_results=n_results, where=where_clause)
    chunks = []
    if results["ids"] and results["ids"][0]:
        for i in range(len(results["ids"][0])):
            chunks.append({
                "text": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "distance": results["distances"][0][i],
            })
    return chunks


# ── ACTUALLY RUN IT — news_result (Cell 9 থেকে already in scope) ──
raw_company_news = news_result.get("news_raw", {}).get("company_news", "")
raw_global_news  = news_result.get("news_raw", {}).get("global_news", "")

# patch না দিলে fallback — অন্তত report থেকে ingest হবে
if not raw_company_news and not raw_global_news:
    print("⚠️  news_raw পাওয়া যায়নি — fallback: news_report ব্যবহার হচ্ছে।")
    raw_company_news = news_result.get("news_report", "")

ingest_news_to_corpus(
    company="@@TICKER@@",
    trade_date=TRADE_DATE,
    company_news=raw_company_news,
    global_news=raw_global_news,
)

# ── ✅ NEW: build a real RAG context and wire it into the pipeline ──
# (Previously this corpus was ingested but never actually retrieved for any
# downstream agent — Bull/Bear/Trader/PM/Risk debators never saw it, only
# this cell's own demo print did. Now the retrieval result is stored in
# `news_rag_context` and injected into every debate/decision prompt below.)
print(f"\n{'='*60}")
print("🔍 NEWS RAG RETRIEVAL — building context for downstream agents")
print(f"{'='*60}")
rag_query = f"{COMPANY} stock catalysts, risks, earnings, orders, and analyst sentiment as of {TRADE_DATE}"
relevant_chunks = retrieve_relevant_news(rag_query, company="@@TICKER@@", n_results=5)

if relevant_chunks:
    _rag_lines = []
    for chunk in relevant_chunks:
        meta = chunk["metadata"]
        print(f"\n📄 Source: {meta['source']} | Date: {meta['trade_date']} | Distance: {chunk['distance']:.3f}")
        print(f"   {chunk['text'][:250]}...")
        _rag_lines.append(f"- ({meta['source']}, {meta['trade_date']}): {chunk['text'][:280]}")
    news_rag_context = (
        "RELEVANT NEWS RETRIEVED VIA RAG FROM THE STORED NEWS CORPUS "
        "(may include earlier days' articles, ranked by semantic relevance):\n"
        + "\n".join(_rag_lines)
    )
else:
    news_rag_context = "(No relevant news chunks found in the RAG corpus yet.)"
    print("   (কোনো relevant chunk পাওয়া যায়নি)")

print(f"\n✅ news_rag_context ready ({len(relevant_chunks)} chunks) — will be injected into Bull/Bear/Trader/Risk/PM prompts.")
print(f"{'='*60}\n")

