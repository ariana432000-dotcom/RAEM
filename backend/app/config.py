"""Settings read from the environment (see .env.example), plus the model lists the API offers."""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
WEB = BACKEND.parent
ENGINE = Path(os.environ.get("RAEM_ENGINE") or BACKEND / "engine")
STORAGE = Path(os.environ.get("RAEM_STORAGE") or BACKEND / "storage")
DATA = BACKEND / "data"
TICKERS_DIR = DATA / "tickers"
EXTRA = DATA / "extra.json"
INDEX = WEB / "frontend" / "dist" / "index.html"

try:  # a local run reads web/.env; Docker passes the same file with env_file. Variables already set win.
    from dotenv import load_dotenv
    load_dotenv(WEB / ".env", override=False)
except ImportError:
    pass
# TZ (e.g. Asia/Dhaka) decides "today" for runs and the notebooks' TRADE_DATE. Windows reads only POSIX-style TZ and would
# take "Asia/Dhaka" as UTC, so there the PC's own time zone is kept; elsewhere it applies now (children inherit it).
if os.name == "nt" and "/" in os.environ.get("TZ", ""):
    os.environ.pop("TZ")
elif os.environ.get("TZ"):
    import time as _time
    _time.tzset()

OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
DEFAULT_LLM = os.environ.get("RAEM_LLM_DEFAULT", "qwen2.5:7b")
RUN_TOKEN = os.environ.get("RAEM_RUN_TOKEN", "")          # protects POST /api/forecast and /api/analysis when set
REFRESH_TOKEN = os.environ.get("RAEM_REFRESH_TOKEN", "")  # turns POST /api/refresh on
NOTEBOOK_TIMEOUT = int(os.environ.get("RAEM_NOTEBOOK_TIMEOUT", str(6 * 3600)))  # a full run takes hours without a GPU
FORECAST_TIMEOUT = int(os.environ.get("RAEM_FORECAST_TIMEOUT", "1800"))
MAX_QUEUE = int(os.environ.get("RAEM_MAX_QUEUE", "12"))  # queued + running runs allowed at once

# forecast_lib model keys and their labels, best average rank first. DEFAULT_MODELS must match
# KEEP_MODELS in exporter/export_ticker.py (a test checks this).
FORECAST_MODELS = [
    ("nbeats", "N-BEATS"), ("lasso", "LASSO"), ("ridge", "Ridge"), ("random_forest", "RandomForest"),
    ("theta", "Theta"), ("arima", "ARIMA"), ("kalman", "Kalman"), ("arimax", "ARIMAX"), ("wsae_lstm", "WSAE-LSTM"),
    ("xgboost", "XGBoost"), ("lstm", "LSTM"), ("dnn", "DNN"), ("tft", "TFT"), ("regime", "RegimeSwitch"),
]
FORECAST_KEYS = [k for k, _ in FORECAST_MODELS]
DEFAULT_MODELS = ["nbeats", "lasso", "ridge", "random_forest", "theta", "arima"]


# Cloud LLM providers for AI analysis: (key, label, API-key variable, default models). A provider shows as usable when its
# API key is set; RAEM_<KEY>_MODELS (comma-separated) replaces the default model list. Use models that take a temperature
# and a plain max_tokens (not reasoning models): the notebooks pass both.
CLOUD_PROVIDERS = [
    ("openai", "OpenAI", "OPENAI_API_KEY", "gpt-4.1-mini,gpt-4o-mini,gpt-4.1"),
    ("anthropic", "Anthropic Claude", "ANTHROPIC_API_KEY", "claude-haiku-5-5,claude-sonnet-5-5"),
    ("google", "Google Gemini", "GOOGLE_API_KEY", "gemini-2.5-flash,gemini-2.5-flash-lite"),
    ("groq", "Groq", "GROQ_API_KEY", "llama-3.3-70b-versatile,llama-3.1-8b-instant"),
    ("kimi", "Kimi (Moonshot AI)", "MOONSHOT_API_KEY", "kimi-k2-turbo-preview,kimi-k2-0905-preview,moonshot-v1-32k"),
]
PROVIDER_KEYS = ["ollama"] + [p[0] for p in CLOUD_PROVIDERS]
KEY_ALIASES = {"MOONSHOT_API_KEY": "KIMI_API_KEY"}  # either name works


