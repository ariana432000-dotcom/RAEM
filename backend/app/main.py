"""RAEM Forecast Console backend.

Serves the built page at `/` and the data under `/api`, and runs models on request:
forecasts from the CSV price files (POST /api/forecast) and the company notebooks' AI analysis
with a chosen Ollama model (POST /api/analysis). Every run is stored in SQLite; asking again on the
same date with the same company and model returns the stored result unless `force` is set.

    python -m uvicorn app.main:app --app-dir backend --port 8000      # from the web/ folder
"""
from __future__ import annotations

import json
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import config, db, jobs


@asynccontextmanager
async def lifespan(_app):
    db.init()
    jobs.start_worker()
    yield


app = FastAPI(title="RAEM Forecast Console", version="2.0.0", lifespan=lifespan,
              description="Seven-day stock forecasts and notebook AI analysis from the RAEM research code. Research output, not investment advice.")
origins = [o.strip() for o in os.environ.get("RAEM_CORS_ORIGINS", "").split(",") if o.strip()]
if origins:
    # allow_private_network: a page on a public HTTPS site (e.g. GitHub Pages) may reach this server on localhost or the LAN
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"],
                       allow_private_network=True)


class ForecastRequest(BaseModel):
    ticker: str
    models: list[str] = Field(default_factory=lambda: list(config.DEFAULT_MODELS), description="forecast_lib model keys")
    force: bool = Field(False, description="Run again even if a run with the same date and models is stored")


class AnalysisRequest(BaseModel):
    ticker: str
    llm_provider: str = Field("ollama", description="ollama, openai, anthropic, google or groq")
    llm_model: str | None = Field(None, description="Model of that provider; the provider's default when empty")
    force: bool = False


def _known(t: str) -> str:
    if t not in config.tickers():
        raise HTTPException(404, f"Unknown ticker {t}. Known: {', '.join(config.tickers())}")
    return t


def _load(t: str) -> dict:
    return json.loads((config.TICKERS_DIR / f"{_known(t)}.json").read_text(encoding="utf-8"))


def _check(token: str, authorization: str | None, what: str) -> None:
    if token and not secrets.compare_digest(authorization or "", f"Bearer {token}"):
        raise HTTPException(401, f"{what} needs the server's access token (Settings page, or the Authorization header).")


def _submitted(run: dict, reused: str | None) -> dict:
    return {"run": run, "reused": reused}


def _submit(*args, **kw):
    try:
        return jobs.submit(*args, **kw)
    except jobs.QueueFull as e:
        raise HTTPException(429, str(e))


# ---------- page and saved data ----------

@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)  # HEAD for hosts' uptime checks
def index():
    if not config.INDEX.exists():
        raise HTTPException(503, "The page is not built yet. Run: python backend/refresh.py --build-only")
    return FileResponse(config.INDEX, media_type="text/html")


@app.get("/api/health")
def health():
    llm = config.llm_models()
    return {"status": "ok", "page_built": config.INDEX.exists(), "tickers": config.tickers(), "date": config.today(),
            "refresh_enabled": bool(config.REFRESH_TOKEN), "run_token_required": bool(config.RUN_TOKEN),
            "ollama": {"ok": llm["ok"], "url": llm["url"], "embeddings_ok": llm["embeddings_ok"]},
            "queue": db.active_count()}


@app.get("/api/models")
def models():
    """What can be run: forecast models (with the default six) and the LLMs for AI analysis (Ollama and cloud providers)."""
    llm = config.llm_models()
    return {"forecast": [{"key": k, "label": l, "default": k in config.DEFAULT_MODELS} for k, l in config.FORECAST_MODELS],
            "llm": llm, "llm_providers": config.llm_providers(), "embeddings_ok": llm["embeddings_ok"],
            "analysis_tickers": sorted(config.notebooks()), "notebooks": config.notebooks()}


@app.get("/api/tickers")
def tickers():
    out = []
    for t in config.tickers():
        d = _load(t)
        f = d["forecast"]
        ens = f["paths"].get("Ensemble")
        out.append({"ticker": t, "name": d["meta"]["name"], "currency": d["meta"]["currency"],
                    "last_price": f["last_price"], "last_date": f["last_date"],
                    "ensemble_day7": ens[6] if ens else None,
                    "ensemble_return_pct": (ens[6] / f["last_price"] - 1) * 100 if ens else None,
                    "daily_signal": d["daily"]["final_signal"], "daily_data_end": d["daily"].get("data_end_date")})
    return out


@app.get("/api/tickers/{ticker}")
def ticker(ticker: str):
    return _load(ticker)


@app.get("/api/extra")
def extra():
    if not config.EXTRA.exists():
        raise HTTPException(503, "Extra data not exported yet. Run: python backend/refresh.py")
    return FileResponse(config.EXTRA, media_type="application/json")


# ---------- runs ----------

@app.post("/api/forecast", status_code=202)
def run_forecast(req: ForecastRequest, authorization: str | None = Header(None)):
    """Forecast one company from its CSV price data with the chosen models (blends are added automatically)."""
    _check(config.RUN_TOKEN, authorization, "Running a forecast")
    t = _known(req.ticker)
    unknown = sorted(set(req.models) - set(config.FORECAST_KEYS))
    if unknown or not req.models:
        what = f"Unknown model: {', '.join(unknown)}. " if unknown else ""
        raise HTTPException(400, f"{what}Choose at least one of: {', '.join(config.FORECAST_KEYS)}")
    models = sorted(set(req.models), key=config.FORECAST_KEYS.index)
    run, reused = _submit("forecast", t, ",".join(models), {"models": models}, force=req.force)
    return _submitted(run, reused)


