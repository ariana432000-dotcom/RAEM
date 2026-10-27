"""
RAEM memory layer — the piece that makes this more than a one-shot analyst.

Ports the notebook's ChromaDB setup, rule-based regime tagging, outcome
backfill (resolving old PENDING episodes with today's price into P&L), episode
retrieval, and episode saving. Everything is lazily initialised so the web app
starts instantly and only touches Ollama/Chroma when a real run begins.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta

from . import config
from .render import first_signal


class RAEMMemory:
    """Wraps the two Chroma collections and the RAEM operations over them."""

    def __init__(self):
        self.client = None
        self.episodic = None
        self.news = None
        self._ready = False

    def connect(self):
        """Open the persistent Chroma store and its two collections. Safe to call twice. The embedding mode (Ollama's
        nomic-embed-text, chromadb's built-in model, or word hashing) comes from RAEM_EMBED / embeddings.embed_mode()."""
        if self._ready:
            return
        import chromadb
        from . import embeddings
        mode = os.environ.get("RAEM_EMBED") or embeddings.embed_mode()
        self.mode = mode
        ef = embeddings.embedding_function(mode)
        self.client = chromadb.PersistentClient(path=embeddings.store_path(mode))
        self.episodic = self.client.get_or_create_collection(
            name="raem_episodic_memory", embedding_function=ef,
            metadata={"description": "Regime-tagged daily trading episodes for RAEM"})
        self.news = self.client.get_or_create_collection(
            name="raem_news_corpus", embedding_function=ef,
            metadata={"description": "Chunked news articles for semantic retrieval"})
        self._ready = True

    def counts(self) -> dict:
        try:
            self.connect()
            return {"episodes": self.episodic.count(), "news_chunks": self.news.count()}
        except Exception as e:  # noqa: BLE001
            return {"episodes": 0, "news_chunks": 0, "error": str(e)}

    # -- outcome backfill ----------------------------------------------------
    def backfill_pending_outcomes(self, company: str, today_date: str, min_days_held: int = 1) -> dict:
        """Resolve PENDING episodes older than min_days_held using today's close."""
        self.connect()
        try:
            pending = self.episodic.get(
                where={"$and": [{"company": company}, {"outcome_status": "PENDING"}]}
            )
        except Exception as e:  # noqa: BLE001
            return {"resolved": 0, "note": f"query error: {e}"}

        if not pending["ids"]:
            return {"resolved": 0, "note": "no pending episodes"}

        today_close = _fetch_latest_close(company, today_date)
        if today_close is None:
            return {"resolved": 0, "note": "could not fetch today's close"}

        resolved = 0
        details = []
        for ep_id, meta in zip(pending["ids"], pending["metadatas"]):
            ep_date = meta.get("trade_date", "")
            try:
                days_held = (datetime.strptime(today_date, "%Y-%m-%d")
                             - datetime.strptime(ep_date, "%Y-%m-%d")).days
            except ValueError:
                continue
            if days_held < min_days_held:
                continue
            try:
                entry_price = float(meta.get("entry_price", "N/A"))
            except (TypeError, ValueError):
                continue

            signal = meta.get("final_signal", "HOLD")
            if signal == "BUY":
                pnl = ((today_close - entry_price) / entry_price) * 100
            elif signal == "SELL":
                pnl = ((entry_price - today_close) / entry_price) * 100
            else:
                pnl = 0.0

            label = "WIN" if pnl > 0.5 else "LOSS" if pnl < -0.5 else "FLAT"
            meta.update({
                "outcome_status": "RESOLVED",
                "exit_price": str(round(today_close, 2)),
                "pnl_pct": str(round(pnl, 2)),
                "outcome_label": label,
                # ✅ FIXED: MemoryRecord.from_chroma() (app/ai_engine/memory/schemas.py)
                # reads the separate "outcome" key, not "outcome_status"/"outcome_label" --
                # this key was never touched here, so the RAEM Memory panel (which reads
                # via the Memory API) kept showing "PENDING" forever even after an episode
                # was fully resolved by this function. Keep both key sets in sync.
                "outcome": label,
            })
            self.episodic.update(ids=[ep_id], metadatas=[meta])
            resolved += 1
            details.append({"date": ep_date, "signal": signal, "pnl_pct": round(pnl, 2), "label": label})

        return {"resolved": resolved, "details": details, "today_close": today_close}

    # -- one-time migration: sync stale "outcome" key on already-RESOLVED episodes --
    def sync_outcome_keys(self, company: str | None = None) -> dict:
        """Fix existing episodes that were RESOLVED before the outcome-key fix above
        went in. Those records have outcome_status=RESOLVED + a correct outcome_label
        (WIN/LOSS/FLAT), but their separate "outcome" key (read by
        MemoryRecord.from_chroma / the RAEM Memory panel) was never updated and is
        stuck on the "PENDING" default. Finds every RESOLVED episode whose "outcome"
        still disagrees with "outcome_label" and patches it in place.

        Safe to run repeatedly (no-op once everything is in sync). Pass `company`
        to scope to one ticker, or omit to sweep the whole episodic collection.
        """
        self.connect()
        where = {"outcome_status": "RESOLVED"} if not company else {
            "$and": [{"company": company}, {"outcome_status": "RESOLVED"}]
        }
        try:
            rows = self.episodic.get(where=where)
        except Exception as e:  # noqa: BLE001
            return {"fixed": 0, "checked": 0, "note": f"query error: {e}"}

        ids, metas = rows.get("ids", []), rows.get("metadatas", [])
        fixed_ids, fixed_details = [], []
        for ep_id, meta in zip(ids, metas):
            label = meta.get("outcome_label")
            if not label or label == "N/A":
                continue  # nothing correct to migrate from
            if meta.get("outcome") == label:
                continue  # already in sync
            meta = dict(meta)
            meta["outcome"] = label
            self.episodic.update(ids=[ep_id], metadatas=[meta])
            fixed_ids.append(ep_id)
            fixed_details.append({
                "company": meta.get("company"), "trade_date": meta.get("trade_date"),
                "outcome_label": label,
            })

        return {"checked": len(ids), "fixed": len(fixed_ids), "details": fixed_details}

    # -- regime helpers ------------------------------------------------------
    def most_recent_regime(self, company: str, exclude_date: str | None = None):
        self.connect()
        try:
            all_eps = self.episodic.get(where={"company": company})
        except Exception:  # noqa: BLE001
            return None, None
        if not all_eps["ids"]:
            return None, None
        dated = []
        for m in all_eps["metadatas"]:
            td = m.get("trade_date", "")
            if td != (exclude_date or ""):
                dated.append((td, m.get("regime", "")))
        if not dated:
            return None, None
        dated.sort(key=lambda x: x[0])
        return dated[-1][1], dated[-1][0]

    def reflect_on_regime_transition(self, company, previous_regime, new_regime, llm) -> str:
        self.connect()
        try:
            resolved = self.episodic.get(
                where={"$and": [
                    {"company": company},
                    {"regime": previous_regime},
                    {"outcome_status": "RESOLVED"},
                ]}
            )
        except Exception as e:  # noqa: BLE001
            return f"(Reflection skipped due to query error: {e})"

        if not resolved["ids"]:
            return (f"(Regime changed {previous_regime} -> {new_regime}, but no RESOLVED "
                    f"outcomes exist yet for {previous_regime} to reflect on.)")

        lines = []
        for meta in resolved["metadatas"]:
            lines.append(
                f"- {meta.get('trade_date', '?')}: Signal={meta.get('final_signal', '?')}, "
                f"P&L={meta.get('pnl_pct', '?')}%, Outcome={meta.get('outcome_label', '?')}"
            )
        history_text = "\n".join(lines)
        prompt = (
            f"You are a trading reflection agent. The market regime for {company} has just "
            f"transitioned from {previous_regime} to {new_regime}.\n\n"
            f"Here is the outcome history of past decisions made during the {previous_regime} regime:\n"
            f"{history_text}\n\n"
            f"In 3-4 concise sentences, reflect on:\n"
            f"1. Whether decisions in {previous_regime} tended to be profitable or not.\n"
            f"2. What that suggests about decision-making as we now enter a {new_regime} regime.\n"
            f"3. One concrete caution or adjustment for the trader to consider.\n\n"
            f"Be specific and reference the actual P&L numbers above. Do not invent data not shown."
        )
        try:
            return llm.invoke(prompt).content.strip()
        except Exception as e:  # noqa: BLE001
            return f"(Reflection LLM call failed: {e})"

    # -- retrieval -----------------------------------------------------------
    def retrieve_similar_episodes(self, query_text, company=None, regime_filter=None, n_results=5) -> list:
        self.connect()
        where = _build_where(company, regime_filter)
        try:
            results = self.episodic.query(query_texts=[query_text], n_results=n_results, where=where)
        except Exception:  # noqa: BLE001
            return []
        episodes = []
        if results["ids"] and results["ids"][0]:
            for i in range(len(results["ids"][0])):
                episodes.append({
                    "id": results["ids"][0][i],
                    "document": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i],
                    "distance": results["distances"][0][i],
                })
        return episodes

    def list_episodes(self, company: str | None = None, limit: int = 100) -> list:
        """All stored episodes for the memory browser, newest first."""
        self.connect()
        where = {"company": company} if company else None
        try:
            got = self.episodic.get(where=where)
        except Exception:  # noqa: BLE001
            return []
        rows = []
        for _id, doc, meta in zip(got["ids"], got["documents"], got["metadatas"]):
            rows.append({"id": _id, "document": doc, "metadata": meta})
        rows.sort(key=lambda r: r["metadata"].get("trade_date", ""), reverse=True)
        return rows[:limit]

    # -- news RAG ------------------------------------------------------------
    def ingest_news(self, company, trade_date, company_news, global_news) -> int:
        self.connect()
        chunks, metas, ids = [], [], []
        for source, text in [("company_news", company_news), ("global_news", global_news)]:
            if not text or str(text).startswith("Error"):
                continue
            for idx, chunk in enumerate(_chunk_text(str(text))):
                cid = hashlib.md5(f"{company}_{trade_date}_{source}_{idx}".encode()).hexdigest()[:16]
                chunks.append(chunk)
                # ✅ CHANGED: same key-name mismatch as build_episode_document
                # (see the comment there) -- "company"/"trade_date" meant the
                # Memory API's MemoryRecord.from_chroma() found no "ticker"/
                # "timestamp"/"memory_id" and every news_memory record showed
                # blank in the Memory UI too. Added the compatible keys
                # without removing the originals (nothing else reads these).
                metas.append({
                    "company": company, "trade_date": trade_date, "source": source,
                    "memory_id": cid, "ticker": company, "timestamp": trade_date,
                    "market_regime": "N/A", "agent_name": "raem_pipeline",
                    "summary": chunk[:200], "decision": "N/A", "confidence": 0.0,
                    "risk": "N/A", "version": "1.0", "outcome": "N/A",
                    "experience_score": 0.0,
                })
                ids.append(cid)
        if chunks:
            self.news.upsert(ids=ids, documents=chunks, metadatas=metas)
        return len(chunks)

    # -- episode save --------------------------------------------------------
    def save_episode(self, company, trade_date, indicators, fund_metrics,
                     news_metrics, sentiment_metrics, final_decision_text,
                     stock_data, macro_regime: str | None = None,
                     verifier_status: str | None = None,
                     llm_provider: str | None = None,
                     run_key: str | None = None,
                     confidence: float | None = None) -> dict:
        self.connect()
        # 🔴 FIXED: compute the id ONCE, here, and pass it into
        # build_episode_document() -- it needs the id too, for the
        # "memory_id" field in the Memory-API-compatibility metadata block
        # (see that block's own comment). Previously build_episode_document
        # computed its own separate, second copy of this id internally;
        # removing that (as part of the run_key fix above, since it was
        # only used for the ChromaDB write which already happens here in
        # save_episode) broke memory_id with a NameError, because it turned
        # out NOT to be fully dead code. Single computation now avoids that
        # class of bug entirely -- there's only one id, used everywhere.
        ep_id = make_episode_id(company, trade_date, run_key)
        doc, meta = build_episode_document(
            company, trade_date, indicators, fund_metrics,
            news_metrics, sentiment_metrics, final_decision_text, stock_data,
            macro_regime=macro_regime, verifier_status=verifier_status,
            llm_provider=llm_provider, ep_id=ep_id, confidence=confidence,
        )
        self.episodic.upsert(ids=[ep_id], documents=[doc], metadatas=[meta])
        return {"id": ep_id, "regime": meta["regime"], "signal": meta["final_signal"],
                "document": doc, "metadata": meta}

    # -- post-mortem ----------------------------------------------------------
    def gather_resolved_episodes(self, company: str, max_n: int = 15) -> list[dict]:
        """All of a company's RESOLVED episodes (any regime), newest first.

        Used by the Post-Mortem / Self-Critique agent — unlike
        `reflect_on_regime_transition` (which only looks at one prior regime,
        and only runs on a regime change), this pulls the full cross-regime
        track record every session.
        """
        self.connect()
        try:
            resolved = self.episodic.get(where={"$and": [
                {"company": company},
                {"outcome_status": "RESOLVED"},
            ]})
        except Exception:  # noqa: BLE001
            return []
        if not resolved["ids"]:
            return []
        rows = list(zip(resolved["ids"], resolved["metadatas"]))
        rows.sort(key=lambda r: r[1].get("trade_date", ""), reverse=True)
        return [m for _, m in rows[:max_n]]


