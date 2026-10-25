"""Export one ticker's daily signal, 7-day forecast, model table and hit-rate to JSON.

    python exporter/export_ticker.py AAPL IBM                       # default six models -> data/tickers/<T>.json
    python exporter/export_ticker.py AAPL --models ridge,tft --out run.json   # chosen models, one file (used by the API)

Reads the engine's CSV price files like the notebooks do. In --out mode a failure exits with code 1.
"""
import json
import math
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
WEB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.environ.get("RAEM_ENGINE") or os.path.join(WEB, "engine")  # forecasting code + its data (see tools/sync_engine.py)
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "tickers")
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, ROOT)
_START_CWD = os.getcwd()
os.chdir(ROOT)

import forecast_agent as fa  # noqa: E402
import forecast_lib as fl  # noqa: E402
import predictor_lib as pl  # noqa: E402

# The website shows only these six: the best average rank across the 8 tickers (error vs naive,
# common holdout, export of 2026-10-20). Ensemble, Fusion and the reviewer are rebuilt from them.
KEEP_MODELS = ["nbeats", "lasso", "ridge", "random_forest", "theta", "arima"]
# forecast_lib only fits the blends when their keys are in the list
BLENDS = ["ensemble", "fusion", "fusion_top3"]


def clean(x):
    if isinstance(x, (np.floating, float)):
        return None if not math.isfinite(float(x)) else round(float(x), 6)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, np.ndarray):
        return [clean(v) for v in x.tolist()]
    if isinstance(x, (pd.Timestamp,)):
        return str(x.date())
    if isinstance(x, pd.DataFrame):
        d = x.reset_index()
        return [{str(k): clean(v) for k, v in r.items()} for r in d.to_dict("records")]
    if isinstance(x, pd.Series):
        return clean(x.to_dict())
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, (str, bool)) or x is None:
        return x
    return str(x)


DAILY_KEYS = ["final_signal", "final_expected_return", "signal_threshold", "signal_basis", "pred_return_7d",
              "pred_price_7d", "raw_pred_return_7d", "winner_model", "winner_direction_acc", "memory_note",
              "memory_correction", "memory_n_similar", "last_price", "volatility_30d", "all_5_agree_on_direction",
              "data_end_date", "n_rows", "kalman_level", "arima_order",
              "xgb_pred_return", "xgb_direction_acc", "xgb_holdout_mae",
              "lstm_pred_return", "lstm_direction_acc", "lstm_holdout_mae",
              "arima_pred_return", "arima_direction_acc", "arima_holdout_mae",
              "ridge_pred_return", "ridge_direction_acc", "ridge_holdout_mae",
              "kalman_pred_return", "kalman_direction_acc", "kalman_holdout_mae"]


def export(t, models=None, out_path=None):
    models = list(models or KEEP_MODELS)
    out = {"ticker": t, "meta": pl.TICKERS[t],
           "run_info": {"models": models, "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")}}
    # --- daily signal (no memory write) ---
    record, raw, feat = pl.run_pipeline(t, verbose=False)
    emb = pl.state_embedding(feat)
    record, _ = pl.apply_memory_correction(t, record, emb)
    record.update(pl.decide_signal(t, record["pred_return_7d"], record["volatility_30d"], price_df=raw))
    out["daily"] = {k: clean(record.get(k)) for k in DAILY_KEYS}
    # --- 7-day forecast, all models ---
    f = fl.forecast_ticker(t, verbose=False, models=models + BLENDS)
    paths = f["paths"]
    close = f["raw"]["close"] if "close" in f["raw"] else f["raw"].iloc[:, 0]
    hist = close.tail(180)
    out["history"] = [[str(i.date()), round(float(v), 4)] for i, v in hist.items()]
    out["forecast"] = {
        "last_price": clean(f["last_price"]), "last_date": str(f["last_date"].date()),
        "currency": f["currency"], "company": f["company"], "n_rows": f["n_rows"],
        "dates": [str(i.date()) for i in paths.index],
        "paths": {m: clean(paths[m].values) for m in paths.columns},
        "final_price": clean(f["final_price"]), "total_return_pct": clean(f["total_return_pct"]),
        "direction": f["direction"], "best_model": f["best_model"],
        "ensemble_members": f.get("ensemble_members"), "top_models": f.get("top_models"),
        "failed_models": clean(f["failed_models"]), "always_up_holdout_acc": clean(f["always_up_holdout_acc"]),
        "naive_holdout_mae": clean(f["naive_holdout_mae"]),
        "scoreboard": clean(f["scoreboard"]), "fusion_report": clean(f["fusion_report"]),
        "all_models": clean(fl.all_models_table(f)),
    }
    # --- agent ---
    try:
        plan0 = fa.forecast_agent(f)
        plan = fa.reviewer_agent(f, plan0)
        table = fa.forecast_table(f, plan)
        out["agent"] = {"before_members": clean(plan0["members"]),
                        "plan": {k: clean(v) for k, v in plan.items() if not isinstance(v, (pd.DataFrame,)) or k == "log"},
                        "table": clean(table)}
    except Exception as e:  # keep going
        out["agent"] = {"error": f"{type(e).__name__}: {e}"}
    # --- hit rate ---
    h = fl.hit_rate_report(t)
    out["hitrate"] = {k: clean(h[k]) for k in ["per_model", "by_horizon", "recent_calls", "n_forecasts",
                                                "pending_calls", "verdict"]}
    path = out_path or os.path.join(OUT, f"{t}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)
    os.replace(tmp, path)
    print("done", t, flush=True)


if __name__ == "__main__":
    args, models, out_path, tickers = sys.argv[1:], None, None, []
    i = 0
    while i < len(args):
        if args[i] == "--models":
            models = [m.strip() for m in args[i + 1].split(",") if m.strip()]
            i += 2
        elif args[i] == "--out":
            out_path = os.path.abspath(os.path.join(_START_CWD, args[i + 1]))
            i += 2
        else:
            tickers.append(args[i])
            i += 1
    failed = False
    for tk in tickers:
        print(f"== Forecast {tk} with {len(models or KEEP_MODELS)} models", flush=True)
        try:
            export(tk, models=models, out_path=out_path)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print("FAILED", tk, e, flush=True)
            failed = True
    sys.exit(1 if (failed and out_path) else 0)
