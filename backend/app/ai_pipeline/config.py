"""Settings of the AI-analysis pipeline (the any_with_dse pipeline, run for any ticker without a notebook).

Paths follow the rest of the backend: the engine folder (override: RAEM_ENGINE) holds the trading logs and the memory
store. RAEM_DATA_DIR is NOT used here, it is the hosting volume (tools/persist.py)."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _env(name: str, default: str) -> str:
    val = os.environ.get(name)
    return val if val is not None and val.strip() != "" else default


BACKEND = Path(__file__).resolve().parents[2]
ENGINE = Path(os.environ.get("RAEM_ENGINE") or BACKEND / "engine")


def ensure_tradingagents_on_path() -> bool:
    """Make `import tradingagents` work (the vendored package sits in backend/tradingagents)."""
    for c in (os.environ.get("TRADINGAGENTS_PATH", ""), str(BACKEND)):
        if c and (Path(c) / "tradingagents").is_dir():
            if c not in sys.path:
                sys.path.insert(0, c)
            return True
    return False


OLLAMA_BASE_URL = _env("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_EMBED_URL = _env("OLLAMA_EMBED_URL", f"{OLLAMA_BASE_URL}/api/embeddings")
LLM_MODEL = _env("RAEM_LLM_MODEL", "qwen2.5:7b")
EMBED_MODEL = _env("RAEM_EMBED_MODEL", "nomic-embed-text")
LLM_TEMPERATURE = float(_env("RAEM_LLM_TEMPERATURE", "0"))

# Trading logs (trading_log_<TICKER>.json) live next to the memory store, where sessions.py reads them.
DATA_DIR = Path(_env("RAEM_LOG_DIR", str(ENGINE / "notebooks")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
CHROMA_DB_PATH = _env("RAEM_CHROMA_PATH", str(DATA_DIR / "raem_chroma_db"))   # the nomic-embed-text store
LOG_MAX_DAYS = int(_env("RAEM_LOG_MAX_DAYS", "0"))   # 0 = keep every session in the trading log; N>0 drops sessions older than N days


def log_file_for(company: str) -> Path:
    safe = "".join(ch for ch in company.upper() if ch.isalnum() or ch in ("-", "_"))
    return DATA_DIR / f"trading_log_{safe}.json"


DEFAULT_ASSET_TYPE = _env("RAEM_DEFAULT_ASSET_TYPE", "stock")
DEFAULT_INVESTMENT_ROUNDS = int(_env("RAEM_INVESTMENT_ROUNDS", "2"))
DEFAULT_RISK_ROUNDS = int(_env("RAEM_RISK_ROUNDS", "3"))
DEFAULT_INVESTOR_PROFILE = _env("RAEM_INVESTOR_PROFILE", "Aggressive")
MARKET_INDICATORS = ["rsi", "macd", "close_50_sma", "boll_ub", "boll_lb"]
DEMO_MODE = False   # the simulated run of the original app is not part of this site
