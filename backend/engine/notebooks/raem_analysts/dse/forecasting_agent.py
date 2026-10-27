# raem_analysts/dse/forecasting_agent.py -- shared by abbank-dse, batbc-dse, bpml-dse, waltonhil-dse
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 🔮 FORECASTING AGENT — 10-day price forecast, validated walk-forward against "price stays the same"
# ================================================================
# What it does (all in code, NO LLM call -> 0 tokens, nothing for Fact Guard to repair):
#   1) Four candidate models predict the cumulative log-return over the next FC_HORIZON trading days:
#        naive         : price stays the same (the baseline every other model must beat)
#        damped_trend  : EWMA of daily returns, damped (phi=0.9) -- classic trend extrapolation
#        ridge         : closed-form ridge on causal price features (momentum / trend / RSI / vol regime)
#        analog_knn    : median forward return of the K most similar past days (regime-conditional)
#   2) VALIDATE BEFORE DEPLOYMENT (same rule as the GNN and the RL overlay): walk-forward on the last FC_HOLDOUT days
#      with purged training (no overlap with the holdout outcome). A model is "VALIDATED" only if it beats naive on
#      MAE by >= 3% AND the paired loss-difference t-stat (non-overlapping samples) >= 1.28 AND direction accuracy >= 52%.
#      In earlier runs of this project ARIMA / Kalman / Ridge did NOT beat naive, so NOT_VALIDATED is a normal, honest result.
#   3) Output = one fact line for the FACT SHEET ([F#]). If validated: median + 80% range from that model's own holdout
#      errors. If not: "no forecast skill" + only a historical-error range around the last close, and agents are told
#      not to cite a direction from it.
#   4) Writes every candidate's h=1..10 predictions to forecast_log/@@TICKER@@_forecasts.csv (same schema as AAPL_forecasts.csv)
#      and back-fills actual_price / errors for old rows once the target date has passed -> a LIVE scorecard.
# Limits: ~150 holdout days = only ~15 non-overlapping 10-day outcomes, so the gate is a safety check, not proof of skill.
# Trading calendar = Sun-Thu without DSE holidays, so a target_date can be off by a day around a holiday.
# Ablation switch for the thesis: USE_FORECAST_AGENT = False -> no forecast line anywhere (old behaviour).
import os
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

USE_FORECAST_AGENT = True
FC_HORIZON = 10                # main horizon (trading days); h = 1..FC_HORIZON are logged
FC_LOOKBACK_DAYS = 900         # calendar days of price history
FC_MIN_TRAIN = 150             # min training days before the first holdout date
FC_HOLDOUT = 150               # last N dates (with known outcome) used for validation
FC_WARMUP = 60                 # feature warm-up rows dropped
FC_MAE_GATE = 0.97             # model MAE / naive MAE must be <= this
FC_T_GATE = 1.28               # one-sided ~10% paired t-test on non-overlapping samples
FC_DIR_GATE = 0.52             # direction accuracy must be >= this
FC_QUANTILES = (0.10, 0.90)    # 80% range
FC_TOL_PCT = 2.0               # "within_tolerance" = |error| <= 2% of actual
FC_RIDGE_ALPHA = 10.0
FC_KNN = 30
FC_DAMP_PHI = 0.9
FC_MODELS = ["naive", "damped_trend", "ridge", "analog_knn"]
FC_LOG_DIR = "../forecast_log" if os.path.isdir("../forecast_log") else "forecast_log"   # notebooks/ sits next to forecast_log/
FC_LOG_COLS = ["made_on", "target_date", "horizon_day", "model", "price_when_made", "predicted_price",
               "actual_price", "abs_pct_error", "within_tolerance", "direction_correct"]


def fc_features(close: pd.Series) -> np.ndarray:
    """Causal features only (rolling windows, no global statistics -> no look-ahead). [T, 6]"""
    lc = np.log(close)
    r1 = lc.diff()
    v20 = r1.rolling(20, min_periods=10).std().replace(0, np.nan)
    v60 = r1.rolling(60, min_periods=20).std().replace(0, np.nan)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14, min_periods=5).mean()
    loss = (-delta.clip(upper=0)).rolling(14, min_periods=5).mean()
    rsi = (100 - 100 / (1 + gain / loss.replace(0, np.nan))) / 100 - 0.5
    f = pd.DataFrame({
        "r1": r1 / v20,
        "r5": (lc - lc.shift(5)) / (v20 * np.sqrt(5)),
        "r20": (lc - lc.shift(20)) / (v20 * np.sqrt(20)),
        "trend": (close / close.rolling(50, min_periods=20).mean() - 1) / (v20 * np.sqrt(50)),
        "rsi": rsi * 2,
        "volreg": v20 / v60 - 1,
    }, index=close.index)
    return np.nan_to_num(f.clip(-3, 3).to_numpy(copy=True), nan=0.0)


