"""Forecast agent + reviewer agent -- the last step after forecast_lib.

forecast_lib.forecast_ticker() trains every model. This module sits on top of
that result and answers one practical question: "what will the price be over
the next 7 trading days?" in two steps, both code-only (no LLM, no Ollama):

  1. forecast_agent(fcast)         picks the 3 best base models for the ticker and
                                   averages their 7-day paths into one forecast.
  2. reviewer_agent(fcast, plan)   audits that forecast, and where a check fails
                                   it changes the forecast and says what it changed.

"Best" means the lowest error on the common out-of-sample holdout dates (the
same ranking forecast_lib uses for the Ensemble), so nothing here looks at the
future. The reviewer's calibration step reads the same holdout, so it adds one
fitted number (a shrink factor) and no more.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import forecast_lib as fl
import predictor_lib as pl

N_MEMBERS = 3
SHRINK_GRID = np.round(np.arange(0.0, 1.0001, 0.1), 2)
SHRINK_MIN_GAIN = 0.02      # shrink only if it cuts the holdout MAE by at least 2%
PLAUSIBLE_PCTL = 95         # a move beyond this percentile of past same-horizon moves is clipped
BAND_PCTL = (10, 90)        # price range = residual quantiles from the holdout
STALE_DAYS = 5              # last price older than this (calendar days) is reported as stale
MIN_BAND_ORIGINS = 20

_KEY_OF = {label: key for key, label in fl.MODEL_LABELS.items()}


def _actuals(fcast: dict) -> pd.DataFrame:
    """What really happened after each forecast origin (target_h1..h7 returns)."""
    return fl.make_features(fcast["raw"])[fl.TARGETS].dropna()


def _ranked_base_models(fcast: dict) -> pd.DataFrame:
    """Base models with holdout predictions, best first. Uses the fusion step's
    ranking (all models scored on the same dates) when it exists."""
    results = fcast["results"]
    ranking = fcast["fusion_report"].attrs.get("ranking") if len(fcast["fusion_report"]) else None
    if ranking is not None and len(ranking):
        ranked = ranking[[_KEY_OF.get(m) in results for m in ranking.index]].copy()
        if len(ranked):
            return ranked
    # fusion could not run: fall back to each model's own holdout score
    sb = fcast["scoreboard"]
    sb = sb[[(_KEY_OF.get(m) in results and _KEY_OF[m] in fl.BASE_MODELS) for m in sb.index]]
    return (sb.rename(columns={"mean_mae": "holdout_mae"})[["holdout_mae", "mae_vs_naive"]]
            .sort_values("holdout_mae"))


def _member_frame(fcast: dict, labels: list) -> pd.DataFrame:
    """Day-1..7 predicted cumulative returns, one row per model."""
    return pd.DataFrame({m: np.asarray(fcast["results"][_KEY_OF[m]]["path"], dtype=float) for m in labels},
                        index=range(1, fl.FORECAST_DAYS + 1)).T


def forecast_agent(fcast: dict, n: int = N_MEMBERS) -> dict:
    """Pick the n best base models and average their paths."""
    ranked = _ranked_base_models(fcast)
    if len(ranked) < 1:
        raise ValueError("no base model has holdout results to rank")
    members = list(ranked.index[:n])
    rets = _member_frame(fcast, members)
    return {"members": members, "ranked": ranked, "member_returns": rets,
            "path_ret": rets.mean(axis=0).values, "shrink": 1.0, "band_ret": None, "log": []}


def _holdout_combo(fcast: dict, members: list):
    """Members' averaged out-of-sample predictions vs the actual returns, on the
    dates every member has a prediction for."""
    frames = [fcast["results"][_KEY_OF[m]].get("holdout") for m in members]
    if any(f is None for f in frames):
        return None, None
    actual = _actuals(fcast)
    idx = actual.index
    for f in frames:
        idx = idx.intersection(f.dropna().index)
    if len(idx) < 10:
        return None, None
    pred = np.mean([f.loc[idx, fl.TARGETS].values for f in frames], axis=0)
    return pred, actual.loc[idx].values


def _hist_move_limits(close: pd.Series) -> np.ndarray:
    """Per-horizon cap: the PLAUSIBLE_PCTL percentile of |h-day return| over the last ~2 years."""
    c = close.iloc[-520:]
    return np.array([np.nanpercentile(np.abs(c.pct_change(h).dropna()), PLAUSIBLE_PCTL)
                     for h in range(1, fl.FORECAST_DAYS + 1)])


def reviewer_agent(fcast: dict, plan: dict) -> dict:
    """Audit the forecast agent's plan. Each check ends PASS, FIXED (the forecast was
    changed) or WARN (reported, not changed). Returns a new plan; the input is untouched."""
    ticker, last_price = fcast["ticker"], fcast["last_price"]
    ranked = plan["ranked"]
    members = list(plan["members"])
    log = []

    def note(check, status, detail):
        log.append({"check": check, "status": status, "detail": detail})

    # 1. data freshness ---------------------------------------------------------
    try:
        gap = (pd.Timestamp(pl.last_final_session(ticker)) - fcast["last_date"]).days
        if gap > STALE_DAYS:
            note("Data freshness", "WARN",
                 f"last price is {fcast['last_date'].date()}, {gap} days behind the last finished session "
                 f"-- the forecast starts from an old price.")
        else:
            note("Data freshness", "PASS", f"last price {fcast['last_date'].date()} ({gap} day(s) behind the latest session).")
    except Exception as e:
        note("Data freshness", "WARN", f"could not check ({type(e).__name__}).")

    # 2. member paths are usable -------------------------------------------------
    def usable(m):
        r = np.asarray(fcast["results"][_KEY_OF[m]]["path"], dtype=float)
        return bool(np.all(np.isfinite(r)) and np.all(1 + r > 0))

    bad = [m for m in members if not usable(m)]
    if bad:
        spare = [m for m in ranked.index if m not in members and usable(m)]
        for m in bad:
            members.remove(m)
            if spare:
                members.append(spare.pop(0))
        note("Path integrity", "FIXED", f"{', '.join(bad)} gave NaN or non-positive prices; replaced with {', '.join(members)}.")
    else:
        note("Path integrity", "PASS", "all member paths are finite and positive.")

    # 3. members must beat "no change" ------------------------------------------
    mvn = ranked["mae_vs_naive"]
    losers = [m for m in members if mvn.get(m, np.inf) >= 1.0]
    if losers:
        better = [m for m in ranked.index if m not in members and mvn.get(m, np.inf) < 1.0 and usable(m)]
        swapped = []
        for m in losers:
            if better:
                new = better.pop(0)
                members[members.index(m)] = new
                swapped.append(f"{m} -> {new}")
        if swapped:
            note("Members beat naive", "FIXED",
                 f"{', '.join(losers)} did not beat 'no change' on the holdout; swapped {', '.join(swapped)}.")
        else:
            note("Members beat naive", "WARN",
                 f"{', '.join(losers)} did not beat 'no change' on the holdout and no better model is available.")
    else:
        note("Members beat naive", "PASS", "all 3 members beat 'no change' on the holdout "
             f"(mae_vs_naive {', '.join(f'{m} {mvn[m]:.2f}' for m in members)}).")

    rets = _member_frame(fcast, members)
    path = rets.mean(axis=0).values
    final_move = path[-1]

    # 4. members agree on direction ---------------------------------------------
    votes = np.sign(rets.iloc[:, -1].values)
    split_vote = len(set(votes[votes != 0])) > 1
    if split_vote:
        note("Direction agreement", "WARN",
             "members disagree on the day-7 direction (" +
             ", ".join(f"{m} {'UP' if v > 0 else 'DOWN' if v < 0 else 'FLAT'}" for m, v in zip(members, votes)) +
             ") -- low confidence in the direction.")
    else:
        note("Direction agreement", "PASS", "all members point the same way on day 7.")

    # 5. calibration: does the average need shrinking toward 'no change'? --------
    shrink, holdout_mvn, dir_acc = 1.0, np.nan, np.nan
    pred, actual = _holdout_combo(fcast, members)
    if pred is not None:
        naive_mae = float(np.mean(np.abs(actual)))
        maes = {s: float(np.mean(np.abs(actual - s * pred))) for s in SHRINK_GRID}
        full = maes[1.0]
        best_s = min(maes, key=maes.get)
        holdout_mvn = full / naive_mae
        dir_acc = float(np.mean(np.sign(pred[:, -1]) == np.sign(actual[:, -1])))
        if best_s < 1.0 and (full - maes[best_s]) / full >= SHRINK_MIN_GAIN:
            shrink = float(best_s)
            note("Calibration", "FIXED",
                 f"the 3-model average is over-confident on the holdout (error vs naive {full / naive_mae:.2f}); "
                 f"scaled every move by {shrink:.1f}, which cuts holdout error {100 * (full - maes[best_s]) / full:.0f}% "
                 f"(vs naive {maes[best_s] / naive_mae:.2f}).")
        else:
            note("Calibration", "PASS",
                 f"holdout error vs naive {full / naive_mae:.2f} over {len(pred)} dates; no shrink improves it by "
                 f"{SHRINK_MIN_GAIN:.0%} or more.")
        path = path * shrink
    else:
        note("Calibration", "WARN", "members share too few holdout dates to calibrate; forecast left unscaled.")

    # 6. plausibility: no move larger than history makes believable --------------
    limits = _hist_move_limits(fcast["raw"]["Close"])
    clipped = np.clip(path, -limits, limits)
    if not np.allclose(clipped, path):
        days = [str(d + 1) for d in np.flatnonzero(~np.isclose(clipped, path))]
        note("Plausible range", "FIXED",
             f"day(s) {', '.join(days)} moved more than the {PLAUSIBLE_PCTL}th percentile of past same-horizon "
             f"moves (day 7 limit +/-{limits[-1] * 100:.1f}%); clipped to that limit.")
        path = clipped
    else:
        note("Plausible range", "PASS", f"day-7 move {path[-1] * 100:+.2f}% is inside the +/-{limits[-1] * 100:.1f}% "
             f"historical {PLAUSIBLE_PCTL}th-percentile range.")

    # 7. price band from the holdout's own forecast errors -------------------------
    band = None
    if pred is not None and len(pred) >= MIN_BAND_ORIGINS:
        resid = actual - shrink * pred
        band = np.column_stack([np.percentile(resid, BAND_PCTL[0], axis=0),
                                np.percentile(resid, BAND_PCTL[1], axis=0)])
        note("Price band", "PASS", f"{BAND_PCTL[0]}-{BAND_PCTL[1]}% range built from {len(pred)} out-of-sample forecast errors.")
    else:
        note("Price band", "WARN", "too few holdout dates for a price range.")

    # 8. live track record ------------------------------------------------------
    try:
        rep = fl.hit_rate_report(ticker)
        note("Live track record", "PASS" if rep["n_forecasts"] else "WARN", rep["verdict"])
    except Exception as e:
        note("Live track record", "WARN", f"could not read forecast_log ({type(e).__name__}).")

    # overall confidence ---------------------------------------------------------
    warns = sum(1 for r in log if r["status"] == "WARN" and r["check"] in
                ("Direction agreement", "Members beat naive", "Calibration"))
    unproven = np.isfinite(holdout_mvn) and holdout_mvn >= 1.0 and shrink == 1.0   # no better than naive, nothing to shrink
    if split_vote or unproven or shrink <= 0.3:
        confidence = "LOW"
    elif warns == 0 and shrink >= 0.7 and np.isfinite(holdout_mvn) and holdout_mvn < 1.0:
        confidence = "HIGH"
    else:
        confidence = "MEDIUM"

    out = dict(plan)
    out.update({"members": members, "member_returns": rets, "path_ret": path, "shrink": shrink,
                "band_ret": band, "log": log, "confidence": confidence,
                "holdout_mae_vs_naive": holdout_mvn, "holdout_dir_acc": dir_acc,
                "changed": any(r["status"] == "FIXED" for r in log)})
    return out


def forecast_table(fcast: dict, plan: dict) -> pd.DataFrame:
    """Day-by-day price table: the three members, the reviewed forecast and its range."""
    last = fcast["last_price"]
    dec = 4 if fcast["currency"] == "BDT" else 2
    dates = fcast["paths"].index
    t = pd.DataFrame(index=pd.Index([d.date() for d in dates], name="date"))
    t["day"] = range(1, fl.FORECAST_DAYS + 1)
    for m in plan["members"]:
        t[m] = (last * (1 + plan["member_returns"].loc[m].values)).round(dec)
    price = last * (1 + plan["path_ret"])
    t["FORECAST"] = price.round(dec)
    t["change_%"] = ((price / last - 1) * 100).round(2)
    if plan["band_ret"] is not None:
        t["low"] = (last * (1 + plan["path_ret"] + plan["band_ret"][:, 0])).round(dec)
        t["high"] = (last * (1 + plan["path_ret"] + plan["band_ret"][:, 1])).round(dec)
    return t


def plot_agent_forecast(fcast: dict, plan: dict, history_days: int = 30):
    import matplotlib.pyplot as plt

    last, dates = fcast["last_price"], fcast["paths"].index
    hist = fcast["raw"]["Close"].iloc[-history_days:]
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(hist.index, hist.values, color="#444", lw=1.8, label="actual close")
    x = [hist.index[-1], *dates]
    for m in plan["members"]:
        ax.plot(x, [last, *(last * (1 + plan["member_returns"].loc[m].values))], ls="--", lw=1, alpha=.8, label=m)
    price = last * (1 + plan["path_ret"])
    if plan["band_ret"] is not None:
        lo = last * (1 + plan["path_ret"] + plan["band_ret"][:, 0])
        hi = last * (1 + plan["path_ret"] + plan["band_ret"][:, 1])
        ax.fill_between(dates, lo, hi, color="#1f77b4", alpha=.15,
                        label=f"{BAND_PCTL[0]}-{BAND_PCTL[1]}% range")
    ax.plot(x, [last, *price], color="#1f77b4", lw=2.6, marker="o", label="reviewed forecast")
    for d, p in zip(dates, price):
        ax.annotate(f"{p:.2f}", (d, p), textcoords="offset points", xytext=(0, 8), ha="center", fontsize=8)
    ax.set_title(f"{fcast['ticker']} -- 7-day forecast, best 3 models, reviewed "
                 f"(confidence {plan.get('confidence', '?')})")
    ax.set_ylabel(fcast["currency"])
    ax.grid(alpha=.3)
    ax.legend(loc="best", fontsize=8)
    fig.autofmt_xdate()
    return fig


def run_forecast_and_review(fcast: dict, show: bool = True) -> dict:
    """Run both agents on a forecast_lib result; print and plot the outcome."""
    plan0 = forecast_agent(fcast)
    plan = reviewer_agent(fcast, plan0)
    table = forecast_table(fcast, plan)
    if show:
        import matplotlib.pyplot as plt
        from IPython.display import display

        dec = 4 if fcast["currency"] == "BDT" else 2
        print(f"FORECAST AGENT -- best 3 base models for {fcast['ticker']} on the common holdout "
              f"(lowest error first):")
        display(plan0["ranked"].head(6).round(4))
        print(f"Picked: {', '.join(plan0['members'])}   (plain average of their 7-day paths)\n")
        print("REVIEWER AGENT -- checks:")
        display(pd.DataFrame(plan["log"]).set_index("check"))
        if plan["members"] != plan0["members"]:
            print(f"Members after review: {', '.join(plan['members'])} (were {', '.join(plan0['members'])})")
        before = fcast["last_price"] * (1 + plan0["path_ret"][-1])
        after = fcast["last_price"] * (1 + plan["path_ret"][-1])
        print(f"Day-7 price before review {before:.{dec}f}  ->  after review {after:.{dec}f}"
              + ("   (changed)" if plan["changed"] else "   (unchanged)"))
        print(f"\nFINAL 7-DAY PRICE FORECAST ({fcast['currency']}), last close "
              f"{fcast['last_price']:.{dec}f} on {fcast['last_date'].date()}:")
        display(table)
        move = plan["path_ret"][-1] * 100
        print(f"Day 7: {fcast['last_price'] * (1 + plan['path_ret'][-1]):.{dec}f} ({move:+.2f}%) -- "
              f"confidence {plan['confidence']}"
              + (f", holdout error vs naive {plan['holdout_mae_vs_naive']:.2f}" if np.isfinite(plan["holdout_mae_vs_naive"]) else "")
              + (f", holdout day-7 direction {plan['holdout_dir_acc']:.0%}" if np.isfinite(plan["holdout_dir_acc"]) else "")
              + f" (always-UP would score {fcast['always_up_holdout_acc']:.0%}).")
        plot_agent_forecast(fcast, plan)
        plt.show()
    return {"plan_before_review": plan0, "plan": plan, "table": table}