# --------------------------------------------------------------------------
# Free functions (usable without a live Chroma connection)
# --------------------------------------------------------------------------
def classify_regime(indicators: dict) -> str:
    """Rule-based market-regime tag from parsed technical indicators."""
    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    rsi = _num(indicators.get("rsi"))
    macd = _num(indicators.get("macd"))
    boll_ub = _num(indicators.get("boll_ub"))
    boll_lb = _num(indicators.get("boll_lb"))

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
        if macd < 0:
            return "TRENDING_BEAR"
    return "NEUTRAL_RANGING"


def make_episode_id(company: str, trade_date: str, run_key: str | None = None) -> str:
    # 🔴 FIXED: ID used to be a pure function of (company, trade_date) --
    # so running TWO analyses for the same ticker on the same calendar
    # day (e.g. Kimi then Sonnet, the documented workflow for building a
    # provider-comparison dataset -- or simply re-running after a bad
    # result) hashed to the IDENTICAL id. save_episode() below writes
    # with .upsert(), so the second run silently OVERWROTE the first
    # run's episode in ChromaDB rather than creating a second one --
    # losing that trade from history entirely, not just mislabeling it.
    # `run_key` (the calling session's start timestamp, second-precision
    # -- see runner.py's save_episode call) makes the id unique per
    # actual run instead. run_key=None keeps the old id shape for any
    # caller that hasn't been updated to pass one.
    key = f"{company}_{trade_date}" if run_key is None else f"{company}_{trade_date}_{run_key}"
    return hashlib.md5(key.encode()).hexdigest()[:16]


