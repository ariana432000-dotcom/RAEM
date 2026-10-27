"""Run history, live progress of an AI analysis, hold, and a single run."""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from app.api import deps
from app.core import config
from app.db import runs as db
from app.services import live
from app.workers import jobs

router = APIRouter()


@router.get("/api/runs")
def runs(kind: str | None = None, ticker: str | None = None, limit: int = 50, status: str | None = None):
    """Run history, newest first (without the large results). `status` filters, e.g. `queued,running`."""
    statuses = tuple(s for s in (status or "").split(",") if s in ("queued", "running", "held", "done", "failed"))
    return [jobs.public(r) for r in db.list_runs(kind, ticker, limit, statuses)]


@router.get("/api/runs/latest")
def runs_latest(date: str | None = None):
    """The newest finished forecast and analysis per company for a date (today by default; `all` for any date), with results."""
    d = None if date == "all" else (date or config.today())
    return {"date": d or "all", "forecast": db.latest_done(d, "forecast"), "analysis": db.latest_done(d, "analysis")}


@router.get("/api/runs/{run_id}/live")
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
        entry = live.live_entry(r["ticker"], r["started_at"])
    return {"status": r["status"], "entry": entry}


@router.post("/api/runs/{run_id}/hold")
def run_hold(run_id: str, authorization: str | None = Header(None)):
    """Hold a queued or running AI analysis: it stops, its saved LLM answers stay, and the next request for the same
    company, date and model resumes it (answers whose prompts are unchanged are reused)."""
    deps.check_token(config.RUN_TOKEN, authorization, "Holding a run")
    r = db.get(run_id, with_result=False)
    if r is None:
        raise HTTPException(404, "No such run")
    if r["kind"] != "analysis":
        raise HTTPException(400, "Only AI analysis runs can be held; a forecast takes a few minutes.")
    if r["status"] not in ("queued", "running"):
        raise HTTPException(409, f"This run is {r['status']}, so there is nothing to hold.")
    return jobs.hold(run_id)


@router.get("/api/runs/{run_id}")
def run(run_id: str):
    r = jobs.get(run_id)
    if r is None:
        raise HTTPException(404, "No such run.")
    return r


@router.get("/api/jobs/{run_id}")
def job(run_id: str):
    """Progress of a run (same as /api/runs/{id}, without the result)."""
    r = jobs.get(run_id, with_result=False)
    if r is None:
        raise HTTPException(404, "No such job.")
    return r
