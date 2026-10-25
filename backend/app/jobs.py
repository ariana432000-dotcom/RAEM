"""Background worker: runs queued jobs one at a time and records each in the database.

Kinds:
  forecast  - exporter/export_ticker.py for one company with the chosen forecast models (reads the CSV price files)
  analysis  - exporter/run_notebook.py: the company's notebook, Run All, with the chosen Ollama model
  refresh   - refresh.py steps (price top-up, re-export, rebuild)

Same-date reuse: submit() returns the stored result of an earlier finished run with the same
company, date and model unless force=True, and joins a run that is already queued or running.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime

from . import config, db

sys.path.insert(0, str(config.BACKEND))
import refresh  # noqa: E402

PY = sys.executable
EXPORTER = config.BACKEND / "exporter"
_live: dict[str, list[str]] = {}
_procs: dict[str, subprocess.Popen] = {}  # the child process of each running run, so Hold can stop it
_held: set[str] = set()                   # runs asked to hold: their stop counts as 'held', not 'failed'
_wake = threading.Event()
_started = False
_start_lock = threading.Lock()


class QueueFull(Exception):
    """Too many runs are already waiting."""


def start_worker() -> None:
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, name="raem-worker", daemon=True).start()


def _loop() -> None:
    while True:
        try:
            run = db.next_queued()
        except Exception as e:  # never let a database hiccup stop the worker
            print("worker: could not read the queue:", e, flush=True)
            run = None
        if run is None:
            _wake.wait(5)
            _wake.clear()
            continue
        _keep_awake(True)
        try:
            _execute(run)
        except Exception as e:
            print("worker: run", run.get("id"), "crashed:", e, flush=True)
        finally:
            _keep_awake(False)


def _keep_awake(on: bool) -> None:
    """On Windows, stop the PC from going to sleep while a run is going (a sleep makes the cell time limit
    fire on wake). The display is kept on too: on Modern Standby laptops a display that turns off suspends the
    run. Closing a laptop lid still sleeps. Does nothing on other systems."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x00000001, 0x00000002
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED if on else 0))
    except Exception:
        pass


def submit(kind: str, ticker: str, model_key: str, params: dict, force: bool = False, run_date: str | None = None) -> tuple[dict, str | None]:
    """Queue a run, or return an existing one. The second value is 'stored', 'active' or None (new)."""
    run_date = run_date or config.today()
    if not force:
        done = db.find(kind, ticker, run_date, model_key, ("done",))
        if done:
            return done, "stored"
    active = db.find(kind, ticker, run_date, model_key, ("queued", "running"), with_result=False)
    if active:
        return public(active), "active"
    if kind != "refresh" and db.active_count() >= config.MAX_QUEUE:
        raise QueueFull(f"{config.MAX_QUEUE} runs are already waiting. Try again when some have finished.")
    held = db.find(kind, ticker, run_date, model_key, ("held",), with_result=False)
    if held:  # resume: back in the queue; an analysis reuses its saved LLM answers while the prompts are unchanged
        db.update(held["id"], status="queued", error=None, finished_at=None)
        _wake.set()
        return public(db.get(held["id"], with_result=False)), "resumed"
    run = db.create(kind, ticker, run_date, model_key, params)
    _wake.set()
    return public(run), None


def hold(rid: str) -> dict | None:
    """Stop a queued or running run and keep it for later. Its saved data stays; the next request for the same
    company, date and model resumes it."""
    run = db.get(rid, with_result=False)
    if run is None or run["status"] not in ("queued", "running"):
        return run
    if run["status"] == "queued":
        db.update(rid, status="held")
    else:
        _held.add(rid)
        p = _procs.get(rid)
        if p is not None:
            _kill_tree(p)
    return public(db.get(rid, with_result=False))


def _kill_tree(p: subprocess.Popen) -> None:
    """Stop a child and everything it started (the notebook kernel too, so its LLM request ends)."""
    try:
        import psutil
        parent = psutil.Process(p.pid)
        for c in parent.children(recursive=True):
            c.kill()
        parent.kill()
    except Exception:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        else:
            p.kill()


def public(run: dict | None) -> dict | None:
    """A run as the API returns it, with the live log while it is running and its place in the queue."""
    if run is None:
        return None
    if run["status"] == "running" and run["id"] in _live:
        run["log"] = _live[run["id"]][-80:]
    if run["status"] == "running" and run.get("started_at"):
        try:
            run["elapsed"] = int((datetime.now() - datetime.fromisoformat(run["started_at"])).total_seconds())
        except ValueError:
            pass
    if run["status"] in ("queued", "running"):
        run["ahead"] = db.queued_before(run["id"])
    if run["kind"] == "analysis" and run["status"] in ("held", "failed"):
        run["saved_answers"] = _saved_answers(run["ticker"], run["model_key"])  # what the next run of it reuses
    return run


def _saved_answers(ticker: str, model_key: str) -> int:
    try:
        with open(config.llm_cache_path(ticker, model_key), encoding="utf-8") as fh:
            return len(json.load(fh).get("entries", {}))
    except (OSError, ValueError):
        return 0


def get(rid: str, with_result: bool = True) -> dict | None:
    return public(db.get(rid, with_result))


def _log(rid: str, line: str) -> None:
    buf = _live.setdefault(rid, [])
    buf.append(line)
    if len(buf) > 600:
        del buf[:200]
    if line.startswith("== Held"):  # keep the step it reached, so the page can say where it stopped
        db.update(rid, log=buf)
    elif line.startswith("== "):
        db.update(rid, step=line[3:][:200], log=buf)


