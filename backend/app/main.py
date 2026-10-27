"""RAEM Forecast Console backend.

Serves the built page at `/` and the data under `/api`, and runs models on request:
forecasts from the CSV price files (POST /api/forecast) and the company notebooks' AI analysis
with a chosen Ollama model (POST /api/analysis). Every run is stored in SQLite; asking again on the
same date with the same company and model returns the stored result unless `force` is set.

Layout (same as the DSE app):
    app/core/       settings read from the environment
    app/db/         the SQLite run store
    app/models/     request bodies
    app/api/        routes (one file per topic) and their shared helpers
    app/services/   saved company data, search and added companies, live progress of a run
    app/workers/    the background queue that runs forecasts, AI analyses, refreshes and company adds
    app/pipeline/   forecast export scripts, add_company.py and the trading-log archive
    app/ai_engine/  the notebook runner (the analysts live in engine/notebooks/raem_analysts/)

    python -m uvicorn app.main:app --app-dir backend --port 8000      # from the repo folder
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import analysis, companies, forecast, health, refresh, runs, stocks
from app.db import runs as db
from app.workers import jobs


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

# stocks first: it holds "/" and the fixed paths; runs has the "{run_id}" paths, and /api/runs/latest is declared before them
for _r in (stocks, health, companies, forecast, analysis, refresh, runs):
    app.include_router(_r.router)