def _fc_predict(model: str, F: np.ndarray, y: np.ndarray, tr: np.ndarray, t: int, mu: np.ndarray, h: int) -> float:
    """Predicted cumulative log-return over h days for date index t, trained only on rows `tr` (all outcomes known by t)."""
    if model == "naive":
        return 0.0
    if model == "damped_trend":
        return float(mu[t] * sum(FC_DAMP_PHI ** k for k in range(1, h + 1)))
    X, Y = F[tr], y[tr]
    if model == "ridge":
        xm, ym = X.mean(0), Y.mean()
        Xc = X - xm
        w = np.linalg.solve(Xc.T @ Xc + FC_RIDGE_ALPHA * np.eye(X.shape[1]), Xc.T @ (Y - ym))
        return float(ym + (F[t] - xm) @ w)
    if model == "analog_knn":
        sd = X.std(0)
        sd[sd < 1e-9] = 1.0
        d = (((X - F[t]) / sd) ** 2).sum(1)
        return float(np.median(Y[np.argsort(d)[:min(FC_KNN, len(d))]]))
    raise ValueError(model)


def _fc_target(lc: np.ndarray, h: int) -> np.ndarray:
    y = np.full(len(lc), np.nan)
    y[:-h] = lc[h:] - lc[:-h]
    return y


def fc_walkforward(F, lc, mu, h: int, hold: int) -> dict:
    """Walk-forward on the last `hold` dates whose h-day outcome is known. Returns per-model metrics + residuals."""
    T = len(lc)
    y = _fc_target(lc, h)
    evals = list(range(T - h - hold, T - h))                  # t such that y[t] is known
    out = {}
    for m in FC_MODELS:
        preds = []
        for t in evals:
            tr = np.arange(FC_WARMUP, t - h + 1)              # purge: train targets resolved by t
            preds.append(_fc_predict(m, F, y, tr, t, mu, h))
        preds = np.array(preds)
        act = y[evals]
        out[m] = {"pred": preds, "act": act, "err": act - preds}
    base = np.abs(out["naive"]["act"])
    base_mae = float(base.mean())
    for m in FC_MODELS:
        e = np.abs(out[m]["err"])
        out[m]["mae_pct"] = round(float(e.mean()) * 100, 3)
        out[m]["mae_ratio"] = round(float(e.mean() / base_mae), 3) if base_mae > 0 else np.nan
        nz = out[m]["pred"] != 0
        out[m]["dir_acc"] = round(float((np.sign(out[m]["pred"][nz]) == np.sign(out[m]["act"][nz])).mean()), 3) if nz.sum() >= 10 else np.nan
        d = (base - e)[::h]                                   # non-overlapping samples only
        out[m]["n_indep"] = int(len(d))
        out[m]["t_stat"] = round(float(d.mean() / (d.std(ddof=1) / np.sqrt(len(d)))), 2) if len(d) >= 5 and d.std(ddof=1) > 1e-12 else 0.0
        out[m]["validated"] = bool(m != "naive" and np.isfinite(out[m]["mae_ratio"]) and out[m]["mae_ratio"] <= FC_MAE_GATE
                                   and out[m]["t_stat"] >= FC_T_GATE and np.isfinite(out[m]["dir_acc"]) and out[m]["dir_acc"] >= FC_DIR_GATE)
    return out


def _fc_target_dates(last_date: pd.Timestamp, n: int):
    return list(pd.bdate_range(last_date + pd.Timedelta(days=1), periods=n, freq="C", weekmask="Sun Mon Tue Wed Thu"))


