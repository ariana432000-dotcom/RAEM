# raem_analysts/international/chromadb_memory_setup.py -- shared by AAPL, IBM, 0300_HK, 2222_SR
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 🧠 ChromaDB RAG SETUP — Episodic Memory + News Corpus
# ================================================================
# প্রথমবার চালানোর আগে টার্মিনালে: ollama pull nomic-embed-text

import chromadb
from chromadb.utils import embedding_functions
import hashlib

# ── Ollama embedding function (local, CPU-friendly, no API key) ──
ollama_ef = embedding_functions.OllamaEmbeddingFunction(
    url="http://localhost:11434/api/embeddings",
    model_name="nomic-embed-text",
)

# ── Persistent client — disk-এ save হয়, kernel restart-এও থাকে ──
CHROMA_DB_PATH = "./raem_chroma_db"
chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)

# ── Collection 1: Episodic Memory (RAEM core) ──
episodic_memory = chroma_client.get_or_create_collection(
    name="raem_episodic_memory",
    embedding_function=ollama_ef,
    metadata={"description": "Regime-tagged daily trading episodes for RAEM"},
)

# ── Collection 2: News Corpus (RAG retrieval) ──
news_corpus = chroma_client.get_or_create_collection(
    name="raem_news_corpus",
    embedding_function=ollama_ef,
    metadata={"description": "Chunked news articles for semantic retrieval"},
)

print(f"✅ ChromaDB ready at: {CHROMA_DB_PATH}")
print(f"   📦 episodic_memory  : {episodic_memory.count()} episodes stored")
print(f"   📰 news_corpus      : {news_corpus.count()} chunks stored")


# ================================================================
# 🏷️ REGIME TAGGING — simple rule-based classifier
# ================================================================
def classify_regime(indicators: dict) -> str:
    """
    market_result["indicators_parsed"] থেকে regime tag বের করে।
    Tags: OVERBOUGHT, OVERSOLD, TRENDING_BULL, TRENDING_BULL_HIGH_VOL,
          TRENDING_BEAR, NEUTRAL_RANGING
    """
    rsi = indicators.get("rsi")
    macd = indicators.get("macd")
    boll_ub = indicators.get("boll_ub")
    boll_lb = indicators.get("boll_lb")

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    rsi, macd, boll_ub, boll_lb = _num(rsi), _num(macd), _num(boll_ub), _num(boll_lb)

    if rsi is not None:
        if rsi >= 70:
            return "OVERBOUGHT"
        if rsi <= 30:
            return "OVERSOLD"

    if macd is not None:
        if macd > 0:
            band_width = None
            if boll_ub is not None and boll_lb is not None and boll_lb != 0:
                band_width = (boll_ub - boll_lb) / boll_lb
            if band_width is not None and band_width > 0.05:
                return "TRENDING_BULL_HIGH_VOL"
            return "TRENDING_BULL"
        elif macd < 0:
            return "TRENDING_BEAR"

    return "NEUTRAL_RANGING"


def make_episode_id(company: str, trade_date: str, run_id: str = None) -> str:
    """Deterministic ID.
    ✅ CHANGED: আগে শুধু company+trade_date দিয়ে ID বানানো হতো — মানে একই দিনে
    একাধিকবার (যেমন প্রতি ২ ঘণ্টায়) pipeline চালালে প্রতিবার আগের episode
    overwrite (upsert) হয়ে যেত, শুধু দিনের শেষ run-টাই থেকে যেত।
    এখন run_id (SESSION_ID) দিলে প্রতিটা run-এর জন্য আলাদা, unique episode
    ID তৈরি হয় — একই দিনে একাধিক run করলে সবগুলোই আলাদাভাবে store হবে,
    কোনোটাই overwrite হবে না। run_id না দিলে পুরোনো per-day (overwrite)
    আচরণ বজায় থাকে (backward-compatible)।
    """
    suffix = run_id or trade_date
    return hashlib.md5(f"{company}_{trade_date}_{suffix}".encode()).hexdigest()[:16]


print("✅ Regime classifier + ID helper ready!")


def chroma_query_with_retry(collection, max_attempts: int = 3, retry_delay: float = 1.5, **query_kwargs):
    """Wraps collection.query() with retries for transient ChromaDB errors --
    concurrent access from another process running the same notebook can
    produce "Error creating hnsw segment reader: Nothing found on disk" even
    though the database itself is fine (seen in practice: two kernels open on
    the same raem_chroma_db/ at once). On repeated failure, degrades to an
    empty result (as if nothing matched) instead of crashing the notebook --
    a memory lookup that returns nothing is recoverable; a crashed pipeline
    mid-run is not."""
    import time
    last_err = None
    for attempt in range(max_attempts):
        try:
            return collection.query(**query_kwargs)
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            transient = ("hnsw" in msg or "nothing found on disk" in msg
                         or "internal error" in msg)
            if transient and attempt < max_attempts - 1:
                print(f"   [ChromaDB] transient query error (attempt {attempt + 1}/{max_attempts}); "
                      f"retrying in {retry_delay}s: {e}")
                time.sleep(retry_delay)
                continue
            if not transient:
                raise
    print(f"   [ChromaDB] query failed after {max_attempts} attempts ({last_err}); "
          "treating as 'no results found' rather than crashing the notebook.")
    n = len(query_kwargs.get("query_texts") or [""])
    empty = [[] for _ in range(n)]
    return {"ids": empty, "documents": empty, "metadatas": empty, "distances": empty}


print("Chroma retry helper ready (guards against transient concurrent-access errors)!")

