"""Run the AI analysis (TradingAgents + RAEM memory) of one company, with a chosen LLM, and save the new session in the
website's format. One Python pipeline for every ticker (app/ai_pipeline/, the any_with_dse pipeline): no notebook per company.

    python app/ai_engine/run_pipeline.py --ticker AAPL --provider openai --model gpt-4.1-mini --out result.json
    python app/ai_engine/run_pipeline.py --ticker ABBANK --provider ollama --model qwen2.5:7b --out result.json

Printed lines (the server shows them as the run's log):
    == [3/16] News Analyst        a stage starts
    !! Cause: <reason>  (in step: <stage>)   the run failed
Environment: RAEM_LLM_CACHE (file of saved model answers: Continue after a hold or a failure), OLLAMA_BASE_URL, API keys.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
sys.path.insert(0, str(BACKEND))                 # app.ai_pipeline, tradingagents
sys.path.insert(0, str(BACKEND / "app" / "pipeline"))   # sessions.py

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
MODEL_REFUSED = re.compile(r"(model[^\n]{0,60}(not found|does not exist|not exist|unknown|deprecated|retired|decommission|invalid))"
                           r"|((not found|does not exist|invalid)[^\n]{0,40}model)|(?<!Module)NotFoundError|model_not_found", re.I)
BILLING = re.compile(r"credit balance|insufficient[_ ]quota|billing|payment required|exceeded your current quota|recharge", re.I)
KEY_REFUSED = re.compile(r"\b401\b|\b403\b|invalid[ _]api[ _]key|authentication|permission|No API key", re.I)


def failure_cause(message: str, stage: str | None) -> list[str]:
    """Plain lines saying why the run failed (the last line of the error) and, for common causes, what to do."""
    text = ANSI.sub("", str(message)).strip()
    last = ""
    for ln in reversed(text.splitlines()):
        if ln.strip():
            last = ln.strip()
            break
    out = [f"!! Cause: {last[:400]}" + (f"  (in step: {stage})" if stage else "")]
    if BILLING.search(text):
        out.append("!! The provider account has no credit left. Add credit there, or choose another provider, then press Continue.")
    elif MODEL_REFUSED.search(last):
        prov = os.environ.get("RAEM_LLM_PROVIDER", "")
        out.append("!! The provider refused the model name. Choose another model in the list" +
                   (f", or update RAEM_{prov.upper()}_MODELS on the server (a retired model stays in an old list)." if prov and prov != "ollama" else "."))
    elif KEY_REFUSED.search(last):
        out.append("!! The provider refused the API key. Check the key of this provider on the server and restart it.")
    return out


def debate_block(state: dict) -> dict | None:
    """The complete debates of a finished run as the website's debate block (the trading log keeps only excerpts)."""
    from sessions import debate_turns, trunc
    deb, risk = state.get("investment_debate_state") or {}, state.get("risk_debate_state") or {}
    turns = debate_turns(deb.get("history"))
    risk_turns = debate_turns(risk.get("history"))
    if not risk_turns:   # no speaker labels in the history: one turn per analyst
        risk_turns = [{"speaker": f"{k.title()} Analyst", "side": k, "round": None, "text": trunc(risk.get(f"{k}_history"), 4000)}
                      for k in ("aggressive", "conservative", "neutral") if str(risk.get(f"{k}_history") or "").strip()]
    if not turns and not risk_turns:
        return None
    return {"turns": turns, "facilitator": trunc(state.get("investment_facilitator_decision"), 6000), "risk_turns": risk_turns,
            "risk_facilitator": trunc(state.get("risk_facilitator_decision"), 6000),
            "trader": trunc(state.get("trader_investment_plan"), 4000), "complete": True}


