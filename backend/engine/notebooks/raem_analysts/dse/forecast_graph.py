# raem_analysts/dse/forecast_graph.py -- shared by abbank-dse, batbc-dse, bpml-dse, waltonhil-dse
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 7-DAY FORECAST GRAPH -- every model's price path, and the
# track record of what was predicted for the days already past
# ================================================================
# Trains all 14 base models (Ridge, XGBoost, LSTM, ARIMA, Kalman, LASSO,
# RandomForest, DNN, WSAE-LSTM, TFT, Theta, N-BEATS, RegimeSwitch, ARIMAX),
# the best-two Ensemble and the two fusion models, then shows:
#   1. the main chart -- recent prices, past Ensemble calls marked hit/miss,
#      every model's path for the next 7 trading days, and the hit rate by day;
#   2. one small panel per model, so every model's path can be read on its own;
#   3. a table of every model's forecast and its error vs "no change".
# Every run archives its forecast to forecast_log/<TICKER>_forecasts.csv and
# grades any earlier forecast whose target date has now arrived. The
# standalone section at the end of the notebook reuses this result.

import warnings
import matplotlib.pyplot as plt
from IPython.display import display

import forecast_lib as fl

# after the import: statsmodels re-enables its own warnings when first imported
warnings.filterwarnings("ignore")

if COMPANY not in pl.TICKERS:
    print(f"WARNING: {COMPANY} is not in predictor_lib.TICKERS -- skipping the forecast graph.")
else:
    fcast = fl.forecast_ticker(COMPANY, verbose=True)
    fl.record_forecast(COMPANY, fcast)
    _newly_graded = fl.grade_forecasts(COMPANY)
    if _newly_graded:
        print(f"Graded {_newly_graded} past prediction(s) against what actually happened.")
    hit_report = fl.hit_rate_report(COMPANY)

    fl.plot_forecast_report(fcast, hit_report)
    plt.show()
    fl.plot_all_models(fcast)
    plt.show()

    print(f"Ensemble = average of the best two models: {' + '.join(fcast['ensemble_members'])}")
    print(f"Every model's {fl.FORECAST_DAYS}-day forecast ({fcast['currency']}):")
    display(fl.all_models_table(fcast))

    # past Ensemble calls for the last 30 trading days, as drawn on the chart
    _g = fl.graded_points(COMPANY, model="Ensemble")
    if not _g.empty:
        _g = _g[_g["target_date"] >= fcast["raw"].index[-30]]
    _hit = int(_g["within_tolerance"].sum()) if not _g.empty else 0
    _miss = int(len(_g) - _hit) if not _g.empty else 0

    log_step(
        "Forecast Graph",
        {"ticker": COMPANY, "horizon_days": fl.FORECAST_DAYS,
         "source": f"forecast_lib ({len(fl.BASE_MODELS)} base models, best-two Ensemble, 2 fusions)"},
        {"metrics": {
            "best_model": fcast["best_model"],
            "ensemble_members": fcast["ensemble_members"],
            "last_price_usd": round(fcast["last_price_usd"], 4),
            "forecast_final_price_usd": round(fcast["final_price_usd"], 4),
            "native_price": round(fcast["last_price"], 4),
            "native_currency": fcast["currency"],
            "total_move_pct": round(fcast["total_return_pct"], 3),
            "direction": fcast["direction"],
            "past_calls_within_tolerance": _hit,
            "past_calls_off": _miss,
        }},
    )