def build_episode_document(company, trade_date, indicators, fund_metrics,
                           news_metrics, sentiment_metrics, final_decision_text,
                           stock_data, macro_regime: str | None = None,
                           verifier_status: str | None = None,
                           llm_provider: str | None = None,
                           ep_id: str | None = None,
                           confidence: float | None = None):
    import re
    regime = classify_regime(indicators)
    entry_price = None
    lines = [l for l in str(stock_data).split("\n") if l.strip() and not l.startswith("#")]
    if len(lines) > 1:
        for row in reversed(lines[1:]):
            parts = row.split(",")
            if len(parts) >= 5 and parts[4].strip() and parts[4].strip() not in ("", "Close"):
                try:
                    entry_price = float(parts[4].strip())
                    break
                except ValueError:
                    pass

    # 🔴 FIXED: this was its own fourth independent copy of the same buggy
    # regex found (and fixed) in render.py::first_signal(),
    # orchestrator.py::_signal(), and agents.py::run_decision_verifier() --
    # no word boundary, first-match-anywhere, so a word merely containing
    # "hold" ("shareholders", "stakeholders", ...) appearing before the
    # decision text's own "FINAL TRANSACTION PROPOSAL" line could get
    # saved as this episode's final_signal. This is the most consequential
    # instance of the four: final_signal gets written straight into the
    # episode's permanent ChromaDB metadata (below) and is later read back
    # by backfill_pending_outcomes() (this same file, ~line 115) to decide
    # the P&L formula's sign -- ((close-entry)/entry for BUY vs the
    # inverse for SELL. A wrong final_signal here doesn't just mislabel
    # the episode, it can flip a real WIN into a recorded LOSS (or hide it
    # as a false FLAT), corrupting the win-rate/Sharpe/backtest numbers
    # downstream. Reuses the already-fixed shared implementation.
    final_signal = first_signal(final_decision_text)

    document_text = f"""Trading Episode: {company} on {trade_date}
Market Regime: {regime}
Macro Regime: {macro_regime or 'N/A'}
Technical Indicators: RSI={indicators.get('rsi', 'N/A')}, MACD={indicators.get('macd', 'N/A')}, 50-SMA={indicators.get('close_50_sma', 'N/A')}
Fundamentals: P/E={fund_metrics.get('pe_ratio', 'N/A')}, EPS={fund_metrics.get('eps_ttm', 'N/A')}, Market Cap={fund_metrics.get('market_cap', 'N/A')}
News Sentiment: {news_metrics.get('overall_sentiment', 'N/A')} (Pos:{news_metrics.get('positive_count', 0)} Neg:{news_metrics.get('negative_count', 0)} Neu:{news_metrics.get('neutral_count', 0)})
Social Sentiment Score: {sentiment_metrics.get('score', 'N/A')}/10 ({sentiment_metrics.get('overall', 'N/A')})
Final Decision: {final_signal}
Verifier Status: {verifier_status or 'N/A'}
Decision Rationale Summary: {final_decision_text[:400]}"""

    # (episode id itself is computed once in save_episode() and passed in
    # as `ep_id`, rather than recomputed here -- see that function's
    # comment. Used below for the "memory_id" Memory-API-compat field.)

    metadata = {
        "company": company,
        "trade_date": trade_date,
        "regime": regime,
        "macro_regime": macro_regime or "N/A",
        "verifier_status": verifier_status or "N/A",
        "final_signal": final_signal,
        "rsi": str(indicators.get("rsi", "N/A")),
        "macd": str(indicators.get("macd", "N/A")),
        "pe_ratio": str(fund_metrics.get("pe_ratio", "N/A")),
        "news_sentiment": str(news_metrics.get("overall_sentiment", "N/A")),
        "entry_price": str(entry_price) if entry_price is not None else "N/A",
        "outcome_status": "PENDING",
        "exit_price": "N/A",
        "pnl_pct": "N/A",
        "outcome_label": "N/A",
        # For Kimi-vs-Sonnet (or any provider) comparison: which LLM actually
        # produced this trading decision, e.g. "anthropic:claude-sonnet-5" or
        # "kimi:kimi-k3" -- see app/pipeline/llm.py::provider_label(). Lets
        # eval_metrics.py / backtest.py slice resolved episodes per model.
        "llm_provider": llm_provider or "N/A",
        # ✅ CHANGED: the Memory API (app/ai_engine/memory/schemas.py's
        # MemoryRecord.from_chroma) reads a *different* set of metadata key
        # names than RAEM's own code above uses (ticker vs company, decision
        # vs final_signal, market_regime vs regime, timestamp vs trade_date,
        # memory_id vs the id passed separately to .upsert()) -- so every
        # episode saved here was landing in the same "episodic_memory"
        # collection the Memory page reads, but with metadata the reader
        # couldn't recognize at all. Result: the Memory UI/API showed
        # ticker="", timestamp="", decision="", memory_id="" for every
        # record, even though the underlying document/reasoning text was
        # fully populated. These are ADDED (not replacing the keys above --
        # gather_resolved_episodes/_build_where still filter on "company"/
        # "regime"/"outcome_status") so both readers work off the same record.
        "memory_id": ep_id,
        "ticker": company,
        "timestamp": trade_date,
        "market_regime": regime,
        "agent_name": "raem_pipeline",
        "summary": final_decision_text[:200],
        "decision": final_signal,
        # 🔴 FIXED: this was hardcoded 0.0 for every episode ever saved --
        # the actual confidence (the same number shown on the frontend as
        # "confidence 89%", computed in orchestrator.py's _synthesize()
        # as the mean of the analysts' own reported confidences) was
        # simply never threaded through to here. That silently discarded
        # every episode's confidence at save time, making it impossible
        # to ever check whether RAEM's stated confidence is empirically
        # justified (e.g. "do 85%+ confidence BUYs actually win more
        # often than 50% confidence ones?") -- see
        # eval_metrics.py::confidence_calibration() for that analysis,
        # which depends entirely on this field actually being populated
        # going forward.
        "confidence": float(confidence) if confidence is not None else 0.0,
        "risk": "N/A",
        "source": "raem_pipeline",
        "version": "1.0",
        "outcome": "PENDING",
        "experience_score": 0.0,
    }
    return document_text.strip(), metadata


