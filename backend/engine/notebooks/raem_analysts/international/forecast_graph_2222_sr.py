# raem_analysts/international/forecast_graph_2222_sr.py -- shared by 2222_SR
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 7-DAY FORECAST GRAPH -- this company's price path, and the
# track record of what was predicted for the days already past
# ================================================================
# The Prediction Engine cell above gives ONE cumulative 7-day number and a
# BUY/HOLD/SELL. This cell answers the other question: what does the path look
# like day by day, and -- more usefully -- how did the last set of predictions
# actually turn out?
#
# Left of "today": grey is the real price; green dots and red crosses are what
# was forecast FOR those days before they happened, with a hairline down to
# what the price actually did. Right of "today": the next seven days.
#
# Every run archives its forecast to forecast_log/<TICKER>_forecasts.csv and
# grades any earlier forecast whose target date has now arrived, so the report
# card on the left builds itself as you keep running this notebook.

import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

import forecast_lib as fl

if COMPANY not in pl.TICKERS:
    print(f"WARNING: {COMPANY} is not in predictor_lib.TICKERS -- skipping the forecast graph.")
else:
    _fc = fl.forecast_ticker(COMPANY, verbose=True)
    fl.record_forecast(COMPANY, _fc)
    _newly_graded = fl.grade_forecasts(COMPANY)
    if _newly_graded:
        print(f"Graded {_newly_graded} past prediction(s) against what actually happened.")

    _SERIES = {"Ridge": "#2a78d6", "XGBoost": "#eb6834", "LSTM": "#1baf7a",
               "ARIMA": "#eda100", "Kalman": "#e87ba4"}
    _INK, _MUTED, _GRID, _SURF = "#0b0b0b", "#898781", "#e1e0d9", "#fcfcfb"
    _HIT, _MISS = "#0ca30c", "#d03b3b"

    plt.rcParams.update({
        "figure.facecolor": _SURF, "axes.facecolor": _SURF,
        "axes.edgecolor": "#c3c2b7", "axes.linewidth": 0.8,
        "axes.labelcolor": "#52514e", "text.color": _INK,
        "xtick.color": _MUTED, "ytick.color": _MUTED,
        "grid.color": _GRID, "grid.linewidth": 0.8, "grid.linestyle": "-",
        "font.family": "sans-serif", "font.size": 10, "figure.dpi": 110,
    })

    # Everything on this chart is shown in USD, converted at a single current
    # spot rate applied uniformly to past and future alike (no historical FX
    # series is fetched) -- "roughly what it was worth in USD", not an
    # FX-adjusted return series. For a USD company (AAPL, IBM) this is a no-op.
    _fx = _fc["fx_rate_to_usd"]
    _hist = _fc["raw"]["Close"].iloc[-30:] / _fx
    _paths = _fc["paths_usd"]
    _last_date, _last_price = _fc["last_date"], _fc["last_price_usd"]

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.grid(True, axis="y", zorder=0)
    ax.set_axisbelow(True)
    ax.plot(_hist.index, _hist.values, color=_MUTED, linewidth=2, zorder=2,
            label="Actual (last 30 days)")

    _graded = fl.graded_points(COMPANY, model="Ensemble")
    _hit = _miss = 0
    if not _graded.empty:
        _g = _graded[_graded["target_date"] >= _hist.index[0]].copy()
        _g["predicted_price"] = _g["predicted_price"] / _fx
        _g["actual_price"] = _g["actual_price"] / _fx
        for _flag, _colour, _marker, _lbl in [
            (True, _HIT, "o", f"past call within {fl.TOLERANCE_PCT}%"),
            (False, _MISS, "X", f"past call off by >{fl.TOLERANCE_PCT}%"),
        ]:
            _sub = _g[_g["within_tolerance"] == _flag]
            if _sub.empty:
                continue
            if _flag:
                _hit = len(_sub)
            else:
                _miss = len(_sub)
            ax.scatter(_sub["target_date"], _sub["predicted_price"], s=34, c=_colour,
                       marker=_marker, zorder=5, linewidths=1.1,
                       edgecolors=_SURF, label=_lbl)
            for _, _r in _sub.iterrows():
                ax.plot([_r["target_date"], _r["target_date"]],
                        [_r["predicted_price"], _r["actual_price"]],
                        color=_colour, linewidth=0.8, alpha=0.45, zorder=3)

    for _model, _colour in _SERIES.items():
        if _model in _paths:
            ax.plot([_last_date] + list(_paths.index),
                    [_last_price] + list(_paths[_model].values),
                    color=_colour, linewidth=2, zorder=4, label=_model)
    ax.plot([_last_date] + list(_paths.index), [_last_price] + list(_paths["Ensemble"].values),
            color=_INK, linewidth=2.6, marker="o", markersize=5.5,
            markeredgecolor=_SURF, markeredgewidth=1.2, zorder=6, label="Ensemble")
    ax.annotate(f"${_paths['Ensemble'].iloc[-1]:,.2f}",
                xy=(_paths.index[-1], _paths["Ensemble"].iloc[-1]),
                xytext=(9, 0), textcoords="offset points",
                color=_INK, fontsize=10, va="center", fontweight="bold")

    ax.axvline(_last_date, color=_GRID, linewidth=1, zorder=1)
    ax.annotate("today", xy=(_last_date, ax.get_ylim()[0]), xytext=(4, 6),
                textcoords="offset points", color=_MUTED, fontsize=8)

    _scored = ""
    if _hit + _miss:
        _scored = (f"  -  past calls shown: {_hit} within {fl.TOLERANCE_PCT}%, "
                   f"{_miss} off ({_hit / (_hit + _miss):.0%} hit rate)")
    _native = "" if _fc["currency"] == "USD" else f"  (native: {_fc['currency']}, 1 USD = {_fx:.3f} {_fc['currency']})"
    ax.set_title(f"{_fc['company']} ({COMPANY}) - {fl.FORECAST_DAYS}-day forecast, in USD{_scored}{_native}",
                 loc="left", fontsize=11.5, color=_INK, fontweight="bold", pad=12)
    ax.set_ylabel("Price (USD)")
    _span = ax.get_ylim()[1] - ax.get_ylim()[0]
    _dp = 0 if _span > 20 else (1 if _span > 2 else 2)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _, d=_dp: f"${v:,.{d}f}"))
    for _spine in ("top", "right"):
        ax.spines[_spine].set_visible(False)
    ax.legend(frameon=False, fontsize=9, ncol=4, loc="upper left")
    fig.autofmt_xdate(rotation=0, ha="center")
    plt.tight_layout()
    plt.show()

    _table = _paths.round(2)
    _table.index = [d.date() for d in _table.index]
    _native_note = "" if _fc["currency"] == "USD" else f" (native {_fc['currency']}: multiply by {_fx:.3f})"
    print(f"\nPredicted price by day, USD{_native_note}:")
    print(_table.to_string())

    _acc = fl.accuracy_by_horizon(COMPANY, model="Ensemble")
    if not _acc.empty:
        print("\nTrack record -- how accuracy decays with distance:")
        print(_acc.to_string())

    log_step(
        "Forecast Graph",
        {"ticker": COMPANY, "horizon_days": fl.FORECAST_DAYS,
         "source": "forecast_lib (Ridge/XGBoost/LSTM/ARIMA/Kalman, multi-step)"},
        {"metrics": {
            "best_model": _fc["best_model"],
            "last_price_usd": round(_fc["last_price_usd"], 4),
            "forecast_final_price_usd": round(_fc["final_price_usd"], 4),
            "native_price": round(_fc["last_price"], 4),
            "native_currency": _fc["currency"],
            "total_move_pct": round(_fc["total_return_pct"], 3),
            "direction": _fc["direction"],
            "past_calls_within_tolerance": _hit,
            "past_calls_off": _miss,
        }},
    )

