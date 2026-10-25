"""Run one company's notebook headlessly, the same as opening it in Jupyter and pressing Run All,
with a chosen LLM (Ollama or a cloud provider). Then save the new session in the website's AI-analysis format.

    python exporter/run_notebook.py --ticker AAPL --model qwen2.5:7b --out result.json
    python exporter/run_notebook.py --ticker AAPL --provider openai --model gpt-4.1-mini --out result.json

The notebook file itself is never changed. The run works on an in-memory copy where:
  * a first cell (CONFIG_CELL) holds the provider, model, Ollama address and memory settings for this run,
  * every `model="qwen2.5:7b"` becomes that model and every "http://localhost:11434" that address,
  * with a cloud provider every `ChatOllama(` becomes `RAEMCloudChat(`: a ChatOllama subclass whose text comes from
    the cloud model, so the Fact Guard (it patches ChatOllama.invoke) and the analyst cache keep working,
  * the memory's embedding function and store path go through RAEM_embedding_function / RAEM_CHROMA_PATH: the
    notebooks' nomic-embed-text store when Ollama has that model, else chromadb's built-in model in its own store,
  * `%pip install` lines are skipped (install packages from backend/requirements.txt instead).
Everything else (analysts, debate, Graph Analyst, Forecasting Agent, Prediction Engine, Verifier,
Position Sizing, scenario, memory, trading log) runs exactly as written in the notebook.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
ENGINE = Path(os.environ.get("RAEM_ENGINE") or BACKEND / "engine")
NB_DIR = ENGINE / "notebooks"
RUNS_DIR = Path(os.environ.get("RAEM_STORAGE") or BACKEND / "storage") / "notebook_runs"
CELL_TIMEOUT = int(os.environ.get("RAEM_CELL_TIMEOUT", "7200"))  # one debate cell can take an hour on a CPU-only machine
EMBED_DOWNLOAD_TIMEOUT = int(os.environ.get("RAEM_EMBED_DOWNLOAD_TIMEOUT", "900"))  # chromadb's embedding model, first use only

sys.path.insert(0, str(HERE))
from sessions import ai_entry, archive_logs, debate_turns, notebook_map, trunc  # noqa: E402

CONFIG_CELL = '''# (added by the web runner) LLM, provider and memory settings for this run
import os
RAEM_LLM_PROVIDER = os.environ.get("RAEM_LLM_PROVIDER", "ollama")
RAEM_LLM_MODEL = os.environ.get("RAEM_LLM_MODEL", "qwen2.5:7b")
RAEM_OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
# Memory embeddings: "ollama" = nomic-embed-text, the notebooks' own store; "local" = chromadb's built-in model, used when
# Ollama's nomic-embed-text is not reachable; "hash" = word hashing (no model), when that model could not be downloaded either.
# Vectors of different models cannot share a collection, so each mode has its own folder.
RAEM_EMBED = os.environ.get("RAEM_EMBED", "ollama")
RAEM_CHROMA_PATH = {"ollama": "./raem_chroma_db", "local": "./raem_chroma_db_local"}.get(RAEM_EMBED, "./raem_chroma_db_hash")

from chromadb.api.types import Documents as _RaemDocs, EmbeddingFunction as _RaemEF


class RAEMHashEmbedding(_RaemEF[_RaemDocs]):
    """Signed feature hashing of words: no model and no download, so memory still works (by shared words) offline."""

    def __init__(self, dim=384):
        self.dim = dim

    def __call__(self, input):
        import hashlib, re
        import numpy as np
        out = []
        for doc in input:
            v = np.zeros(self.dim, dtype=np.float32)
            for w in re.findall(r"[a-z0-9]+", str(doc).lower()):
                h = int(hashlib.md5(w.encode()).hexdigest()[:8], 16)
                v[h % self.dim] += 1.0 if h & 0x80000000 else -1.0
            n = float(np.linalg.norm(v))
            out.append(v / n if n else v)
        return out

    @staticmethod
    def name():
        return "raem-hash"

    def get_config(self):
        return {"dim": self.dim}

    @staticmethod
    def build_from_config(config):
        return RAEMHashEmbedding(config.get("dim", 384))


def RAEM_embedding_function(url, model_name):
    from chromadb.utils import embedding_functions as _ef
    if RAEM_EMBED == "ollama":
        return _ef.OllamaEmbeddingFunction(url=url, model_name=model_name)
    return _ef.DefaultEmbeddingFunction() if RAEM_EMBED == "local" else RAEMHashEmbedding()


def _raem_copy_memory():
    """Without Ollama's nomic-embed-text the notebooks' memory (./raem_chroma_db: past episodes, news) cannot be
    searched. Copy what this run's store lacks, embedded again with this run's model, so past memory still counts.
    The notebooks' own store keeps its contents (chroma only updates its bookkeeping when it opens a store)."""
    if RAEM_EMBED == "ollama" or not os.path.isdir("./raem_chroma_db"):
        return
    try:
        import chromadb
        src, dst = chromadb.PersistentClient(path="./raem_chroma_db"), chromadb.PersistentClient(path=RAEM_CHROMA_PATH)
        ef, added = RAEM_embedding_function(url="", model_name="nomic-embed-text"), 0
        for col in src.list_collections():
            s = src.get_collection(getattr(col, "name", col))
            got = s.get(include=["documents", "metadatas"])
            d = dst.get_or_create_collection(name=s.name, embedding_function=ef, metadata=s.metadata or None)
            have = set(d.get(include=[])["ids"])
            rows = [r for r in zip(got["ids"], got["documents"], got["metadatas"]) if r[0] not in have and r[1]]
            for k in range(0, len(rows), 100):
                part = rows[k:k + 100]
                d.add(ids=[r[0] for r in part], documents=[r[1] for r in part], metadatas=[r[2] or None for r in part])
            added += len(rows)
        if added:
            print(f"Memory: {added} past episodes and news chunks copied from ./raem_chroma_db into {RAEM_CHROMA_PATH}")
    except Exception as e:  # a run without past memory still works
        print("Memory copy skipped:", str(e)[:200])