def _stream(rid: str, argv: list[str], timeout: int, env: dict | None = None) -> bool:
    e = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1", **(env or {}))
    p = subprocess.Popen(argv, cwd=str(config.BACKEND), env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace")
    _procs[rid] = p
    killed = []

    def _kill():
        killed.append(True)
        _kill_tree(p)

    timer = threading.Timer(timeout, _kill)
    timer.start()
    try:
        for line in p.stdout:
            _log(rid, line.rstrip())
        code = p.wait()
    finally:
        timer.cancel()
        _procs.pop(rid, None)
    if code != 0:
        _log(rid, "== Held: stopped on request; saved data kept" if rid in _held else
             f"!! exited with code {code}" + (" (time limit reached)" if killed else ""))
    return code == 0


def _rebuild(rid: str) -> None:
    for script in ("merge.py", "build.py"):
        subprocess.run([PY, str(config.WEB / "frontend" / "src" / script)], cwd=str(config.WEB), capture_output=True,
                       env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1"))
    _log(rid, "== Page rebuilt")


def _write_json(path, obj) -> None:
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _run_forecast(run: dict) -> tuple[dict, dict]:
    t, models = run["ticker"], run["params"]["models"]
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "result.json")
        ok = _stream(run["id"], [PY, "-W", "ignore", str(EXPORTER / "export_ticker.py"), t, "--models", ",".join(models), "--out", out],
                     config.FORECAST_TIMEOUT)
        if not ok or not os.path.exists(out):
            raise RuntimeError("The forecast did not finish. See the log for the reason.")
        result = json.load(open(out, encoding="utf-8"))
    f = result["forecast"]
    ens = f["paths"].get("Ensemble")
    summary = {"models": models, "last_date": f["last_date"], "last_price": f["last_price"],
               "ensemble_day7": ens[6] if ens else None,
               "ensemble_return_pct": (ens[6] / f["last_price"] - 1) * 100 if ens else None,
               "daily_signal": result["daily"].get("final_signal"), "failed_models": list((f.get("failed_models") or {}).keys())}
    if sorted(models) == sorted(config.DEFAULT_MODELS):  # the site's default model set: update the saved page too
        _write_json(config.TICKERS_DIR / f"{t}.json", result)
        _rebuild(run["id"])
    return result, summary


def _run_analysis(run: dict) -> tuple[dict, dict]:
    t, model = run["ticker"], run["model_key"]
    params = run.get("params") or {}
    provider = params.get("provider", "ollama")  # runs stored before cloud providers have only an Ollama model
    cache = config.llm_cache_path(t, model)
    cache.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "result.json")
        argv = [PY, "-W", "ignore", str(EXPORTER / "run_notebook.py"), "--ticker", t, "--provider", provider,
                "--model", params.get("llm_model", model), "--out", out]
        ok = _stream(run["id"], argv, config.NOTEBOOK_TIMEOUT, env={"OLLAMA_BASE_URL": config.OLLAMA_URL, "RAEM_LLM_CACHE": str(cache)})
        if not ok or not os.path.exists(out):
            raise RuntimeError("The notebook run did not finish. See the log for the failing cell.")
        result = json.load(open(out, encoding="utf-8"))
    try:  # finished: the next run of this company and model starts fresh
        cache.unlink(missing_ok=True)
    except OSError:
        pass
    latest = result["entry"]["latest"]
    summary = {"llm_model": model, "rating": latest.get("rating"), "trade_date": latest.get("date"),
               "steps": len(latest.get("steps") or {}), "duration_s": result.get("duration_s"), "notebook": result.get("notebook"),
               "reused_answers": result.get("reused_answers"), "new_llm_calls": result.get("new_llm_calls")}
    try:  # keep the saved page in step with the newest analysis
        extra = json.load(open(config.EXTRA, encoding="utf-8"))
        extra.setdefault("ai", {})[t] = result["entry"]
        _write_json(config.EXTRA, extra)
        _rebuild(run["id"])
    except Exception as e:
        _log(run["id"], f"!! Could not update extra.json: {e}")
    return result, summary


def _run_refresh(run: dict) -> tuple[dict, dict]:
    p = run["params"]
    ok = refresh.run(refresh.steps(p.get("tickers"), extra=p.get("extra", True), top_up=p.get("top_up", True)),
                     on_line=lambda s: _log(run["id"], s))
    if not ok:
        raise RuntimeError("A refresh step failed. See the log.")
    return {"ok": True}, {"tickers": p.get("tickers") or "all"}


RUNNERS = {"forecast": _run_forecast, "analysis": _run_analysis, "refresh": _run_refresh}


def _execute(run: dict) -> None:
    rid = run["id"]
    _live[rid] = []
    db.update(rid, status="running", started_at=db.now())
    t0 = time.time()
    try:
        result, summary = RUNNERS[run["kind"]](run)
        summary = dict(summary or {}, seconds=round(time.time() - t0))
        db.update(rid, status="done", finished_at=db.now(), result=result, summary=summary, log=_live.get(rid, []))
    except Exception as e:
        if rid in _held:  # stopped by Hold: keep it for a resume, at the step it reached
            db.update(rid, status="held", error=None, log=_live.get(rid, []))
        else:
            _log(rid, f"!! {type(e).__name__}: {e}")
            db.update(rid, status="failed", finished_at=db.now(), error=str(e)[:2000], log=_live.get(rid, []))
    finally:
        _live.pop(rid, None)
        _held.discard(rid)
        _save_state(f"{run['kind']} {run['ticker']}")


def _save_state(reason: str) -> None:
    """On a Hugging Face Space with RAEM_HF_DATASET set: copy the changed data (memory, database...) to the dataset."""
    try:
        sys.path.insert(0, str(config.BACKEND / "tools"))
        import hf_sync
        hf_sync.save_in_background(reason)
    except Exception as e:
        print("worker: state save skipped:", e, flush=True)
