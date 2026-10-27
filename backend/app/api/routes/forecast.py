from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from app.api import deps
from app.core import config
from app.models.schemas import ForecastRequest

router = APIRouter()


@router.post("/api/forecast", status_code=202)
def run_forecast(req: ForecastRequest, authorization: str | None = Header(None)):
    """Forecast one company from its CSV price data with the chosen models (blends are added automatically)."""
    deps.check_token(config.RUN_TOKEN, authorization, "Running a forecast")
    t = deps.known(req.ticker)
    unknown = sorted(set(req.models) - set(config.FORECAST_KEYS))
    if unknown or not req.models:
        what = f"Unknown model: {', '.join(unknown)}. " if unknown else ""
        raise HTTPException(400, f"{what}Choose at least one of: {', '.join(config.FORECAST_KEYS)}")
    models = sorted(set(req.models), key=config.FORECAST_KEYS.index)
    run, reused = deps.submit("forecast", t, ",".join(models), {"models": models}, force=req.force)
    return deps.submitted(run, reused)