def _fc_backfill_log(path: str, close: pd.Series) -> dict:
    """Fill actual_price / errors for logged rows whose target_date has a known close. Returns live-scorecard stats."""
    try:
        df = pd.read_csv(path)
    except Exception:
        return {}
    if df.empty:
        return {}
    cl = close.copy(); cl.index = pd.to_datetime(cl.index).normalize()
    tgt = pd.to_datetime(df["target_date"]).dt.normalize()
    for c in ("actual_price", "abs_pct_error"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ("within_tolerance", "direction_correct"):
        df[c] = df[c].astype(object)          # empty on fresh rows -> float64 NaN; must accept 1.0/0.0 and True/False
    for i in df.index[df["actual_price"].isna()]:
        a = cl.get(tgt[i])
        if a is None or not np.isfinite(a):
            continue
        p, w = float(df.at[i, "predicted_price"]), float(df.at[i, "price_when_made"])
        df.at[i, "actual_price"] = round(float(a), 4)
        df.at[i, "abs_pct_error"] = round(abs(p - a) / a * 100, 4)
        df.at[i, "within_tolerance"] = 1.0 if abs(p - a) / a * 100 <= FC_TOL_PCT else 0.0
        df.at[i, "direction_correct"] = bool(np.sign(p - w) == np.sign(a - w)) if p != w else False
    df[FC_LOG_COLS].to_csv(path, index=False)
    r = df[(df["horizon_day"] == FC_HORIZON) & df["actual_price"].notna()]
    stats = {}
    for m, g in r.groupby("model"):
        stats[m] = {"n": int(len(g)), "mae_pct": round(float(g["abs_pct_error"].mean()), 3)}
    return stats


def _fc_write_log(path: str, rows: list):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    new = pd.DataFrame(rows, columns=FC_LOG_COLS)
    if os.path.exists(path):
        try:
            old = pd.read_csv(path)
            old = old[~old["made_on"].isin(new["made_on"].unique())]      # re-running the same day replaces that day's rows
            new = pd.concat([old, new], ignore_index=True)
        except Exception:
            pass
    new[FC_LOG_COLS].to_csv(path, index=False)


def run_forecast_agent(company: str, as_of: str) -> dict:
    res = {"company": company, "as_of": as_of, "status": "SKIPPED", "validated": False, "fact": "", "report": ""}
    if not USE_FORECAST_AGENT:
        res["report"] = "🔮 Forecast agent disabled (USE_FORECAST_AGENT=False)."
        return res
    start = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=FC_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    try:
        df = fetch_dse_ohlcv_range(company, start, as_of)
    except Exception as e:
        res["report"] = f"🔮 Forecast skipped: price fetch failed ({str(e)[:100]})"
        res["fact"] = "10-day forecast: not available (price fetch failed); do not invent one"
        return res
    close = df["Close"].where(df["Close"] > 0).dropna() if df is not None and not df.empty else pd.Series(dtype=float)
    need = FC_MIN_TRAIN + FC_HOLDOUT + FC_HORIZON + FC_WARMUP
    if len(close) < need:
        res["report"] = f"🔮 Forecast skipped: {len(close)} price rows (need >= {need})."
        res["fact"] = "10-day forecast: not available (not enough price history); do not invent one"
        return res

    close = close.astype(float)
    close.index = pd.to_datetime(close.index)
    last_date, px = close.index[-1], float(close.iloc[-1])
    lc = np.log(close.to_numpy())
    F = fc_features(close)
    mu = pd.Series(np.diff(lc, prepend=lc[0])).ewm(span=60, adjust=False).mean().to_numpy()
    T = len(close)

    # (2) validation at the main horizon
    wf = fc_walkforward(F, lc, mu, FC_HORIZON, FC_HOLDOUT)
    cand = [m for m in FC_MODELS if m != "naive"]
    best = min(cand, key=lambda m: wf[m]["mae_pct"])
    validated = wf[best]["validated"]
    use = best if validated else "naive"
    q_lo, q_hi = (float(np.quantile(wf[use]["err"], q)) for q in FC_QUANTILES)

    # (3) final predictions for h = 1..FC_HORIZON, every model, trained on all outcomes known today
    preds = {m: {} for m in FC_MODELS}
    for h in range(1, FC_HORIZON + 1):
        yh = _fc_target(lc, h)
        tr = np.arange(FC_WARMUP, T - h)
        for m in FC_MODELS:
            preds[m][h] = _fc_predict(m, F, yh, tr, T - 1, mu, h)
    h = FC_HORIZON
    pt = preds[use][h]
    med_px, lo_px, hi_px = (px * np.exp(pt + d) for d in (0.0, q_lo, q_hi))
    pct = (np.exp(pt) - 1) * 100
    w = wf[best]

    if validated:
        fact = (f"{h}-day price forecast (code, walk-forward validated on {FC_HOLDOUT} days): model {best}; median {med_px:.2f} BDT ({pct:+.2f}% vs last close {px:.2f}), "
                f"80% range {lo_px:.2f} - {hi_px:.2f}; holdout MAE {w['mae_ratio']:.2f}x the 'price stays the same' baseline, direction accuracy {w['dir_acc'] * 100:.0f}%. "
                f"Weak evidence: ~{w['n_indep']} independent outcomes")
    else:
        fact = (f"{h}-day price forecast (code): NO forecast skill -- best model {best} had holdout MAE {w['mae_ratio']:.2f}x the 'price stays the same' baseline "
                f"(needs <= {FC_MAE_GATE}, t >= {FC_T_GATE}, direction >= {FC_DIR_GATE * 100:.0f}%). Do NOT cite a forecast direction or target price. "
                f"Only a historical-error range: {h}-day 80% range {lo_px:.2f} - {hi_px:.2f} around last close {px:.2f}")

    # (4) log + live scorecard
    path = os.path.join(FC_LOG_DIR, f"{company.replace('.', '_')}_forecasts.csv")
    tdates = _fc_target_dates(last_date, FC_HORIZON)
    rows = []
    for m in FC_MODELS:
        label = {"damped_trend": "damped_trend", "analog_knn": "analog_knn"}.get(m, m)
        for hh, td in enumerate(tdates, start=1):
            rows.append({"made_on": as_of, "target_date": td.strftime("%Y-%m-%d"), "horizon_day": hh, "model": label,
                         "price_when_made": round(px, 4), "predicted_price": round(px * float(np.exp(preds[m][hh])), 4),
                         "actual_price": np.nan, "abs_pct_error": np.nan, "within_tolerance": np.nan, "direction_correct": np.nan})
    live = {}
    try:
        _fc_write_log(path, rows)
        live = _fc_backfill_log(path, close)
    except Exception as e:
        print(f"   ⚠️ forecast log not written: {str(e)[:100]}")

    # report
    lines = [f"🔮 FORECASTING AGENT — {company} | as of {as_of}  (last close {px:.2f} on {last_date.date()}, horizon {h} trading days)",
             f"   Walk-forward on the last {FC_HOLDOUT} dates ({w['n_indep']} independent {h}-day outcomes), purged training",
             f"   {'Model':14s}{'MAE % (10d)':>12s}{'vs naive':>10s}{'dir acc':>9s}{'t-stat':>8s}  gate"]
    for m in FC_MODELS:
        r = wf[m]
        lines.append(f"   {m:14s}{r['mae_pct']:12.2f}{r['mae_ratio']:10.2f}" + (f"{r['dir_acc'] * 100:8.0f}%" if np.isfinite(r["dir_acc"]) else f"{'n/a':>9s}")
                     + f"{r['t_stat']:8.2f}  " + ("PASS" if r["validated"] else ("(baseline)" if m == "naive" else "fail")))
    lines.append(f"   => {'VALIDATED: ' + best if validated else 'NOT VALIDATED (best was ' + best + ') -> baseline + historical range only'}")
    lines.append(f"   {h}-day: median {med_px:.2f} ({pct:+.2f}%), 80% range {lo_px:.2f} - {hi_px:.2f}"
                 + ("" if validated else "   [range from baseline errors, NOT a prediction]"))
    if live:
        lines.append("   Live scorecard (logged forecasts already resolved, h=10): " + ", ".join(f"{k} n={v['n']} MAE {v['mae_pct']:.2f}%" for k, v in live.items()))
    lines.append(f"   Log: {path}  (+{len(rows)} rows)")
    lines.append("   Past distribution / model skill check only -- not investment advice.")
    res.update({"status": "OK", "validated": validated, "model": best, "used": use, "last_close": round(px, 2), "median_price": round(float(med_px), 2),
                "pct": round(float(pct), 2), "lo": round(float(lo_px), 2), "hi": round(float(hi_px), 2), "fact": fact, "report": "\n".join(lines),
                "metrics": {m: {k: (None if (isinstance(v, float) and not np.isfinite(v)) else v) for k, v in wf[m].items() if k in ("mae_pct", "mae_ratio", "dir_acc", "t_stat", "n_indep", "validated")} for m in FC_MODELS},
                "live_scorecard": live, "log_path": path})
    return res


# ── RUN ──
print("=" * 60)
try:
    forecast_result = run_forecast_agent(COMPANY, TRADE_DATE)
except Exception as _e:
    forecast_result = {"status": "ERROR", "validated": False, "fact": "", "report": f"🔮 Forecast agent error: {str(_e)[:150]}"}
print(forecast_result["report"])
print("=" * 60 + "\n")
try:
    session_log["summary_table"]["forecast"] = {k: forecast_result.get(k) for k in ("status", "validated", "model", "last_close", "median_price", "pct", "lo", "hi")}
except Exception:
    pass
log_step("Forecasting Agent", {"company": COMPANY, "date": TRADE_DATE, "horizon": FC_HORIZON},
         {"forecast": {k: v for k, v in forecast_result.items() if k not in ("report",)}, "report": forecast_result.get("report", "")})

