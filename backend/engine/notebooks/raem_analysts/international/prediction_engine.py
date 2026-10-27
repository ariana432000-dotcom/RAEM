# raem_analysts/international/prediction_engine.py -- shared by AAPL, IBM, 0300_HK, 2222_SR
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# PREDICTION ENGINE -- quantitative price forecast (separate from
# the qualitative BUY/SELL/HOLD decision above)
# ================================================================
# Everything above this cell is the multi-agent RAEM decision pipeline
# (analysts -> debate -> risk debate -> Portfolio Manager). It argues in
# words and ends in a signal, but never predicts an actual future PRICE.
# This cell adds that: five tuned ML models (XGBoost / LSTM / ARIMA /
# Ridge / Kalman, built in predictor_lib.py) trained on 2 years of this
# ticker's price history, each evaluated on a genuine held-out tail so
# the reported accuracy is honest, not curve-fit. All four forecasts are
# reported below -- the model with the best out-of-sample direction
# accuracy additionally becomes this run's "winner", the single number
# carried into the episodic-memory correction and the final signal.
#
# Episodic memory (separate from the RAEM memory the agents use above):
# past PENDING forecasts get graded against what actually happened, then
# today's forecast is nudged by the average error in similar past states
# (see predictor_lib.apply_memory_correction -- similarity-gated, outlier-
# clipped, capped at 1 sigma, and a no-op until 5+ comparable graded
# episodes exist). This run is then stored as a new episode for the future.
#
# Results are cached (see predictor_lib.cache_status()) so re-running
# this cell on unchanged price data is near-instant; new data triggers
# a fast refit using the last tuned hyperparameters, not a full re-search.

import sys, os
_PARENT = os.path.abspath("..")
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

import predictor_lib as pl

if COMPANY not in pl.TICKERS:
    print(f"WARNING: {COMPANY} is not configured in predictor_lib.TICKERS -- "
          f"skipping the quantitative forecast for this ticker.")
    prediction_result = None
else:
    # Pull in any trading days that happened since data/*.csv was last written,
    # so the snapshot stays current on its own instead of needing a manual
    # re-download. Appends whole rows only; existing rows are never rewritten,
    # and if the feed is unreachable it just carries on with what is on disk.
    _added = pl.top_up_data_csv(COMPANY)

    _pred_record, _pred_raw, _pred_feat = pl.run_pipeline(COMPANY, verbose=True)

    # --- episodic memory: grade past forecasts, then self-correct today's ---
    _graded_mem = pl.grade_memory(COMPANY, _pred_raw)
    _embedding = pl.state_embedding(_pred_feat)
    _pred_record, _similar_episodes = pl.apply_memory_correction(COMPANY, _pred_record, _embedding)

    # every model's own forecast, independent of which one "wins" --
    # nothing here is hidden behind the single winner pick below.
    _summary = pl.summarize_record(_pred_record)
    _last_price = _pred_record["last_price"]
    model_forecasts = {}
    for _model_name, _row in _summary.iterrows():
        _ret = float(_row["pred_return_7d"])
        model_forecasts[_model_name] = {
            "predicted_return_pct": round(_ret * 100, 3),
            "predicted_price": round(_last_price * (1 + _ret), 4),
            "holdout_direction_accuracy": round(float(_row["direction_acc"]), 3),
            "holdout_mae": round(float(_row["holdout_mae"]), 5),
            "is_winner": bool(_row["winner"]),
        }

    prediction_result = {
        "ticker": COMPANY,
        "horizon_days": pl.HORIZON,
        "model_forecasts": model_forecasts,
        "winner_model": _pred_record["winner_model"],
        "last_price": round(_last_price, 4),
        "raw_predicted_return_pct": round(_pred_record["raw_pred_return_7d"] * 100, 3),
        "predicted_price": round(_pred_record["pred_price_7d"], 4),
        "predicted_return_pct": round(_pred_record["pred_return_7d"] * 100, 3),
        "memory_correction_pct": round(_pred_record["memory_correction"] * 100, 3),
        "memory_correction_applied": _pred_record["memory_correction_applied"],
        "memory_n_similar_episodes": _pred_record["memory_n_similar"],
        "memory_note": _pred_record["memory_note"],
        "holdout_direction_accuracy": round(_pred_record["winner_direction_acc"], 3),
        "all_5_models_agree": _pred_record["all_5_agree_on_direction"],
        "currency": _pred_record["currency"],
        "cache_mode": _pred_record["cache_mode"],
        "graded_past_episodes_this_run": _graded_mem,
    }

    conf = prediction_result["holdout_direction_accuracy"]
    conf_note = ("HIGH" if conf >= 0.60 else "LOW (near coin-flip)" if conf < 0.55 else "MODERATE")
    ccy = prediction_result["currency"]
    lp = prediction_result["last_price"]

    bar = "=" * 66
    print("\n" + bar)
    print(f"  PREDICTION ENGINE -- {COMPANY}")
    print(bar)
    print(f"  Last close : {lp:,.2f} {ccy}")
    print(f"\n  {pl.HORIZON}-day forecast, all {len(model_forecasts)} models:")
    for _model_name, _f in model_forecasts.items():
        _mark = "  <<< used for signal below" if _f["is_winner"] else ""
        print(f"    {_model_name:15s}: {_f['predicted_price']:>10,.2f} {ccy}  "
              f"({_f['predicted_return_pct']:+.2f}%)   "
              f"holdout dir.acc {_f['holdout_direction_accuracy']:.1%}{_mark}")

    w = prediction_result["winner_model"].upper()
    print(f"\n  Winner model       : {w}")
    rawret = prediction_result["raw_predicted_return_pct"]
    print(f"  Raw winner forecast: {rawret:+.2f}%")
    if prediction_result["memory_correction_applied"]:
        mcp = prediction_result["memory_correction_pct"]
        n_sim = prediction_result["memory_n_similar_episodes"]
        print(f"  Memory correction  : {mcp:+.2f}% (from {n_sim} similar past episode(s))")
    else:
        note = prediction_result["memory_note"]
        print(f"  Memory correction  : none applied -- {note}")
    hz = prediction_result["horizon_days"]
    pp = prediction_result["predicted_price"]
    pr = prediction_result["predicted_return_pct"]
    print(f"  FINAL {hz}-day forecast (memory-corrected, from the winner model): "
          f"{pp:,.2f} {ccy}  ({pr:+.2f}%)")
    print(f"  Confidence         : {conf:.1%} holdout direction accuracy ({conf_note})")
    agree = prediction_result["all_5_models_agree"]
    print(f"  All 5 models agree : {agree}")
    print(f"  Past episodes graded this run: {_graded_mem}")
    cm = prediction_result["cache_mode"]
    print(f"  (cache: {cm})")
    print(bar)

    if conf < 0.55:
        print("\n  [!] The winner model is close to a coin flip on held-out data for this "
              "ticker right now -- check the per-model table above, another model may be "
              "more trustworthy this run. Weight the qualitative decision above accordingly.")

    # Store this run as an episode so FUTURE runs can grade it and recall it.
    _episode_id = pl.remember_episode(COMPANY, _pred_record, _embedding)

    # Fold the numeric forecast into this session's log, same convention the
    # analyst cells above already use (a "metrics" dict lands in summary_table).
    log_step(
        "Prediction Engine",
        {"ticker": COMPANY, "horizon_days": pl.HORIZON,
         "source": "predictor_lib (XGBoost/LSTM/ARIMA/Ridge/Kalman + episodic memory)"},
        {"metrics": prediction_result},
    )