@app.post("/api/analysis", status_code=202)
def run_analysis(req: AnalysisRequest, authorization: str | None = Header(None)):
    """Run the company's notebook (Run All) with an Ollama model or a cloud LLM. Hours with a 7B model on a CPU, minutes in the cloud."""
    _check(config.RUN_TOKEN, authorization, "Running an AI analysis")
    nbs = config.notebooks()
    if req.ticker not in nbs:
        raise HTTPException(404, f"No notebook for {req.ticker}. Notebooks exist for: {', '.join(sorted(nbs))}")
    provider = req.llm_provider
    if provider not in config.PROVIDER_KEYS:
        raise HTTPException(400, f"Unknown LLM provider {provider}. Choose one of: {', '.join(config.PROVIDER_KEYS)}")
    cloud = config.cloud_provider(provider)
    model = req.llm_model or (cloud["models"][0] if cloud and cloud["models"] else config.DEFAULT_LLM)
    key = config.llm_key(provider, model)
    if not req.force:
        stored = db.find("analysis", req.ticker, config.today(), key, ("done",))
        if stored:
            return _submitted(stored, "stored")
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
    run, reused = _submit("analysis", req.ticker, key, {"provider": provider, "llm_model": model, "notebook": nbs[req.ticker]},
                          force=req.force)
    return _submitted(run, reused)


@app.post("/api/refresh", status_code=202)
def run_refresh(tickers: str | None = Query(None, description="Comma-separated tickers; all when empty"),
                extra: bool = True, top_up: bool = True, authorization: str | None = Header(None)):
    """Re-run the default forecasts for all (or some) companies and rebuild the page."""
    if not config.REFRESH_TOKEN:
        raise HTTPException(403, "Refresh is turned off. Set RAEM_REFRESH_TOKEN on the server to turn it on.")
    _check(config.REFRESH_TOKEN, authorization or "", "Refresh")
    tks = [t.strip() for t in tickers.split(",") if t.strip()] if tickers else None
    if tks:
        for t in tks:
            _known(t)
    run, reused = jobs.submit("refresh", "*", json.dumps({"t": tks, "e": extra, "u": top_up}),
                              {"tickers": tks, "extra": extra, "top_up": top_up}, force=True)
    return run


@app.get("/api/runs")
def runs(kind: str | None = None, ticker: str | None = None, limit: int = 50, status: str | None = None):
    """Run history, newest first (without the large results). `status` filters, e.g. `queued,running`."""
    statuses = tuple(s for s in (status or "").split(",") if s in ("queued", "running", "held", "done", "failed"))
    return [jobs.public(r) for r in db.list_runs(kind, ticker, limit, statuses)]


@app.get("/api/runs/latest")
def runs_latest(date: str | None = None):
    """The newest finished forecast and analysis per company for a date (today by default; `all` for any date), with results."""
    d = None if date == "all" else (date or config.today())
    return {"date": d or "all", "forecast": db.latest_done(d, "forecast"), "analysis": db.latest_done(d, "analysis")}


@app.get("/api/runs/{run_id}/live")
def run_live(run_id: str):
    """While an AI analysis runs: the session the notebook has written so far (it saves its log after every agent),
    so the page can show each agent's output as it finishes. A finished run returns its stored entry."""
    r = db.get(run_id)
    if r is None or r["kind"] != "analysis":
        raise HTTPException(404, "No such analysis run")
    if r["status"] == "done":
        return {"status": "done", "entry": (r.get("result") or {}).get("entry")}
    entry = None
    if r["status"] == "running" and r.get("started_at"):
        try:
            import sys
            sys.path.insert(0, str(config.BACKEND / "exporter"))
            from sessions import ai_entry
            entry = ai_entry(str(config.ENGINE / "notebooks"), r["ticker"], since=r["started_at"][:16])
        except Exception:  # the notebook may be writing its log right now; the page asks again
            entry = None
    return {"status": r["status"], "entry": entry}


@app.post("/api/runs/{run_id}/hold")
def run_hold(run_id: str, authorization: str | None = Header(None)):
    """Hold a queued or running AI analysis: it stops, its saved LLM answers stay, and the next request for the same
    company, date and model resumes it (answers whose prompts are unchanged are reused)."""
    _check(config.RUN_TOKEN, authorization, "Holding a run")
    r = db.get(run_id, with_result=False)
    if r is None:
        raise HTTPException(404, "No such run")
    if r["kind"] != "analysis":
        raise HTTPException(400, "Only AI analysis runs can be held; a forecast takes a few minutes.")
    if r["status"] not in ("queued", "running"):
        raise HTTPException(409, f"This run is {r['status']}, so there is nothing to hold.")
    return jobs.hold(run_id)


@app.get("/api/runs/{run_id}")
def run(run_id: str):
    r = jobs.get(run_id)
    if r is None:
        raise HTTPException(404, "No such run.")
    return r


@app.get("/api/jobs/{run_id}")
def job(run_id: str):
    """Progress of a run (same as /api/runs/{id}, without the result)."""
    r = jobs.get(run_id, with_result=False)
    if r is None:
        raise HTTPException(404, "No such job.")
    return r
