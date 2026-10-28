"""Settings read from the environment (see .env.example), plus the model lists the API offers."""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]   # backend/
WEB = BACKEND.parent
ENGINE = Path(os.environ.get("RAEM_ENGINE") or BACKEND / "engine")
STORAGE = Path(os.environ.get("RAEM_STORAGE") or BACKEND / "storage")
DATA = BACKEND / "data"
TICKERS_DIR = DATA / "tickers"
EXTRA = DATA / "extra.json"
COMPANIES = DATA / "companies.json"   # companies added from the website (see engine/raem_companies.py)
INDEX = WEB / "frontend" / "dist" / "index.html"
PIPELINE = BACKEND / "app" / "pipeline"      # forecast export scripts + sessions.py (run as scripts, imported flat)
AI_ENGINE = BACKEND / "app" / "ai_engine"    # the AI-analysis runner (run_pipeline.py)

try:  # a local run reads web/.env; Docker passes the same file with env_file. Variables already set win.
    from dotenv import load_dotenv
    load_dotenv(WEB / ".env", override=False)
except ImportError:
    pass
# TZ (e.g. Asia/Dhaka) decides "today" for runs and the pipeline's trade date. Windows reads only POSIX-style TZ and would
# take "Asia/Dhaka" as UTC, so there the PC's own time zone is kept; elsewhere it applies now (children inherit it).
if os.name == "nt" and "/" in os.environ.get("TZ", ""):
    os.environ.pop("TZ")
elif os.environ.get("TZ"):
    import time as _time
    _time.tzset()

# the engine (predictor_lib) and the pipeline scripts read the added companies from the same file
os.environ.setdefault("RAEM_COMPANIES", str(COMPANIES))

OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
DEFAULT_LLM = os.environ.get("RAEM_LLM_DEFAULT", "qwen2.5:7b")
RUN_TOKEN = os.environ.get("RAEM_RUN_TOKEN", "")          # protects POST /api/forecast and /api/analysis when set
REFRESH_TOKEN = os.environ.get("RAEM_REFRESH_TOKEN", "")  # turns POST /api/refresh on
ANALYSIS_TIMEOUT = int(os.environ.get("RAEM_ANALYSIS_TIMEOUT") or os.environ.get("RAEM_NOTEBOOK_TIMEOUT") or 6 * 3600)  # a full run takes hours without a GPU
FORECAST_TIMEOUT = int(os.environ.get("RAEM_FORECAST_TIMEOUT", "1800"))
COMPANY_TIMEOUT = int(os.environ.get("RAEM_COMPANY_TIMEOUT", "3600"))  # adding a company: prices, notebook, forecast, page data
MAX_QUEUE = int(os.environ.get("RAEM_MAX_QUEUE", "12"))  # queued + running runs allowed at once

# The forecast models a run may use: the best six by average rank (forecast_lib has 14; the other eight -- Kalman, ARIMAX,
# WSAE-LSTM, XGBoost, LSTM, DNN, TFT, RegimeSwitch -- ranked lower and the deep ones took most of a run's time and memory on
# a hosted server). DEFAULT_MODELS must match KEEP_MODELS in app/pipeline/export_ticker.py (a test checks this).
FORECAST_MODELS = [
    ("nbeats", "N-BEATS"), ("lasso", "LASSO"), ("ridge", "Ridge"), ("random_forest", "RandomForest"),
    ("theta", "Theta"), ("arima", "ARIMA"),
]
FORECAST_KEYS = [k for k, _ in FORECAST_MODELS]
DEFAULT_MODELS = list(FORECAST_KEYS)


# Cloud LLM providers for AI analysis: (key, label, API-key variable, default models). A provider shows as usable when its
# API key is set; RAEM_<KEY>_MODELS (comma-separated) replaces the default model list. Use models that take a temperature
# (reasoning models such as gpt-5 and o-series are handled, but are slower and dearer).
CLOUD_PROVIDERS = [
    ("openai", "OpenAI", "OPENAI_API_KEY", "gpt-4.1-mini,gpt-4o-mini,gpt-4.1"),
    ("anthropic", "Anthropic Claude", "ANTHROPIC_API_KEY", "claude-haiku-5-5,claude-sonnet-5-5"),
    ("google", "Google Gemini", "GOOGLE_API_KEY", "gemini-2.5-flash,gemini-2.5-flash-lite"),
    ("groq", "Groq", "GROQ_API_KEY", "llama-3.3-70b-versatile,llama-3.1-8b-instant"),
    ("kimi", "Kimi (Moonshot AI)", "MOONSHOT_API_KEY", "kimi-k3.1-pro,kimi-k3,kimi-k2.6"),
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


RUN_THREADS = 2  # the models are small: a six-model forecast took 18 s on 1 thread, 20 s on 2 or 4, 66 s on 12


def cpu_limit() -> int:
    """Threads for one model run. A hosted container (Railway) sees every core of the host machine, so numpy, torch and
    scikit-learn would start a thread per core; that made a run slower, and on a usage-billed plan (Railway Hobby bills
    CPU by the second) dearer. RAEM_THREADS overrides; else RUN_THREADS, or fewer when the cgroup CPU quota or the
    CPUs the process may run on are fewer."""
    if os.environ.get("RAEM_THREADS", "").isdigit() and int(os.environ["RAEM_THREADS"]) > 0:
        return int(os.environ["RAEM_THREADS"])
    return min(RUN_THREADS, _cpus())


def _cpus() -> int:
    try:  # cgroup v2: "<quota> <period>" or "max <period>"
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()[:2]
        if quota != "max":
            return max(1, int(int(quota) / int(period)))
    except (OSError, ValueError):
        pass
    try:  # cgroup v1
        quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
        if quota > 0:
            return max(1, int(quota / int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())))
    except (OSError, ValueError):
        pass
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        return os.cpu_count() or 1


def thread_env() -> dict:
    """Environment for a model run (forecast, notebook, add company): its numeric libraries use cpu_limit() threads."""
    n = str(cpu_limit())
    return {k: os.environ.get(k) or n for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                                                "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "LOKY_MAX_CPU_COUNT")}


def today() -> str:
    """The run date used for same-date reuse: the server's local date, like the pipeline's trade date."""
    return date.today().isoformat()


def tickers() -> list[str]:
    return sorted(p.stem for p in TICKERS_DIR.glob("*.json"))


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
