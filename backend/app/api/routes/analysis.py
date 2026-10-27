from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from app.api import deps
from app.core import config
from app.db import runs as db
from app.models.schemas import AnalysisRequest
from app.services import companies as company_data

router = APIRouter()


@router.post("/api/analysis", status_code=202)
def run_analysis(req: AnalysisRequest, authorization: str | None = Header(None)):
    """Run the company's notebook (Run All) with an Ollama model or a cloud LLM. Hours with a 7B model on a CPU, minutes in the cloud."""
    deps.check_token(config.RUN_TOKEN, authorization, "Running an AI analysis")
    nbs = config.notebooks()
    ticker = req.ticker.strip()
    if ticker not in nbs:
        # Any company can be analysed: one without a notebook gets it made first from the shared analyst files
        # (prices, registration, notebook), so no company depends on a hand-made notebook file.
        try:
            ticker = company_data.normalize(ticker)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if req.market not in ("auto", "international", "dse"):
            raise HTTPException(400, "market must be auto, international or dse.")
        req = req.model_copy(update={"ticker": ticker})
    else:
        req = req.model_copy(update={"ticker": ticker})
    provider = req.llm_provider
    if provider not in config.PROVIDER_KEYS:
        raise HTTPException(400, f"Unknown LLM provider {provider}. Choose one of: {', '.join(config.PROVIDER_KEYS)}")
    cloud = config.cloud_provider(provider)
    model = req.llm_model or (cloud["models"][0] if cloud and cloud["models"] else config.DEFAULT_LLM)
    key = config.llm_key(provider, model)
    if not req.force:
        stored = db.find("analysis", req.ticker, config.today(), key, ("done",))
        if stored:
            return deps.submitted(stored, "stored")
    if cloud:
        if not cloud["ok"]:
            raise HTTPException(400, f"{cloud['label']} needs an API key: add {cloud['key_env']}=... to the server's .env and restart it.")
        if model not in cloud["models"]:
            raise HTTPException(400, f"Model {model} is not in the {cloud['label']} list: {', '.join(cloud['models'])}. "
                                     f"Change the list with RAEM_{provider.upper()}_MODELS.")
    else:
        llm = config.llm_models(max_age=0)  # check the server right now before starting a long run
        if not llm["ok"]:
            raise HTTPException(503, llm["error"] + " Start Ollama or set OLLAMA_BASE_URL.")
        if model not in llm["models"]:
            raise HTTPException(400, f"Model {model} is not on the Ollama server. Available: {', '.join(llm['models']) or 'none'}")
    # Without Ollama's nomic-embed-text the run still works: the memory uses chromadb's built-in model (separate store).
    run, reused = deps.submit("analysis", req.ticker, key, {"provider": provider, "llm_model": model, "notebook": nbs.get(req.ticker),
                                                   "market": req.market, "name": (req.name or "").strip() or None},
                              force=req.force)
    return deps.submitted(run, reused)
