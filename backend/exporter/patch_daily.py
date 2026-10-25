"""Recompute the 'daily' block (cached run_pipeline, no memory write) for every exported ticker JSON."""
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")
WEB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.environ.get("RAEM_ENGINE") or os.path.join(WEB, "engine")  # forecasting code + its data (see tools/sync_engine.py)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.chdir(ROOT)

import predictor_lib as pl  # noqa: E402
from export_ticker import clean  # noqa: E402

KEEP = ["final_signal", "final_expected_return", "signal_threshold", "signal_basis", "pred_return_7d",
        "pred_price_7d", "raw_pred_return_7d", "winner_model", "winner_direction_acc", "memory_note",
        "memory_correction", "memory_n_similar", "last_price", "volatility_30d", "all_5_agree_on_direction",
        "data_end_date", "n_rows", "kalman_level", "arima_order",
        "xgb_pred_return", "xgb_direction_acc", "xgb_holdout_mae",
        "lstm_pred_return", "lstm_direction_acc", "lstm_holdout_mae",
        "arima_pred_return", "arima_direction_acc", "arima_holdout_mae",
        "ridge_pred_return", "ridge_direction_acc", "ridge_holdout_mae",
        "kalman_pred_return", "kalman_direction_acc", "kalman_holdout_mae"]

for t in sys.argv[1:] or list(pl.TICKERS):
    p = os.path.join(os.path.dirname(HERE), "data", "tickers", f"{t}.json")
    if not os.path.exists(p):
        print("skip", t)
        continue
    d = json.load(open(p, encoding="utf-8"))
    record, raw, feat = pl.run_pipeline(t, verbose=False)
    record, _ = pl.apply_memory_correction(t, record, pl.state_embedding(feat))
    record.update(pl.decide_signal(t, record["pred_return_7d"], record["volatility_30d"], price_df=raw))
    d["daily"] = {k: clean(record.get(k)) for k in KEEP}
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    print("patched", t, d["daily"]["data_end_date"], d["daily"]["final_signal"])