_raem_copy_memory()


from langchain_ollama import ChatOllama as _RaemChatOllama
_RAEM_INNER = {}


def _raem_cloud_model(temperature, max_tokens):
    p, m, env = RAEM_LLM_PROVIDER, RAEM_LLM_MODEL, os.environ
    kw = {} if temperature is None else {"temperature": temperature}
    if p in ("openai", "groq"):
        from langchain_openai import ChatOpenAI
        base = "https://api.groq.com/openai/v1" if p == "groq" else (env.get("RAEM_OPENAI_BASE_URL") or None)
        return ChatOpenAI(model=m, api_key=env.get("GROQ_API_KEY" if p == "groq" else "OPENAI_API_KEY"), base_url=base,
                          max_tokens=max_tokens, timeout=600, max_retries=4, **kw)
    if p == "kimi":  # Moonshot's API speaks the OpenAI protocol
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=m, api_key=env.get("MOONSHOT_API_KEY") or env.get("KIMI_API_KEY"),
                          base_url=env.get("RAEM_KIMI_BASE_URL") or "https://api.moonshot.ai/v1",
                          max_tokens=max_tokens, timeout=600, max_retries=4, **kw)
    if p == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=m, api_key=env.get("ANTHROPIC_API_KEY"), max_tokens=max_tokens or 2048,
                             timeout=600, max_retries=4, **kw)
    if p == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        # no output cap: Gemini 2.5 counts its thinking against it, and a cloud model does not run away like a local 7B
        return ChatGoogleGenerativeAI(model=m, google_api_key=env.get("GOOGLE_API_KEY"), max_retries=4, **kw)
    raise ValueError(f"Unknown LLM provider {p!r}")


class RAEMCloudChat(_RaemChatOllama):
    """A ChatOllama on the outside, so every notebook cell, the Fact Guard (it patches ChatOllama.invoke) and the
    analyst cache work unchanged; the text comes from the chosen cloud model."""

    def _raem_inner(self, plain=False):
        key = (self.temperature, self.num_predict, plain)
        if key not in _RAEM_INNER:
            _RAEM_INNER[key] = _raem_cloud_model(None if plain else self.temperature, self.num_predict)
        return _RAEM_INNER[key]

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        try:
            return self._raem_inner()._generate(messages, stop=stop, **kwargs)
        except Exception as e:
            if "temperature" not in str(e).lower():
                raise
            return self._raem_inner(plain=True)._generate(messages, stop=stop, **kwargs)  # a model that takes no temperature

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        yield from self._raem_inner()._stream(messages, stop=stop, **kwargs)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return await self._raem_inner()._agenerate(messages, stop=stop, **kwargs)


