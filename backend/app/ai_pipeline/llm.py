"""LLM factory of the AI-analysis pipeline.

The provider and model come from the run (RAEM_LLM_PROVIDER, RAEM_LLM_MODEL): ollama, or a cloud provider listed in
app.core.config.CLOUD_PROVIDERS (openai, anthropic, google, groq, kimi). Every agent calls invoke_llm_with_retry().

Hold and resume: with RAEM_LLM_CACHE set, every model answer is saved the moment it arrives, under a hash of provider, model,
temperature, stop words, call options and the messages. A later run that sends the identical prompt gets the saved answer at
once, so it continues where a held or failed run stopped; a step whose inputs changed asks the model again.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time

from . import config

REASONING_MODEL = re.compile(r"^(o\d|gpt-5)", re.I)   # these OpenAI models take no temperature
BILLING = re.compile(r"credit balance|insufficient[_ ]quota|billing|payment required|exceeded your current quota|recharge", re.I)

_STATS = {"entries": {}, "reused": 0, "new": 0}
_PATCHED: set = set()


def _provider(override: str | None = None) -> str:
    p = (override or os.environ.get("RAEM_LLM_PROVIDER", "")).strip().lower()
    return p or "ollama"


def _model(provider: str) -> str:
    default = {"anthropic": "claude-sonnet-5-5", "kimi": "kimi-k3", "openai": "gpt-4.1-mini",
               "groq": "llama-3.3-70b-versatile", "google": "gemini-2.5-flash", "ollama": config.LLM_MODEL}.get(provider, config.LLM_MODEL)
    return os.environ.get("RAEM_LLM_MODEL") or default


def _key(*names: str) -> str | None:
    for n in names:
        if os.environ.get(n):
            return os.environ[n]
    return None


def make_llm(temperature=None, provider_override: str | None = None):
    """A chat model for the run's provider. Raises ValueError for an unknown provider or a missing API key."""
    provider, model = _provider(provider_override), _model(_provider(provider_override))
    temp = config.LLM_TEMPERATURE if temperature is None else temperature
    env = os.environ
    if provider == "ollama":
        from langchain_ollama import ChatOllama
        llm = ChatOllama(model=model, base_url=config.OLLAMA_BASE_URL, temperature=temp, num_predict=2048, repeat_penalty=1.15)
    elif provider in ("openai", "groq", "kimi"):
        from langchain_openai import ChatOpenAI
        if provider == "groq":
            key, base = _key("GROQ_API_KEY"), "https://api.groq.com/openai/v1"
        elif provider == "kimi":   # Moonshot's API speaks the OpenAI protocol; its newer models accept only temperature 1
            key, base = _key("MOONSHOT_API_KEY", "KIMI_API_KEY"), env.get("RAEM_KIMI_BASE_URL") or "https://api.moonshot.ai/v1"
        else:
            key, base = _key("OPENAI_API_KEY"), env.get("RAEM_OPENAI_BASE_URL") or env.get("OPENAI_BASE_URL") or None
        if not key:
            raise ValueError(f"No API key for {provider}: set it in the server's environment")
        kw = {} if (provider == "openai" and REASONING_MODEL.match(model)) else {"temperature": 1 if provider == "kimi" else temp}
        llm = ChatOpenAI(model=model, api_key=key, base_url=base, timeout=600, max_retries=4, **kw)
    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        key = _key("ANTHROPIC_API_KEY")
        if not key:
            raise ValueError("No API key for anthropic: set ANTHROPIC_API_KEY in the server's environment")
        llm = ChatAnthropic(model=model, api_key=key, max_tokens=4096, timeout=600, max_retries=4)   # no temperature: newer Claude models refuse it
    elif provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        key = _key("GOOGLE_API_KEY")
        if not key:
            raise ValueError("No API key for google: set GOOGLE_API_KEY in the server's environment")
        llm = ChatGoogleGenerativeAI(model=model, google_api_key=key, temperature=temp, max_retries=4)
    else:
        raise ValueError(f"Unknown LLM provider {provider!r}")
    cache = os.environ.get("RAEM_LLM_CACHE")
    if cache:
        enable_resume(type(llm), cache, provider, model)
    return llm


def provider_label(override: str | None = None) -> str:
    p = _provider(override)
    return f"{p}:{_model(p)}"


# ---- hold and resume -------------------------------------------------------------------------------------------------
def saved_counts() -> dict:
    return {"entries": len(_STATS["entries"]), "reused": _STATS["reused"], "new": _STATS["new"]}


def _save(path: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_STATS, f, ensure_ascii=False)
    os.replace(tmp, path)


def load_saved(path: str) -> int:
    """Read the answers of an earlier unfinished run. Returns how many there are."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        _STATS["entries"] = d.get("entries", {})
    except Exception:
        _STATS["entries"] = {}
    _STATS["reused"] = _STATS["new"] = 0
    return len(_STATS["entries"])


def enable_resume(cls, path: str, provider: str, model: str) -> None:
    """Wrap cls._generate once: answer from the saved file when the prompt is identical, else ask and save the answer."""
    if cls in _PATCHED:
        return
    _PATCHED.add(cls)
    original = cls._generate

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        from langchain_core.messages import message_to_dict, messages_from_dict
        from langchain_core.outputs import ChatGeneration, ChatResult
        key = hashlib.sha256(json.dumps([provider, model, getattr(self, "temperature", None), stop, sorted(kwargs.items(), key=lambda kv: kv[0]),
                                         [(m.type, str(m.content)) for m in messages]], ensure_ascii=False, default=str).encode()).hexdigest()
        hit = _STATS["entries"].get(key)
        if hit is not None:
            _STATS["reused"] += 1
            _save(path)
            return ChatResult(generations=[ChatGeneration(message=m) for m in messages_from_dict(hit)])
        result = original(self, messages, stop=stop, run_manager=run_manager, **kwargs)
        _STATS["new"] += 1
        _STATS["entries"][key] = [message_to_dict(g.message) for g in result.generations]
        _save(path)
        return result

    cls._generate = _generate


# ---- calls -----------------------------------------------------------------------------------------------------------
def _normalize_content(content):
    """Newer Claude models return .content as a list of blocks (text, thinking); the agents expect a string."""
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return content


def invoke_llm_with_retry(llm, prompt_or_messages, max_attempts: int = 3):
    """llm.invoke() with a back-off on rate limits. A billing error ("credit balance is too low", "insufficient_quota") is
    not a rate limit: waiting does not help, so it is raised at once with the provider's own message."""
    last_err = None
    for attempt in range(max_attempts):
        try:
            response = llm.invoke(prompt_or_messages)
            response.content = _normalize_content(response.content)
            return response
        except Exception as e:  # noqa: BLE001
            last_err = e
            text = str(e)
            if BILLING.search(text):
                raise
            if "429" in text or "rate_limit" in text.lower():
                time.sleep(60 * (attempt + 1))
            else:
                raise
    raise RuntimeError(f"{max_attempts} attempts hit the provider's rate limit, last error: {last_err}")