def api_key(env: str) -> str:
    return os.environ.get(env) or os.environ.get(KEY_ALIASES.get(env, ""), "")


def cloud_provider(key: str) -> dict | None:
    for k, label, env, default in CLOUD_PROVIDERS:
        if k == key:
            models = [m.strip() for m in os.environ.get(f"RAEM_{k.upper()}_MODELS", default).split(",") if m.strip()]
            return {"key": k, "label": label, "key_env": env, "ok": bool(api_key(env)), "models": models}
    return None


def llm_cache_path(ticker: str, model_key: str) -> Path:
    """Saved LLM answers of an unfinished AI analysis (held or failed); a new run of the same company and model reuses
    every answer whose prompt is unchanged, so it continues where the earlier run stopped. Deleted when a run finishes."""
    import re
    return STORAGE / "llm_cache" / f"{re.sub(r'[^A-Za-z0-9._-]', '_', ticker)}__{re.sub(r'[^A-Za-z0-9._-]', '_', model_key)}.json"


def llm_providers(max_age: float = 30.0) -> list[dict]:
    """Every LLM source for AI analysis: Ollama (its models) and each cloud provider (usable when its API key is set)."""
    o = llm_models(max_age)
    out = [{"key": "ollama", "label": "Ollama (this server)", "ok": o["ok"], "models": o["models"], "default": o["default"],
            "error": o.get("error")}]
    return out + [cloud_provider(k) for k, *_ in CLOUD_PROVIDERS]


def llm_key(provider: str, model: str) -> str:
    """The run key of an AI analysis: the bare model name for Ollama (as before), provider:model for the cloud."""
    return model if provider == "ollama" else f"{provider}:{model}"


def today() -> str:
    """The run date used for same-date reuse: the server's local date, like the notebooks' TRADE_DATE."""
    return date.today().isoformat()


def tickers() -> list[str]:
    return sorted(p.stem for p in TICKERS_DIR.glob("*.json"))


def notebooks() -> dict[str, str]:
    import sys
    sys.path.insert(0, str(BACKEND / "exporter"))
    from sessions import notebook_map
    return notebook_map(str(ENGINE / "notebooks"))


_llm_cache: dict = {"t": 0.0, "v": None}


def llm_models(max_age: float = 30.0) -> dict:
    """Models the Ollama server offers (embedding models left out), and whether the notebooks' embedding model is there.

    Cached for `max_age` seconds: Ollama answers slowly while it is generating, and every page load asks."""
    import time
    if _llm_cache["v"] is not None and time.time() - _llm_cache["t"] < max_age:
        return _llm_cache["v"]
    _llm_cache["v"] = _llm_models_now()
    _llm_cache["t"] = time.time()
    return _llm_cache["v"]


def _llm_models_now() -> dict:
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=3) as r:
            data = json.load(r)
        names = [m.get("name", "") for m in data.get("models", [])]
        chat = [n for n in names if n and "embed" not in n]
        return {"ok": True, "url": OLLAMA_URL, "models": chat,
                "default": DEFAULT_LLM if DEFAULT_LLM in chat else (chat[0] if chat else DEFAULT_LLM),
                "embeddings_ok": any(n.startswith("nomic-embed-text") for n in names)}
    except Exception as e:
        return {"ok": False, "url": OLLAMA_URL, "models": [], "default": DEFAULT_LLM, "embeddings_ok": False,
                "error": f"Ollama is not reachable at {OLLAMA_URL} ({type(e).__name__})."}
