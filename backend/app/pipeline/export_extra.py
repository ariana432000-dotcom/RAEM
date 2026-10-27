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
WEB = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend/
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

out = {"ai": {}, "dataset": {}, "backtest": {}, "memory": {}, "news_corpus": []}
EXTRA_PATH = os.path.join(os.path.dirname(HERE), "..", "data", "extra.json")
EXTRA_PATH = os.path.abspath(EXTRA_PATH)


def ai_for(t):
    """LLM analyst sessions: every step the notebooks log, newest session in full (None when it never ran)."""
    return ai_entry(os.path.join(ROOT, "notebooks"), t)


def dataset_for(t):
    """The full price table of a company."""
    df = fl.load_combined_history(t)
    rows = []
    for i, r in df.iterrows():
        rows.append([str(pd.Timestamp(i).date()), round(float(r["Open"]), 4) if r.get("Open") == r.get("Open") else None,
                     round(float(r["High"]), 4) if r.get("High") == r.get("High") else None,
                     round(float(r["Low"]), 4) if r.get("Low") == r.get("Low") else None,
                     round(float(r["Close"]), 4), int(r["Volume"]) if r.get("Volume") == r.get("Volume") and r.get("Volume") else None,
                     str(r.get("source", ""))])
    return rows


def backtest_for(t):
    """Backtest from the forecast log (graded rows only; day-7 calls, non-overlapping origins). {} until forecasts are graded."""
    p = os.path.join(ROOT, "forecast_log", f"{t.replace('.', '_')}_forecasts.csv")
    if not os.path.exists(p):
        p = os.path.join(ROOT, "forecast_log", f"{pl.safe_filename(t)}_forecasts.csv")
    if not os.path.exists(p):  # a company added on the website has no forecast log until it has been forecast
        return {}
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
    return res


def fx_for(currency):
    try:
        rate = float(pl.get_fx_rate_to_usd(currency))
        return rate if rate == rate and rate > 0 else None   # NaN/0 (no cached rate) -> null, not invalid JSON
    except Exception:
        return None


# ---- one company only (python export_extra.py --only TSLA): update its entries in the saved extra.json, leave the rest ----
if "--only" in sys.argv:
    tk = sys.argv[sys.argv.index("--only") + 1]
    saved = json.load(open(EXTRA_PATH, encoding="utf-8"))
    ai = ai_for(tk)
    if ai:
        saved.setdefault("ai", {})[tk] = ai
    saved.setdefault("dataset", {})[tk] = dataset_for(tk)
    saved.setdefault("backtest", {})[tk] = backtest_for(tk)
    cur = pl.TICKERS[tk]["currency"]
    saved.setdefault("fx", {})
    if saved["fx"].get(cur) is None:
        saved["fx"][cur] = fx_for(cur)
    tmp = EXTRA_PATH + ".tmp"
    json.dump(saved, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, EXTRA_PATH)
    print("extra data updated for", tk, "| price rows", len(saved["dataset"][tk]), "| backtest models", len(saved["backtest"][tk]))
    sys.exit(0)

# ---- LLM analyst sessions ----
for t in pl.TICKERS:
    e = ai_for(t)
    if e:
        out["ai"][t] = e

# ---- full price tables ----
for t in pl.TICKERS:
    out["dataset"][t] = dataset_for(t)

# ---- backtest ----
for t in pl.TICKERS:
    out["backtest"][t] = backtest_for(t)

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

out["asof"] = pd.Timestamp.now().strftime("%Y-%m-%d")   # the page's "Data as of" date (frontend/src/build.py reads it)

# FX rates (currency units per USD), used by the return calculator
out["fx"] = {cur: fx_for(cur) for cur in sorted({v["currency"] for v in pl.TICKERS.values()})}
json.dump(out, open(EXTRA_PATH, "w", encoding="utf-8"), ensure_ascii=False)
print({k: (len(v) if hasattr(v, "__len__") else v) for k, v in out.items()})
print({k: len(v) for k, v in out["backtest"].items()})
print(json.dumps(out["news_corpus"][:2], ensure_ascii=False)[:800])
print(json.dumps(out["memory"].get("raem_episodic_memory", [])[:1], ensure_ascii=False)[:800])
print(os.path.getsize(EXTRA_PATH))