# Hold and resume: every LLM answer of this company and model is saved the moment it arrives. A later run that sends the
# identical prompt (same prices, news and earlier answers) gets the saved answer at once, so it continues where the held
# run stopped; a step whose inputs changed asks the model again. The server deletes the file when a run finishes.
import hashlib as _raem_hash, json as _raem_json
RAEM_LLM_CACHE = os.environ.get("RAEM_LLM_CACHE", "")
_RAEM_SAVED = {"entries": {}, "reused": 0, "new": 0}
if RAEM_LLM_CACHE and os.path.exists(RAEM_LLM_CACHE):
    try:
        with open(RAEM_LLM_CACHE, encoding="utf-8") as _f:
            _RAEM_SAVED["entries"] = _raem_json.load(_f).get("entries", {})
    except Exception:
        pass


def _raem_save():
    tmp = RAEM_LLM_CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        _raem_json.dump(_RAEM_SAVED, f, ensure_ascii=False)
    os.replace(tmp, RAEM_LLM_CACHE)


def _raem_resumable(generate):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatGeneration, ChatResult
        key = _raem_hash.sha256(_raem_json.dumps([RAEM_LLM_PROVIDER, RAEM_LLM_MODEL, self.temperature, self.num_predict, stop,
                                                  [(m.type, str(m.content)) for m in messages]], ensure_ascii=False).encode()).hexdigest()
        if key in _RAEM_SAVED["entries"]:
            _RAEM_SAVED["reused"] += 1
            _raem_save()
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=_RAEM_SAVED["entries"][key]))])
        result = generate(self, messages, stop=stop, run_manager=run_manager, **kwargs)
        _RAEM_SAVED["new"] += 1
        _RAEM_SAVED["entries"][key] = str(result.generations[0].message.content)
        _raem_save()
        return result
    return _generate


if RAEM_LLM_CACHE:
    _RaemChatOllama._generate = _raem_resumable(_RaemChatOllama._generate)
    RAEMCloudChat._generate = _raem_resumable(RAEMCloudChat._generate)

print("Web runner:", RAEM_LLM_PROVIDER, RAEM_LLM_MODEL, "| memory embeddings:", RAEM_EMBED, "| Ollama:", RAEM_OLLAMA_URL,
      "| saved answers:", len(_RAEM_SAVED["entries"]))
'''


CAPTURE_CELL = '''# (added by the web runner) the full debates for the website; the trading log keeps only excerpts
import json as _raem_j, os as _raem_o


def _raem_pick(name, keys):
    v = globals().get(name)
    return {k: v.get(k) for k in keys} if isinstance(v, dict) else None


_raem_trader = globals().get("trader_result")
_raem_full = {
    "debate": _raem_pick("final_debate_state", ("history", "bull_history", "bear_history", "count")),
    "risk": _raem_pick("final_risk_debate_state", ("history", "aggressive_history", "conservative_history", "neutral_history", "count")),
    "facilitator": globals().get("investment_facilitator_decision"),
    "risk_facilitator": globals().get("risk_facilitator_decision"),
    "trader": _raem_trader.get("trader_investment_plan") if isinstance(_raem_trader, dict) else None,
}
if _raem_o.environ.get("RAEM_FULL_OUT"):
    with open(_raem_o.environ["RAEM_FULL_OUT"], "w", encoding="utf-8") as _raem_f:
        _raem_j.dump(_raem_full, _raem_f, ensure_ascii=False, default=str)
