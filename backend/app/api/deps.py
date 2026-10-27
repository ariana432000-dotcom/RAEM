"""Small helpers shared by the routes: access tokens, known tickers, queueing a run."""
from __future__ import annotations

import secrets

from fastapi import HTTPException

from app.core import config
from app.services import tickers as ticker_data
from app.workers import jobs


def check_token(token: str, authorization: str | None, what: str) -> None:
    if token and not secrets.compare_digest(authorization or "", f"Bearer {token}"):
        raise HTTPException(401, f"{what} needs the server's access token (Settings page, or the Authorization header).")


def known(t: str) -> str:
    if not ticker_data.is_known(t):
        raise HTTPException(404, f"Unknown ticker {t}. Known: {', '.join(config.tickers())}")
    return t


def load_ticker(t: str) -> dict:
    return ticker_data.load(known(t))


def submit(*args, **kw):
    try:
        return jobs.submit(*args, **kw)
    except jobs.QueueFull as e:
        raise HTTPException(429, str(e))


def submitted(run: dict, reused: str | None) -> dict:
    return {"run": run, "reused": reused}