def _build_where(company=None, regime_filter=None):
    conditions = []
    if company:
        conditions.append({"company": company})
    if regime_filter:
        conditions.append({"regime": regime_filter})
    if len(conditions) == 0:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list:
    text = text.strip()
    if len(text) <= chunk_size:
        return [text] if text else []
    chunks, start = [], 0
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


def _fetch_latest_close(company: str, as_of_date: str):
    """Latest close price for outcome backfill.

    🔴 FIXED (confirmed live): the previous version called
    .data_providers.get_stock_data, which is hardcoded to FMP only.
    FMP doesn't cover DSE-listed tickers at all -- so for ANY DSE ticker,
    that call silently returned "no price data", this function returned
    None, and backfill_pending_outcomes() always short-circuited to
    "could not fetch today's close". PENDING episodes for DSE tickers
    could therefore NEVER resolve to RESOLVED no matter how much time
    passed, which is why Post-Mortem Review showed "No resolved episodes
    yet" indefinitely for DSE tickers specifically -- not a "give it more
    time" situation as it looked like, a genuine dead end.

    Now goes through market_data.fetch_ohlcv(), which already correctly
    routes DSE tickers to bdshare and everything else to yfinance/Stooq
    -- the same function the price-history/quote endpoints rely on, so
    this is one consistent source instead of a second, US-only one that
    happened to work for non-DSE tickers by coincidence.
    """
    from .market_data import fetch_ohlcv
    end_dt = datetime.strptime(as_of_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=10)
    try:
        rows = fetch_ohlcv(company, start_dt.strftime("%Y-%m-%d"), as_of_date)
    except Exception:  # noqa: BLE001
        return None
    for row in reversed(rows or []):
        close = row.get("close")
        if close is not None:
            try:
                return float(close)
            except (TypeError, ValueError):
                continue
    return None