'''


def full_debate(path: str) -> dict | None:
    """The capture cell's file as the website's debate block (complete transcripts)."""
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    deb, risk = d.get("debate") or {}, d.get("risk") or {}
    turns = debate_turns(deb.get("history"))
    risk_turns = debate_turns(risk.get("history"))
    if not risk_turns:  # no speaker labels in the history: one turn per analyst
        risk_turns = [{"speaker": f"{k.title()} Analyst", "side": k, "round": None, "text": trunc(risk.get(f"{k}_history"), 4000)}
                      for k in ("aggressive", "conservative", "neutral") if str(risk.get(f"{k}_history") or "").strip()]
    if not turns and not risk_turns:
        return None
    return {"turns": turns, "facilitator": trunc(d.get("facilitator"), 6000), "risk_turns": risk_turns,
            "risk_facilitator": trunc(d.get("risk_facilitator"), 6000), "trader": trunc(d.get("trader"), 4000), "complete": True}


def rewrite(src: str, cloud: bool = False) -> tuple[str, int]:
    n = 0
    lines = []
    for line in src.split("\n"):
        s = line.lstrip()
        if s.startswith(("%pip", "!pip", "%conda", "!conda")):
            lines.append(line[: len(line) - len(s)] + "pass  # (web runner) package install skipped")
            n += 1
        else:
            lines.append(line)
    out = "\n".join(lines)
    for q in ('"', "'"):
        k = out.count(f"model={q}qwen2.5:7b{q}")
        out = out.replace(f"model={q}qwen2.5:7b{q}", "model=RAEM_LLM_MODEL")
        k2 = out.count(f"{q}http://localhost:11434")
        out = out.replace(f"{q}http://localhost:11434", f"RAEM_OLLAMA_URL + {q}")
        n += k + k2
    subs = [(r"embedding_functions\.OllamaEmbeddingFunction\(", "RAEM_embedding_function("),
            (r"CHROMA_DB_PATH\s*=\s*[\"']\./raem_chroma_db[\"']", "CHROMA_DB_PATH = RAEM_CHROMA_PATH")]
    if cloud:  # constructors only: `ChatOllama.invoke` (the Fact Guard's patch) and the import stay as they are
        subs.append((r"\bChatOllama\(", "RAEMCloudChat("))
    for pat, rep in subs:
        out, k = re.subn(pat, rep, out)
        n += k
    return out, n


def embed_mode(ollama_url: str) -> str:
    """'ollama' when the Ollama server has nomic-embed-text (the notebooks' memory store), else 'local' when chromadb's
    built-in model is on disk or downloads in time, else 'hash'. RAEM_EMBED forces a mode ('local' still needs the model)."""
    forced = os.environ.get("RAEM_EMBED")
    if forced in ("ollama", "hash"):
        return forced
    if forced != "local":
        try:
            import urllib.request
            with urllib.request.urlopen(ollama_url.rstrip("/") + "/api/tags", timeout=5) as r:
                names = [m.get("name", "") for m in json.load(r).get("models", [])]
            if any(n.startswith("nomic-embed-text") for n in names):
                return "ollama"
        except Exception:
            pass
    return "local" if local_model_ready() else "hash"


def local_model_ready(timeout: int = EMBED_DOWNLOAD_TIMEOUT) -> bool:
    """chromadb's built-in embedding model (all-MiniLM-L6-v2, about 80 MB) on disk, downloading it now if needed. In a child
    process with a time limit, so a crawling download cannot stall the run (a half-done file fails its checksum and is fetched again)."""
    code = ("from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2 as M; "
            "M()._download_model_if_not_exists()")
    print("== Memory embeddings: checking chromadb's built-in model (a one-time download of about 80 MB)", flush=True)
    try:
        return subprocess.run([sys.executable, "-c", code], timeout=timeout, capture_output=True).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def saved_counts(path: str) -> dict:
    """Saved LLM answers in the resume file, and how many this run reused / added (written by CONFIG_CELL)."""
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return {"entries": len(d.get("entries", {})), "reused": d.get("reused", 0), "new": d.get("new", 0)}
    except Exception:
        return {"entries": 0, "reused": 0, "new": 0}


def labels(nb) -> dict[int, str]:
    """A readable name per code cell: the step it logs (log_step), else the markdown heading above it, else its first comment."""
    out, heading = {}, ""
    for i, c in enumerate(nb.cells):
        src = c.source if isinstance(c.source, str) else "".join(c.source)
        if c.cell_type == "markdown":
            for line in src.splitlines():
                if line.strip().startswith("#"):
                    heading = line.strip().lstrip("#").strip()
                    break
            continue
        step = re.search(r"log_step\(\s*[\"']([^\"']+)[\"']", src)
        made = re.findall(r"^def create_(\w+)", src, re.M)
        name = ""
        for line in src.splitlines():
            t = line.strip().lstrip("#").strip(" =-")
            if line.strip().startswith("#") and len(t) > 3 and not t.startswith(("✅", "✅", "FIXED", "NEW")):
                name = t
                break
        if made and not step:
            name = "Set up " + ", ".join(m.replace("_", " ") for m in made)
        out[i] = ((step.group(1) if step else "") or heading or name or "Setup")[:90]
        heading = ""
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--provider", default="ollama", choices=["ollama", "openai", "anthropic", "google", "groq", "kimi"])
    ap.add_argument("--model", default=os.environ.get("RAEM_LLM_DEFAULT", "qwen2.5:7b"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    import nbformat
    from nbclient import NotebookClient
    from nbclient.exceptions import CellExecutionError

    if sys.platform == "win32":  # pyzmq needs the selector event loop on Windows
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    nb_name = notebook_map(str(NB_DIR)).get(a.ticker)
    if not nb_name:
        print(f"!! No notebook for {a.ticker} in {NB_DIR}", flush=True)
        return 2
    nb = nbformat.read(str(NB_DIR / nb_name), as_version=4)
    cloud = a.provider != "ollama"
    llm_key = a.model if not cloud else f"{a.provider}:{a.model}"
    os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")
    embed = embed_mode(os.environ["OLLAMA_BASE_URL"])
    changed = 0
    for c in nb.cells:
        if c.cell_type == "code":
            c.source, k = rewrite(c.source if isinstance(c.source, str) else "".join(c.source), cloud=cloud)
            changed += k
    nb.cells.insert(0, nbformat.v4.new_code_cell(CONFIG_CELL))
    nb.cells.append(nbformat.v4.new_code_cell(CAPTURE_CELL))
    names = labels(nb)
    code_idx = [i for i, c in enumerate(nb.cells) if c.cell_type == "code"]
    print(f"== Prepared {nb_name}: {len(code_idx)} code cells, {changed} lines adapted, model {llm_key}", flush=True)
    print("== Memory embeddings: " + {"ollama": "nomic-embed-text on Ollama (the notebooks' store)",
                                      "local": "chromadb's built-in model (nomic-embed-text is not on Ollama), separate store raem_chroma_db_local",
                                      "hash": "word hashing (no embedding model could be reached or downloaded), separate store raem_chroma_db_hash"}[embed],
          flush=True)

    os.environ["RAEM_LLM_PROVIDER"] = a.provider
    os.environ["RAEM_LLM_MODEL"] = a.model
    os.environ["RAEM_EMBED"] = embed
    full_out = os.path.join(tempfile.gettempdir(), f"raem_full_{os.getpid()}.json")
    os.environ["RAEM_FULL_OUT"] = full_out
    os.environ.setdefault("TRADINGAGENTS_PATH", str(ENGINE / "TradingAgents-main"))
    os.environ["MPLBACKEND"] = "Agg"
    os.environ["PYTHONIOENCODING"] = "utf-8"

    client = NotebookClient(nb, timeout=CELL_TIMEOUT, kernel_name="python3", allow_errors=False,
                            resources={"metadata": {"path": str(NB_DIR)}})

    def on_start(cell, cell_index):
        if cell_index in code_idx:
            print(f"== [{code_idx.index(cell_index) + 1}/{len(code_idx)}] {names.get(cell_index, 'Cell')}", flush=True)

    client.on_cell_start = on_start
    kept = archive_logs(str(NB_DIR))  # the notebook prunes its log to 10 days; keep every session first
    print(f"== Archived {kept} logged sessions before the run", flush=True)
    cache = os.environ.get("RAEM_LLM_CACHE", "")
    saved_before = saved_counts(cache)["entries"]
    if saved_before:
        print(f"== Resuming: {saved_before} saved LLM answers from the earlier run are reused while their prompts are unchanged", flush=True)
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    t0 = time.time()
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    saved = RUNS_DIR / f"{a.ticker.replace('.', '_')}_{started[:10]}_{re.sub(r'[^A-Za-z0-9._-]', '_', llm_key)}.ipynb"
    try:
        client.execute()
    except CellExecutionError as e:
        nbformat.write(nb, str(saved))
        msg = str(e).strip().splitlines()
        print("!! A notebook cell failed:", flush=True)
        for line in msg[-12:]:
            print("   " + line, flush=True)
        print(f"!! Executed copy saved: {saved}", flush=True)
        return 1
    except Exception as e:
        nbformat.write(nb, str(saved))
        print(f"!! {type(e).__name__}: {e}", flush=True)
        print(f"!! Executed copy saved: {saved}", flush=True)
        return 1
    nbformat.write(nb, str(saved))
    archive_logs(str(NB_DIR))

    entry = ai_entry(str(NB_DIR), a.ticker, since=started[:16])
    full = full_debate(full_out)
    if entry is not None and full:
        entry["latest"]["debate"] = full
    try:
        os.remove(full_out)
    except OSError:
        pass
    if entry is None:
        print("!! The notebook finished but wrote no new trading-log session for this company.", flush=True)
        return 1
    entry["latest"].update(llm_model=llm_key, llm_provider=a.provider, memory_embeddings=embed)
    used = saved_counts(cache)
    if saved_before:
        print(f"== Reused {used['reused']} saved answers, made {used['new']} new LLM calls", flush=True)
    out = {"ticker": a.ticker, "llm_model": llm_key, "llm_provider": a.provider, "notebook": nb_name, "started": started,
           "duration_s": round(time.time() - t0), "executed_notebook": saved.name, "entry": entry,
           "reused_answers": used["reused"], "new_llm_calls": used["new"]}
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)
    print(f"done {a.ticker}: rating {entry['latest'].get('rating')} in {out['duration_s']} s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
