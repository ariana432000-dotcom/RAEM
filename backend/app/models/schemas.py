"""Request bodies of the API."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.core import config


class ForecastRequest(BaseModel):
    ticker: str
    models: list[str] = Field(default_factory=lambda: list(config.DEFAULT_MODELS), description="forecast_lib model keys")
    force: bool = Field(False, description="Run again even if a run with the same date and models is stored")


class AnalysisRequest(BaseModel):
    ticker: str
    llm_provider: str = Field("ollama", description="ollama, openai, anthropic, google or groq")
    llm_model: str | None = Field(None, description="Model of that provider; the provider's default when empty")
    force: bool = False
    market: str = Field("auto", description="auto, international (Yahoo Finance) or dse (used when a company is added)")
    name: str | None = Field(None, description="The company's name (used when it is added)")
