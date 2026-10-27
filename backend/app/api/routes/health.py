from __future__ import annotations

from fastapi import APIRouter

from app.core import config
from app.db import runs as db

router = APIRouter()


@router.get("/api/health")
def health():
    llm = config.llm_models()
    return {"status": "ok", "page_built": config.INDEX.exists(), "tickers": config.tickers(), "date": config.today(),
            "refresh_enabled": bool(config.REFRESH_TOKEN), "run_token_required": bool(config.RUN_TOKEN),
            "ollama": {"ok": llm["ok"], "url": llm["url"], "embeddings_ok": llm["embeddings_ok"]},
            "queue": db.active_count()}


@router.get("/api/models")
def models():
    """What can be run: forecast models (with the default six) and the LLMs for AI analysis (Ollama and cloud providers)."""
    llm = config.llm_models()
    return {"forecast": [{"key": k, "label": l, "default": k in config.DEFAULT_MODELS} for k, l in config.FORECAST_MODELS],
            "llm": llm, "llm_providers": config.llm_providers(), "embeddings_ok": llm["embeddings_ok"],
            "analysis_tickers": config.tickers()}  # companies on the site; any other ticker can be analysed too
