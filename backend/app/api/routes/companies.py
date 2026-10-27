"""Search any company and add it to the site (prices, notebook, forecast), then use it like the first eight."""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.api import deps
from app.core import config
from app.services import companies as company_data

router = APIRouter()


class CompanyRequest(BaseModel):
    ticker: str
    market: str = Field("auto", description="auto, international (Yahoo Finance) or dse (Dhaka Stock Exchange)")
    name: str | None = Field(None, description="Company name; looked up when empty")
    sector: str | None = Field(None, description="DSE only: sector text for the analysts")


@router.get("/api/search")
def search(q: str = Query("", description="Ticker or company name")):
    return company_data.search(q)


@router.get("/api/companies")
def companies():
    """Companies added from the website. The eight research companies are in /api/tickers like these."""
    return company_data.added()


@router.get("/api/companies/{ticker}")
def company(ticker: str):
    """The saved forecast of a company plus its price table, backtest and FX rate (what the page shows for it)."""
    return company_data.bundle(deps.known(ticker))


@router.post("/api/companies", status_code=202)
def add_company(req: CompanyRequest, authorization: str | None = Header(None)):
    """Add a company: download its prices, create its notebook, run the forecast. A few minutes; poll /api/jobs/{id}."""
    deps.check_token(config.RUN_TOKEN, authorization, "Adding a company")
    try:
        t = company_data.normalize(req.ticker)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if req.market not in ("auto", "international", "dse"):
        raise HTTPException(400, "market must be auto, international or dse.")
    if t in config.tickers():
        return {"run": None, "reused": "exists", "ticker": t}
    params = {"market": req.market, "name": (req.name or "").strip() or None, "sector": (req.sector or "").strip() or None}
    run, reused = deps.submit("company", t, req.market, params)
    return deps.submitted(run, reused)
