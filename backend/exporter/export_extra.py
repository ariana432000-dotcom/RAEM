"""Read-only export of the extra pages' data: LLM analyst sessions, fundamentals, news, memory, full price tables, backtest."""
import glob
import json
import os
import re
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
WEB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.environ.get("RAEM_ENGINE") or os.path.join(WEB, "engine")  # forecasting code + its data (see tools/sync_engine.py)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.chdir(ROOT)

import chromadb  # noqa: E402
import forecast_lib as fl  # noqa: E402
import predictor_lib as pl  # noqa: E402
from export_ticker import clean  # noqa: E402

from sessions import ai_entry, trunc  # noqa: E402

out = {"ai": {}, "dataset": {}, "backtest": {}, "memory": {}, "news_corpus": [], "papers": []}

# ---- LLM analyst sessions: every step the notebooks log, newest session in full ----
for t in pl.TICKERS:
    e = ai_entry(os.path.join(ROOT, "notebooks"), t)
    if e:
        out["ai"][t] = e

# ---- full price tables ----
for t in pl.TICKERS:
    df = fl.load_combined_history(t)
    rows = []
    for i, r in df.iterrows():
        rows.append([str(pd.Timestamp(i).date()), round(float(r["Open"]), 4) if r.get("Open") == r.get("Open") else None,
                     round(float(r["High"]), 4) if r.get("High") == r.get("High") else None,
                     round(float(r["Low"]), 4) if r.get("Low") == r.get("Low") else None,
                     round(float(r["Close"]), 4), int(r["Volume"]) if r.get("Volume") == r.get("Volume") and r.get("Volume") else None,
                     str(r.get("source", ""))])
    out["dataset"][t] = rows

# ---- backtest from the forecast log (graded rows only; day-7 calls, non-overlapping origins) ----
for t in pl.TICKERS:
    p = os.path.join(ROOT, "forecast_log", f"{t.replace('.', '_')}_forecasts.csv")
    if not os.path.exists(p):
        p = os.path.join(ROOT, "forecast_log", f"{pl.safe_filename(t)}_forecasts.csv")
    d = pd.read_csv(p)
    d = d[d["actual_price"].notna()].copy()
    d["made_on"] = pd.to_datetime(d["made_on"])
    d["target_date"] = pd.to_datetime(d["target_date"])
    res = {}
    for m, g in d[d["horizon_day"] == 7].groupby("model"):
        g = g.sort_values("made_on")
        picked, last_d = [], None
        for _, r in g.iterrows():
            if last_d is None or (r["made_on"] - last_d).days >= 7:
                picked.append(r)
                last_d = r["made_on"]
        if len(picked) < 8:
            continue
        pdx = pd.DataFrame(picked)
        sgn = np.sign(pdx["predicted_price"] - pdx["price_when_made"])
        ret = pdx["actual_price"] / pdx["price_when_made"] - 1
        strat = (sgn * ret).values
        bh = ret.values
        eq = np.cumprod(1 + strat)
        peak = np.maximum.accumulate(eq)
        res[m] = {"n": int(len(pdx)), "hit": clean(float((np.sign(ret) == sgn)[sgn != 0].mean() * 100)) if (sgn != 0).any() else None,
                  "always_up": clean(float((ret > 0).mean() * 100)),
                  "strategy_pct": clean(float((eq[-1] - 1) * 100)), "buyhold_pct": clean(float((np.prod(1 + bh) - 1) * 100)),
                  "avg_trade_pct": clean(float(strat.mean() * 100)), "max_dd_pct": clean(float(((eq / peak - 1).min()) * 100)),
                  "dates": [str(x.date()) for x in pdx["made_on"]], "equity": [round(float(x), 4) for x in eq],
                  "buyhold_eq": [round(float(x), 4) for x in np.cumprod(1 + bh)]}
    out["backtest"][t] = res

# ---- memory ----
c = chromadb.PersistentClient(path=os.path.join(ROOT, "chroma_memory"))
g = c.get_collection("prediction_episodes").get(include=["documents", "metadatas"])
eps = []
for i, doc, md in zip(g["ids"], g["documents"], g["metadatas"]):
    eps.append({"id": i, "doc": doc, **{k: clean(v) for k, v in (md or {}).items()}})
eps.sort(key=lambda e: e.get("run_id", ""), reverse=True)
out["memory"]["episodes"] = eps
c2 = chromadb.PersistentClient(path=os.path.join(ROOT, "notebooks", "raem_chroma_db"))
for col in c2.list_collections():
    name = col.name if hasattr(col, "name") else col
    cc = c2.get_collection(name)
    gg = cc.get(include=["documents", "metadatas"])
    items = [{"id": i, "doc": trunc(d, 500), "meta": clean(m or {})} for i, d, m in zip(gg["ids"], gg["documents"], gg["metadatas"])]
    if name == "raem_news_corpus":
        out["news_corpus"] = items
    else:
        out["memory"][name] = items

# ---- research papers ----
for p in sorted(glob.glob(os.path.join(ROOT, "paper", "*.pdf"))):
    out["papers"].append({"file": os.path.basename(p), "mb": round(os.path.getsize(p) / 1e6, 2)})

# FX rates (currency units per USD), used by the paper-trading page
out["fx"] = {}
for cur in sorted({v["currency"] for v in pl.TICKERS.values()}):
    try:
        out["fx"][cur] = float(pl.get_fx_rate_to_usd(cur))
    except Exception:
        out["fx"][cur] = None
json.dump(out, open(os.path.join(os.path.dirname(HERE), "data", "extra.json"), "w", encoding="utf-8"), ensure_ascii=False)
print({k: (len(v) if hasattr(v, "__len__") else v) for k, v in out.items()})
print({k: len(v) for k, v in out["backtest"].items()})
print(json.dumps(out["news_corpus"][:2], ensure_ascii=False)[:800])
print(json.dumps(out["memory"].get("raem_episodic_memory", [])[:1], ensure_ascii=False)[:800])
print(os.path.getsize(os.path.join(os.path.dirname(HERE), "data", "extra.json")))