def drop_session(ticker: str, session_start: str) -> None:
    """Remove the half-written session of a failed run from the trading log, so history shows only finished sessions."""
    from app.ai_pipeline import config
    p = config.log_file_for(ticker)
    try:
        data = json.load(open(p, encoding="utf-8"))
        kept = [s for s in data if not (isinstance(s, dict) and s.get("session_start") == session_start)]
        if len(kept) != len(data):
            with open(p, "w", encoding="utf-8") as f:
                json.dump(kept, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--provider", default="ollama", choices=["ollama", "openai", "anthropic", "google", "groq", "kimi"])
    ap.add_argument("--model", default=os.environ.get("RAEM_LLM_DEFAULT", "qwen2.5:7b"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--market", default="auto", help="accepted for compatibility: the pipeline recognises Dhaka Stock Exchange codes itself")
    ap.add_argument("--name", help="accepted for compatibility")
    a = ap.parse_args()
    ticker = a.ticker.strip().upper()

    os.environ["RAEM_LLM_PROVIDER"] = a.provider
    os.environ["RAEM_LLM_MODEL"] = a.model
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")

    from app.ai_pipeline import config, embeddings, llm as llm_mod
    from app.ai_pipeline.runner import PipelineRunner, STAGES
    from sessions import ai_entry, archive_logs

    if not config.ensure_tradingagents_on_path():
        print("!! Cause: the tradingagents package was not found (expected in backend/tradingagents)", flush=True)
        return 1
    if "RAEM_EMBED" not in os.environ:
        os.environ["RAEM_EMBED"] = embeddings.embed_mode()
    mode = os.environ["RAEM_EMBED"]
    print("== Memory embeddings: " + {"ollama": "nomic-embed-text on Ollama (the original store)",
                                     "local": "chromadb's built-in model, separate store raem_chroma_db_local",
                                     "hash": "word hashing (no embedding model), separate store raem_chroma_db_hash"}[mode], flush=True)
    copied = embeddings.copy_memory(mode)
    if copied:
        print(f"== Memory: {copied} past episodes and news chunks copied into the {mode} store", flush=True)

    cache = os.environ.get("RAEM_LLM_CACHE", "")
    saved_before = llm_mod.load_saved(cache) if cache else 0
    if saved_before:
        print(f"== Resuming: {saved_before} saved LLM answers from the earlier run are reused while their prompts are unchanged", flush=True)

    kept = archive_logs(str(config.DATA_DIR))   # the log keeps 10 days; copy every session into the archive first
    print(f"== Archived {kept} logged sessions before the run", flush=True)
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    trade_date = datetime.now().strftime("%Y-%m-%d")
    t0 = time.time()
    llm_key = f"{a.provider}:{a.model}" if a.provider != "ollama" else a.model
    print(f"== Prepared the AI analysis of {ticker}: {len(STAGES)} steps, model {llm_key}", flush=True)

    runner = PipelineRunner(ticker, trade_date, provider_override=a.provider)
    stage = {"label": None}
    error = None
    try:
        for ev in runner.run():
            t = ev.get("type")
            if t == "stage_start":
                stage["label"] = ev["label"]
                print(f"== [{ev['index']}/{ev['total']}] {ev['label']}", flush=True)
            elif t == "log":
                print("   " + str(ev.get("line", ""))[:300], flush=True)
            elif t == "error":
                error = ev
                break
    except Exception as e:   # noqa: BLE001
        error = {"stage": stage["label"], "message": f"{type(e).__name__}: {e}\n{traceback.format_exc()}"}

    if error is None and not runner.session.data.get("final_decision"):
        error = {"message": "The pipeline ended without a final decision."}
    if error is not None:
        drop_session(ticker, runner.session.data["session_start"])
        msg = str(error.get("message", ""))
        print("!! The analysis failed:", flush=True)
        for line in msg.splitlines()[-8:]:
            print("   " + ANSI.sub("", line), flush=True)
        # the error text is "<exception>\n<traceback>": the exception is the first line
        for line in failure_cause(msg.splitlines()[0] if msg.strip() else msg, stage["label"]):
            print(line, flush=True)
        return 1

    archive_logs(str(config.DATA_DIR))
    entry = ai_entry(str(config.DATA_DIR), ticker, since=started[:16])
    if entry is None:
        print("!! The analysis finished but wrote no new trading-log session for this company.", flush=True)
        return 1
    full = debate_block(runner.state)
    if full:
        entry["latest"]["debate"] = full
    entry["latest"].update(llm_model=llm_key, llm_provider=a.provider, memory_embeddings=mode)
    used = llm_mod.saved_counts()
    if saved_before:
        print(f"== Reused {used['reused']} saved answers, made {used['new']} new LLM calls", flush=True)
    out = {"ticker": ticker, "llm_model": llm_key, "llm_provider": a.provider, "pipeline": "ai_pipeline", "started": started,
           "duration_s": round(time.time() - t0), "entry": entry, "reused_answers": used["reused"], "new_llm_calls": used["new"]}
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)
    print(f"done {ticker}: rating {entry['latest'].get('rating')} in {out['duration_s']} s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
