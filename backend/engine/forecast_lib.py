"""7-day price-path forecasting -- the second half of the system.

predictor_lib.py answers "buy or sell today?" with a single cumulative
HORIZON-day return. This module answers a different question: "what does the
price path look like over the next 7 days, day by day?"

Two differences from predictor_lib:

  * Data: the static 2-year snapshot in data/*.csv is combined with the daily
    observations the trading notebooks accumulate in daily_data/*.csv, so the
    series keeps growing as the daily system runs instead of being frozen at
    the snapshot's end date.

  * Models: every model here is genuinely multi-step -- it predicts day+1,
    day+2, ... day+7 as separate values, rather than one lump-sum return
    spread over a week. That is what makes a real trajectory chart possible.
"""

from __future__ import annotations

import glob as _glob
import os

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.compose import TransformedTargetRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Lasso, Ridge
from sklearn.metrics import mean_absolute_error
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.statespace.structural import UnobservedComponents
from xgboost import XGBRegressor

import predictor_lib as pl

try:
    import advanced_forecast as af
    _HAS_ADVANCED = True
except Exception:  # missing optional dependency -- run without N-BEATS/regime/ARIMAX
    _HAS_ADVANCED = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DAILY_DIR = os.path.join(BASE_DIR, "daily_data")
NOTEBOOK_DIR = os.path.join(BASE_DIR, "notebooks")

FORECAST_DAYS = 7        # how many trading days ahead the path extends
SEQ_LEN = 20             # LSTM lookback window
SPLIT_RATIO = 0.85

# Same features as predictor_lib minus vol_chg: daily log rows carry a close
# price but no volume, so a volume feature would go NaN on exactly the newest
# (most relevant) rows.
FEATURES = ["lag_ret_1", "lag_ret_2", "lag_ret_3", "lag_ret_5", "lag_ret_10",
            "sma_10", "sma_50", "vol_10", "vol_30", "rsi_14"]

# One definition of the daily_data schema and readers, shared with
# predictor_lib -- the daily system reads the same file to score the LLM
# panel's past calls, so the two must not drift apart.
DAILY_COLUMNS = pl.DAILY_COLUMNS
load_daily = pl.load_daily


# --------------------------------------------------------------------------
# Daily observations accumulated by the trading notebooks
# --------------------------------------------------------------------------

def daily_path(ticker: str) -> str:
    os.makedirs(DAILY_DIR, exist_ok=True)
    return pl.daily_path(ticker)


def append_daily_observation(ticker: str, trade_date: str, close: float, **fields) -> pd.DataFrame:
    """Record one day's live market observation. Re-running on the same date
    replaces that date's row rather than duplicating it, so running the daily
    notebook twice in one day does not double-count the day."""
    row = {"date": str(trade_date), "ticker": ticker, "close": float(close),
           "recorded_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")}
    row.update({k: v for k, v in fields.items() if k in DAILY_COLUMNS})

    df = load_daily(ticker)
    df = df[df["date"].astype(str) != str(trade_date)]
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df = df.reindex(columns=DAILY_COLUMNS).sort_values("date")
    df.to_csv(daily_path(ticker), index=False)
    return df


def sync_from_trading_logs(ticker: str) -> int:
    """Seed daily_data/ from whatever the trading notebooks have already logged.

    The notebooks write one row per session to notebooks/trading_log_*.csv,
    including the live close and indicators their Market Analyst saw that day.
    Several sessions can share a trade_date; the last one wins. Returns the
    number of dates imported."""
    imported = 0
    for path in _glob.glob(os.path.join(NOTEBOOK_DIR, "trading_log_*.csv")):
        try:
            log = pd.read_csv(path)
        except (pd.errors.EmptyDataError, pd.errors.ParserError):
            continue
        if "company" not in log or "market_latest_close" not in log:
            continue

        mine = log[log["company"].astype(str) == ticker].dropna(subset=["market_latest_close"])
        if mine.empty:
            continue
        mine = mine.sort_values("session_start").drop_duplicates("trade_date", keep="last")

        for _, r in mine.iterrows():
            append_daily_observation(
                ticker, r["trade_date"], r["market_latest_close"],
                rsi=r.get("rsi"), macd=r.get("macd"),
                close_50_sma=r.get("close_50_sma"), boll_ub=r.get("boll_ub"),
                boll_lb=r.get("boll_lb"), final_signal=r.get("final_signal"),
            )
            imported += 1
    return imported


CLOSE_MATCH_TOLERANCE = 0.0005   # 0.05%: within this, two closes are the same price


def merge_report(ticker: str, sync: bool = True) -> dict:
    """How data/ and daily_data/ line up for this ticker: where they agree,
    where they disagree, and which daily rows are genuinely new days."""
    if sync:
        sync_from_trading_logs(ticker)

    hist = pl.load_data_csv(ticker)
    daily = load_daily(ticker)
    out = {"ticker": ticker, "snapshot_rows": len(hist), "daily_rows": len(daily),
           "agree": [], "disagree": [], "new_days": [], "stale_repeats": [], "non_trading": []}
    if daily.empty:
        return out

    daily = daily.dropna(subset=["close"]).copy()
    daily.index = pd.to_datetime(daily["date"])
    daily = daily[~daily.index.duplicated(keep="last")].sort_index()

    def same_price(a, b):
        return abs(float(a) - float(b)) / float(a) <= CLOSE_MATCH_TOLERANCE

    for d, row in daily.iterrows():
        close = float(row["close"])
        if d in hist.index:                       # both sources cover this date
            ref = float(hist.loc[d, "Close"])
            (out["agree"] if same_price(ref, close) else out["disagree"]).append(
                {"date": d.date(), "data_csv": ref, "daily_log": close})
        elif d.weekday() in pl.weekend_days(ticker):
            out["non_trading"].append({"date": d.date(), "close": close})
        elif same_price(hist["Close"].iloc[-1], close):
            # same price as the last known close, logged under a later date:
            # the notebook ran before the bell and saw yesterday's number again
            out["stale_repeats"].append(
                {"date": d.date(), "close": close, "same_as": hist.index[-1].date()})
        else:
            out["new_days"].append({"date": d.date(), "close": close})
    return out


def load_combined_history(ticker: str, sync: bool = True) -> pd.DataFrame:
    """data/ (the historical record) and daily_data/ (what each notebook run
    saw), merged on date into one series.

    The two should agree, and normally do -- data/ now tops itself up from the
    exchange feed, so it usually already contains the day a notebook logged.
    Where both cover a date, data/ wins: it is exchange-dated and carries real
    Open/High/Low/Volume, while a logged row has only a rounded close.

    A daily row is only added as a new bar when it is a weekday the snapshot
    does not have AND its close differs from the last known close. That second
    test is what catches the common case of running the notebook before the
    market closes: the log then records yesterday's price under today's date,
    which would otherwise enter the series as a fake zero-return day.
    """
    if sync:
        sync_from_trading_logs(ticker)

    hist = pl.load_data_csv(ticker).copy()
    hist["source"] = "snapshot"

    daily = load_daily(ticker)
    if daily.empty:
        return hist

    daily = daily.dropna(subset=["close"]).copy()
    daily.index = pd.to_datetime(daily["date"])
    daily = daily[~daily.index.duplicated(keep="last")].sort_index()

    fresh = daily[~daily.index.isin(hist.index)]      # date-wise merge: data/ wins
    fresh = fresh[fresh.index > hist.index[-1]]
    fresh = fresh[~fresh.index.weekday.isin(pl.weekend_days(ticker))]

    if not fresh.empty:
        last_close = float(hist["Close"].iloc[-1])
        # compare with a tolerance, not exactly: the trading log rounds to 2
        # decimals, so 229.55 and 229.5500030517578 are the same observation
        # and an == test silently lets the duplicate through
        keep = [d for d in fresh.index
                if abs(float(fresh.loc[d, "close"]) - last_close) / last_close > CLOSE_MATCH_TOLERANCE]
        fresh = fresh.loc[keep]

    if fresh.empty:
        return hist

    # A logged day gives a close but no OHLCV; carry the close across the
    # price columns so downstream code sees a consistent frame, and mark the
    # row so its provenance stays visible.
    add = pd.DataFrame(index=fresh.index)
    for col in ["Open", "High", "Low", "Close"]:
        add[col] = fresh["close"].astype(float)
    add["Volume"] = np.nan
    add["source"] = "daily_log"

    combined = pd.concat([hist, add])
    combined["Volume"] = combined["Volume"].ffill()
    return combined[~combined.index.duplicated(keep="last")].sort_index()


# --------------------------------------------------------------------------
# Features and multi-horizon targets
# --------------------------------------------------------------------------

def make_features(df: pd.DataFrame) -> pd.DataFrame:
    """Same construction as predictor_lib.make_features, but with one target
    per forecast day (target_h1 ... target_h7) instead of a single lump-sum
    HORIZON-day return."""
    d = pl.price_features(df)      # shared with the daily system
    for h in range(1, FORECAST_DAYS + 1):
        d[f"target_h{h}"] = d["Close"].shift(-h) / d["Close"] - 1
    return d


TARGETS = [f"target_h{h}" for h in range(1, FORECAST_DAYS + 1)]


def _score_path(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    """Per-horizon MAE and direction accuracy, plus their means."""
    per_step = []
    for h in range(y_true.shape[1]):
        per_step.append({
            "horizon_day": h + 1,
            "mae": float(mean_absolute_error(y_true[:, h], y_pred[:, h])),
            "direction_acc": pl.direction_acc(y_true[:, h], y_pred[:, h]),
        })
    steps = pd.DataFrame(per_step)
    return {
        "per_step": steps,
        "mae": float(steps["mae"].mean()),
        "direction_acc": float(steps["direction_acc"].mean()),
    }


# --------------------------------------------------------------------------
# Models -- each predicts all FORECAST_DAYS steps
# --------------------------------------------------------------------------

RIDGE_ALPHA_GRID = [0.1, 1.0, 10.0, 100.0]
XGB_GRID = [{"max_depth": d, "learning_rate": lr, "n_estimators": n,
             "subsample": 0.8, "colsample_bytree": 0.8, "random_state": 42}
            for d in [2, 3] for lr in [0.03, 0.08] for n in [200, 400]]
LSTM_GRID = [{"hidden": h, "lr": lr} for h in [32, 64] for lr in [1e-3, 5e-4]]
ARIMA_GRID = [(1, 1, 0), (2, 1, 0), (1, 1, 1), (2, 1, 1), (5, 1, 0)]
KALMAN_GRID = [
    {"name": "local level", "kwargs": {"level": "local level"}},
    {"name": "local linear trend", "kwargs": {"level": "local linear trend"}},
    {"name": "smooth trend", "kwargs": {"level": "smooth trend"}},
]


def _tune_on_inner_split(labeled: pd.DataFrame, split: int, grid: list, build) -> tuple:
    """Pick hyperparameters on a validation slice carved from the TRAINING
    portion. The holdout (everything past `split`) is never touched here --
    scoring the grid on it and then reporting accuracy from it would make the
    reported number optimistically biased, which is the whole thing the
    project's tuning methodology is supposed to avoid."""
    inner = int(split * SPLIT_RATIO)
    # Embargo: a row dated t carries target_h7 = close[t+7]/close[t] - 1, so the
    # last FORECAST_DAYS training rows have labels reaching into the validation
    # window. Dropping them keeps the two sides genuinely disjoint.
    fit_end = inner - FORECAST_DAYS
    X_in, X_val = labeled[FEATURES].iloc[:fit_end], labeled[FEATURES].iloc[inner:split]
    y_val = labeled[TARGETS].iloc[inner:split].values

    best_params, best_mae = grid[0], np.inf
    for params in grid:
        preds = np.column_stack([
            build(params).fit(X_in, labeled[t].iloc[:fit_end]).predict(X_val) for t in TARGETS
        ])
        mae = float(np.mean(np.abs(y_val - preds)))
        if mae < best_mae:
            best_mae, best_params = mae, params
    return best_params, best_mae


def _fit_direct_multihorizon(labeled, split, last_row, params, build) -> dict:
    """Refit on the training portion (minus the embargoed tail whose labels
    overlap the holdout), score once on the untouched holdout, then refit on
    everything to read off the forward path."""
    fit_end = split - FORECAST_DAYS
    X_tr, X_te = labeled[FEATURES].iloc[:fit_end], labeled[FEATURES].iloc[split:]
    holdout = np.column_stack([
        build(params).fit(X_tr, labeled[t].iloc[:fit_end]).predict(X_te) for t in TARGETS
    ])
    path = np.array([
        build(params).fit(labeled[FEATURES], labeled[t]).predict(last_row[FEATURES])[0]
        for t in TARGETS
    ])
    return {"scores": _score_path(labeled[TARGETS].iloc[split:].values, holdout),
            "path": path, "params": params,
            "holdout": _holdout_frame(holdout, labeled.index[split:])}


def _holdout_frame(preds: np.ndarray, dates) -> pd.DataFrame:
    """Out-of-sample predicted returns, one row per forecast origin date. The
    fusion models are fit on these, so every base model returns one."""
    return pd.DataFrame(np.asarray(preds, dtype=float), index=pd.DatetimeIndex(dates), columns=TARGETS)


def run_ridge(labeled: pd.DataFrame, split: int, last_row: pd.DataFrame) -> dict:
    """Direct multi-horizon strategy: one linear model per forecast day."""
    def build(p):
        return Ridge(alpha=p["alpha"])

    grid = [{"alpha": a} for a in RIDGE_ALPHA_GRID]
    best, _ = _tune_on_inner_split(labeled, split, grid, build)
    return _fit_direct_multihorizon(labeled, split, last_row, best, build)


def run_xgboost(labeled: pd.DataFrame, split: int, last_row: pd.DataFrame) -> dict:
    """Direct multi-horizon strategy: one gradient-boosted model per forecast day."""
    def build(p):
        return XGBRegressor(**p)

    best, _ = _tune_on_inner_split(labeled, split, XGB_GRID, build)
    return _fit_direct_multihorizon(labeled, split, last_row, best, build)


class MultiStepLSTM(nn.Module):
    """One LSTM whose head emits all FORECAST_DAYS horizons at once, so the
    steps are predicted jointly rather than by 7 unrelated models."""

    def __init__(self, n_features: int, hidden: int = 32, n_out: int = FORECAST_DAYS):
        super().__init__()
        self.lstm = nn.LSTM(n_features, hidden, num_layers=1, batch_first=True)
        self.head = nn.Linear(hidden, n_out)

    def forward(self, x):
        out, _ = self.lstm(x)
        return self.head(out[:, -1, :])


def _build_sequences(scaled: np.ndarray, targets: np.ndarray, seq_len: int, with_rows: bool = False):
    """Windows of seq_len rows ending just before row i, labelled with row i's
    targets. with_rows=True also returns each window's row i (its forecast origin)."""
    X, y, rows = [], [], []
    for i in range(seq_len, len(scaled)):
        if np.isnan(targets[i]).any():
            continue
        X.append(scaled[i - seq_len:i])
        y.append(targets[i])
        rows.append(i)
    X, y = np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)
    return (X, y, np.array(rows)) if with_rows else (X, y)


def _train_lstm(X_tr, y_tr, n_features, hidden, lr, epochs, seed=42):
    torch.manual_seed(seed)
    model = MultiStepLSTM(n_features=n_features, hidden=hidden)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.MSELoss()
    xb, yb = torch.tensor(X_tr), torch.tensor(y_tr)
    model.train()
    for _ in range(epochs):
        opt.zero_grad()
        loss = loss_fn(model(xb), yb)
        loss.backward()
        opt.step()
    model.eval()
    return model


def run_lstm(feat: pd.DataFrame, epochs: int = 150) -> dict:
    """Multi-output LSTM over a SEQ_LEN-day window of features."""
    usable = feat.dropna(subset=FEATURES)
    scaler = StandardScaler().fit(usable[FEATURES])
    scaled = scaler.transform(usable[FEATURES])
    targets = usable[TARGETS].values

    X, y, rows = _build_sequences(scaled, targets, SEQ_LEN, with_rows=True)
    if len(X) < 60:
        raise ValueError(f"only {len(X)} usable sequences -- not enough history to train")

    split = int(len(X) * SPLIT_RATIO)
    X_tr, y_tr, X_te, y_te = X[:split], y[:split], X[split:], y[split:]

    # Tune on an inner validation slice of the training portion only -- the
    # holdout below has to stay unseen for its score to mean anything.
    inner = int(split * SPLIT_RATIO)
    X_in, y_in = X_tr[:inner - FORECAST_DAYS], y_tr[:inner - FORECAST_DAYS]  # embargo
    X_val, y_val = X_tr[inner:], y_tr[inner:]

    best_params, best_mae = LSTM_GRID[0], np.inf
    for params in LSTM_GRID:
        m = _train_lstm(X_in, y_in, len(FEATURES), params["hidden"], params["lr"], epochs)
        with torch.no_grad():
            preds = m(torch.tensor(X_val)).numpy()
        mae = float(np.mean(np.abs(y_val - preds)))
        if mae < best_mae:
            best_mae, best_params = mae, params

    scored = _train_lstm(X_tr[:split - FORECAST_DAYS], y_tr[:split - FORECAST_DAYS],
                         len(FEATURES), best_params["hidden"], best_params["lr"], epochs)
    with torch.no_grad():
        holdout = scored(torch.tensor(X_te)).numpy()

    # refit on everything, then read the path off the most recent window
    final = _train_lstm(X, y, len(FEATURES), best_params["hidden"], best_params["lr"], epochs)
    last_window = scaled[-SEQ_LEN:][None, :, :].astype(np.float32)
    with torch.no_grad():
        path = final(torch.tensor(last_window)).numpy()[0]

    return {"scores": _score_path(y_te, holdout), "path": path, "params": best_params,
            "holdout": _holdout_frame(holdout, usable.index[rows[split:]])}


def _walk_forward(log_close: pd.Series, split: int, fit_at) -> tuple:
    """Score a natively multi-step model from many forecast origins across the
    holdout, rather than the single arbitrary origin at `split`. Returns
    (scores, holdout predictions by origin date)."""
    y_true, y_pred, dates = [], [], []
    for pos in range(split, len(log_close) - FORECAST_DAYS):
        try:
            fc = fit_at(log_close.iloc[:pos + 1].values)
        except Exception:
            continue
        base = log_close.iloc[pos]
        y_pred.append(np.exp(fc - base) - 1)
        y_true.append(np.exp(log_close.iloc[pos + 1:pos + 1 + FORECAST_DAYS].values - base) - 1)
        dates.append(log_close.index[pos])

    if not y_true:
        return {"per_step": pd.DataFrame(), "mae": np.nan, "direction_acc": np.nan}, None
    return _score_path(np.array(y_true), np.array(y_pred)), _holdout_frame(y_pred, dates)


def run_kalman(close: pd.Series, split: int) -> dict:
    """Local-level / local-linear-trend state-space model, Kalman-filtered.

    Like ARIMA it forecasts multi-step natively, but it models the price as a
    slowly drifting level (and optionally slope) updated by the filter as each
    observation arrives, instead of fixed autoregressive coefficients -- so it
    adapts to a changing trend rather than assuming a stationary one.
    """
    log_close = np.log(close)
    train = log_close.iloc[:split]

    best_spec, best_aic = KALMAN_GRID[0], np.inf
    for spec in KALMAN_GRID:
        try:
            fit = UnobservedComponents(train.values, **spec["kwargs"]).fit(disp=False)
        except Exception:
            continue
        if fit.aic < best_aic:
            best_aic, best_spec = fit.aic, spec

    def fit_at(series):
        m = UnobservedComponents(series, **best_spec["kwargs"]).fit(disp=False)
        return np.asarray(m.forecast(steps=FORECAST_DAYS))

    scores, holdout = _walk_forward(log_close, split, fit_at)

    fit_full = UnobservedComponents(log_close.values, **best_spec["kwargs"]).fit(disp=False)
    fc_full = np.asarray(fit_full.forecast(steps=FORECAST_DAYS))
    path = np.exp(fc_full - log_close.iloc[-1]) - 1
    return {"scores": scores, "path": path, "params": {"spec": best_spec["name"]}, "holdout": holdout}


def run_arima(close: pd.Series, split: int) -> dict:
    """ARIMA forecasts multi-step natively -- forecast(steps=7) already is a
    day-by-day path, no per-horizon refitting needed."""
    log_close = np.log(close)
    train = log_close.iloc[:split]

    # Order chosen by AIC on the training portion only -- same rule
    # predictor_lib.pick_arima_order() follows. Scoring candidate orders
    # against the first holdout days instead would leak the holdout.
    best_order, best_aic = ARIMA_GRID[0], np.inf
    for order in ARIMA_GRID:
        try:
            fit = ARIMA(train.values, order=order).fit()
        except Exception:
            continue
        if fit.aic < best_aic:
            best_aic, best_order = fit.aic, order

    # scored from many forecast origins across the holdout, not just one
    def fit_at(series):
        return np.asarray(ARIMA(series, order=best_order).fit().forecast(steps=FORECAST_DAYS))

    scores, holdout = _walk_forward(log_close, split, fit_at)

    fit_full = ARIMA(log_close.values, order=best_order).fit()
    fc_full = fit_full.forecast(steps=FORECAST_DAYS)
    path = np.exp(fc_full - log_close.iloc[-1]) - 1
    return {"scores": scores, "path": np.asarray(path), "params": {"order": best_order},
            "holdout": holdout}


# --------------------------------------------------------------------------
# Base models taken from the comparison papers in paper/
# --------------------------------------------------------------------------

LASSO_ALPHA_GRID = [1e-5, 1e-4, 1e-3]
RF_GRID = [{"max_depth": d, "min_samples_leaf": leaf} for d in [4, 8] for leaf in [5, 20]]
DNN_GRID = [{"hidden": h, "alpha": a} for h in [(31, 10, 5), (64, 32)] for a in [1e-4, 1e-2]]
WSAE_CODE_SIZES = (10, 10, 10)   # Bao et al.: stacked autoencoder layers of 10 units
WAVELET_WINDOW = 32              # trailing window each day's denoising sees -- past data only


def _tune_joint(labeled: pd.DataFrame, split: int, grid: list, build) -> dict:
    """_tune_on_inner_split for models that predict all FORECAST_DAYS targets
    with one multi-output fit (random forest, MLP) instead of one model per day."""
    inner = int(split * SPLIT_RATIO)
    fit_end = inner - FORECAST_DAYS
    best, best_mae = grid[0], np.inf
    for params in grid:
        preds = build(params).fit(labeled[FEATURES].iloc[:fit_end],
                                  labeled[TARGETS].iloc[:fit_end].values).predict(
            labeled[FEATURES].iloc[inner:split])
        mae = float(np.mean(np.abs(labeled[TARGETS].iloc[inner:split].values - preds)))
        if mae < best_mae:
            best, best_mae = params, mae
    return best


def _fit_joint_multihorizon(labeled, split, last_row, params, build) -> dict:
    fit_end = split - FORECAST_DAYS
    holdout = build(params).fit(labeled[FEATURES].iloc[:fit_end],
                                labeled[TARGETS].iloc[:fit_end].values).predict(
        labeled[FEATURES].iloc[split:])
    path = build(params).fit(labeled[FEATURES], labeled[TARGETS].values).predict(
        last_row[FEATURES])[0]
    return {"scores": _score_path(labeled[TARGETS].iloc[split:].values, holdout),
            "path": np.asarray(path), "params": params,
            "holdout": _holdout_frame(holdout, labeled.index[split:])}


def run_lasso(labeled: pd.DataFrame, split: int, last_row: pd.DataFrame) -> dict:
    """Iworiso (2023): L1-regularised regression, the paper's economically best
    model. Direct multi-horizon like run_ridge; features are standardised so a
    single alpha penalises every feature on the same scale."""
    def build(p):
        return make_pipeline(StandardScaler(), Lasso(alpha=p["alpha"], max_iter=20000))

    best, _ = _tune_on_inner_split(labeled, split, [{"alpha": a} for a in LASSO_ALPHA_GRID], build)
    return _fit_direct_multihorizon(labeled, split, last_row, best, build)


def run_random_forest(labeled: pd.DataFrame, split: int, last_row: pd.DataFrame) -> dict:
    """Krauss, Do & Huck (2016): random forest, their strongest single learner.
    One multi-output forest predicts all FORECAST_DAYS steps jointly."""
    def build(p):
        return RandomForestRegressor(n_estimators=300, max_features="sqrt", n_jobs=-1,
                                     random_state=42, **p)

    best = _tune_joint(labeled, split, RF_GRID, build)
    return _fit_joint_multihorizon(labeled, split, last_row, best, build)


def run_dnn(labeled: pd.DataFrame, split: int, last_row: pd.DataFrame) -> dict:
    """Krauss, Do & Huck (2016): feed-forward deep network (their 31-10-5
    hidden layout). Inputs and targets are standardised -- raw 7-day returns
    are ~0.01 in size, which an MLP otherwise barely moves away from zero."""
    def build(p):
        mlp = MLPRegressor(hidden_layer_sizes=p["hidden"], alpha=p["alpha"],
                           max_iter=500, random_state=42)
        return TransformedTargetRegressor(regressor=make_pipeline(StandardScaler(), mlp),
                                          transformer=StandardScaler())

    best = _tune_joint(labeled, split, DNN_GRID, build)
    return _fit_joint_multihorizon(labeled, split, last_row, best, build)


def _haar_denoise_last(window: np.ndarray, levels: int = 2) -> float:
    """Soft-threshold the Haar detail coefficients of one trailing window and
    return the denoised value of its LAST point. Applied day by day over a
    trailing window, so a day's denoised value never sees a later price --
    denoising the whole series at once (the usual way) leaks the future in."""
    approx, details = window.astype(float), []
    for _ in range(levels):
        if len(approx) % 2:
            approx = approx[1:]
        details.append((approx[0::2] - approx[1::2]) / np.sqrt(2))
        approx = (approx[0::2] + approx[1::2]) / np.sqrt(2)
    sigma = np.median(np.abs(details[0])) / 0.6745
    thr = sigma * np.sqrt(2 * np.log(len(window)))
    details = [np.sign(d) * np.maximum(np.abs(d) - thr, 0) for d in details]
    for d in reversed(details):
        up = np.empty(len(approx) * 2)
        up[0::2] = (approx + d) / np.sqrt(2)
        up[1::2] = (approx - d) / np.sqrt(2)
        approx = up
    return float(approx[-1])


def causal_wavelet_denoise(frame: pd.DataFrame, window: int = WAVELET_WINDOW) -> pd.DataFrame:
    vals = frame.values
    out = np.full_like(vals, np.nan, dtype=float)
    for i in range(window - 1, len(vals)):
        for j in range(vals.shape[1]):
            out[i, j] = _haar_denoise_last(vals[i - window + 1:i + 1, j])
    return pd.DataFrame(out, index=frame.index, columns=frame.columns)


def _train_sae(X: np.ndarray, sizes=WSAE_CODE_SIZES, epochs: int = 200, seed: int = 42) -> list:
    """Greedy layer-wise stacked autoencoder (Bao et al.): each layer learns to
    reconstruct the previous layer's code; only the encoders are kept."""
    torch.manual_seed(seed)
    encoders, h = [], torch.tensor(X, dtype=torch.float32)
    for size in sizes:
        enc = nn.Sequential(nn.Linear(h.shape[1], size), nn.Sigmoid())
        dec = nn.Linear(size, h.shape[1])
        opt = torch.optim.Adam(list(enc.parameters()) + list(dec.parameters()), lr=1e-2)
        for _ in range(epochs):
            opt.zero_grad()
            loss = nn.functional.mse_loss(dec(enc(h)), h)
            loss.backward()
            opt.step()
        with torch.no_grad():
            h = enc(h)
        encoders.append(enc)
    return encoders


def _encode(encoders: list, X: np.ndarray) -> np.ndarray:
    h = torch.tensor(X, dtype=torch.float32)
    with torch.no_grad():
        for enc in encoders:
            h = enc(h)
    return h.numpy()


def run_wsae_lstm(feat: pd.DataFrame, epochs: int = 150) -> dict:
    """Bao, Yue & Rao (2017): wavelet denoising -> stacked autoencoder features
    -> LSTM. Here the wavelet step is causal (trailing window), and the scaler
    and autoencoder used for the holdout score are fit on training rows only."""
    den = causal_wavelet_denoise(feat[FEATURES].dropna())
    usable = den.dropna().join(feat[TARGETS])
    targets = usable[TARGETS].values
    n_rows = len(usable)
    if n_rows < SEQ_LEN + 80:
        raise ValueError(f"only {n_rows} denoised rows -- not enough history for WSAE-LSTM")

    hidden = 32
    split_row = int(n_rows * SPLIT_RATIO)
    train_rows = usable.iloc[:split_row - FORECAST_DAYS]
    scaler = StandardScaler().fit(train_rows[FEATURES])
    enc = _train_sae(scaler.transform(train_rows[FEATURES]))
    codes = _encode(enc, scaler.transform(usable[FEATURES]))
    X, y, rows = _build_sequences(codes, targets, SEQ_LEN, with_rows=True)
    split = int(len(X) * SPLIT_RATIO)
    model = _train_lstm(X[:split - FORECAST_DAYS], y[:split - FORECAST_DAYS], codes.shape[1],
                        hidden, 1e-3, epochs)
    with torch.no_grad():
        holdout = model(torch.tensor(X[split:])).numpy()
    scores = _score_path(y[split:], holdout)

    scaler_all = StandardScaler().fit(usable[FEATURES])
    enc_all = _train_sae(scaler_all.transform(usable[FEATURES]))
    codes_all = _encode(enc_all, scaler_all.transform(usable[FEATURES]))
    X_all, y_all = _build_sequences(codes_all, targets, SEQ_LEN)
    final = _train_lstm(X_all, y_all, codes_all.shape[1], hidden, 1e-3, epochs)
    with torch.no_grad():
        path = final(torch.tensor(codes_all[-SEQ_LEN:][None, :, :])).numpy()[0]
    return {"scores": scores, "path": path,
            "params": {"wavelet": f"haar, 2 levels, causal {WAVELET_WINDOW}-day window",
                       "sae_layers": list(WSAE_CODE_SIZES), "lstm_hidden": hidden},
            "holdout": _holdout_frame(holdout, usable.index[rows[split:]])}


# --------------------------------------------------------------------------
# Temporal Fusion Transformer (Lim et al. 2021), compact pure-PyTorch version
# --------------------------------------------------------------------------

class _GRN(nn.Module):
    """Gated residual network: the TFT building block. A GLU gate lets the
    network switch a non-linear transform off and pass the input through."""

    def __init__(self, d_in: int, d_hidden: int, d_out: int, dropout: float = 0.1):
        super().__init__()
        self.skip = nn.Linear(d_in, d_out) if d_in != d_out else nn.Identity()
        self.fc1, self.fc2 = nn.Linear(d_in, d_hidden), nn.Linear(d_hidden, d_out)
        self.gate = nn.Linear(d_out, 2 * d_out)
        self.drop, self.norm = nn.Dropout(dropout), nn.LayerNorm(d_out)

    def forward(self, x):
        h = self.fc2(nn.functional.elu(self.fc1(x)))
        h = nn.functional.glu(self.gate(self.drop(h)), dim=-1)
        return self.norm(self.skip(x) + h)


class TemporalFusionTransformer(nn.Module):
    """Variable selection -> LSTM encoder -> causal self-attention -> 7-step head.
    The variable-selection weights say which features the model relied on."""

    def __init__(self, n_features: int, d_model: int = 16, n_heads: int = 4, n_out: int = FORECAST_DAYS):
        super().__init__()
        self.embed = nn.ModuleList([nn.Linear(1, d_model) for _ in range(n_features)])
        self.var_grn = nn.ModuleList([_GRN(d_model, d_model, d_model) for _ in range(n_features)])
        self.selector = _GRN(n_features * d_model, d_model, n_features)
        self.lstm = nn.LSTM(d_model, d_model, batch_first=True)
        self.post_lstm = _GRN(d_model, d_model, d_model)
        self.attn = nn.MultiheadAttention(d_model, n_heads, batch_first=True)
        self.post_attn = _GRN(d_model, d_model, d_model)
        self.head = nn.Linear(d_model, n_out)
        self.last_selection = None

    def forward(self, x):
        emb = [lin(x[..., j:j + 1]) for j, lin in enumerate(self.embed)]
        processed = torch.stack([g(e) for g, e in zip(self.var_grn, emb)], dim=-2)
        weights = torch.softmax(self.selector(torch.cat(emb, dim=-1)), dim=-1)
        self.last_selection = weights.detach()
        selected = (weights.unsqueeze(-1) * processed).sum(dim=-2)
        enc = self.post_lstm(self.lstm(selected)[0] + selected)
        steps = enc.shape[1]
        causal = torch.triu(torch.ones(steps, steps, dtype=torch.bool), diagonal=1)
        att, _ = self.attn(enc, enc, enc, attn_mask=causal)
        return self.head(self.post_attn(att + enc)[:, -1, :])


TFT_GRID = [{"d_model": 16, "lr": 1e-3}]


def _train_tft(X, y, d_model, lr, epochs, seed=42):
    torch.manual_seed(seed)
    model = TemporalFusionTransformer(X.shape[2], d_model=d_model)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    xb, yb = torch.tensor(X), torch.tensor(y)
    model.train()
    for _ in range(epochs):
        opt.zero_grad()
        loss = nn.functional.mse_loss(model(xb), yb)
        loss.backward()
        opt.step()
    model.eval()
    return model


def run_tft(feat: pd.DataFrame, epochs: int = 150) -> dict:
    """Lim et al. (2021) Temporal Fusion Transformer over SEQ_LEN-day windows of
    FEATURES. Targets are standardised (raw returns are ~0.01) and the scaler for
    the holdout score is fit on training rows only."""
    usable = feat.dropna(subset=FEATURES)
    targets = usable[TARGETS].values
    n_train_rows = int(len(usable) * SPLIT_RATIO)

    def prepare(fit_rows):
        xs = StandardScaler().fit(usable[FEATURES].iloc[:fit_rows])
        lab = targets[:fit_rows - FORECAST_DAYS]   # labels that close before the holdout starts
        lab = lab[~np.isnan(lab).any(axis=1)]
        ys = StandardScaler().fit(lab)
        return xs, ys

    xs, ys = prepare(n_train_rows)
    X, y, rows = _build_sequences(xs.transform(usable[FEATURES]), targets, SEQ_LEN, with_rows=True)
    if len(X) < 60:
        raise ValueError(f"only {len(X)} usable sequences -- not enough history to train TFT")
    y_s = ys.transform(y).astype(np.float32)
    split = int(len(X) * SPLIT_RATIO)
    inner = int(split * SPLIT_RATIO)

    best, best_mae = TFT_GRID[0], np.inf
    for p in TFT_GRID:
        m = _train_tft(X[:inner - FORECAST_DAYS], y_s[:inner - FORECAST_DAYS], p["d_model"], p["lr"], epochs)
        with torch.no_grad():
            pred = ys.inverse_transform(m(torch.tensor(X[inner:split])).numpy())
        mae = float(np.mean(np.abs(y[inner:split] - pred)))
        if mae < best_mae:
            best, best_mae = p, mae

    scored = _train_tft(X[:split - FORECAST_DAYS], y_s[:split - FORECAST_DAYS], best["d_model"], best["lr"], epochs)
    with torch.no_grad():
        holdout = ys.inverse_transform(scored(torch.tensor(X[split:])).numpy())

    xs_all, ys_all = prepare(len(usable))
    X_all, y_all = _build_sequences(xs_all.transform(usable[FEATURES]), targets, SEQ_LEN)
    final = _train_tft(X_all, ys_all.transform(y_all).astype(np.float32), best["d_model"], best["lr"], epochs)
    window = xs_all.transform(usable[FEATURES])[-SEQ_LEN:][None, :, :].astype(np.float32)
    with torch.no_grad():
        path = ys_all.inverse_transform(final(torch.tensor(window)).numpy())[0]
        importance = final.last_selection[0].mean(dim=0).numpy()
    return {"scores": _score_path(y[split:], holdout), "path": path,
            "params": {**best, "feature_importance": {f: round(float(w), 3)
                                                      for f, w in zip(FEATURES, importance)}},
            "holdout": _holdout_frame(holdout, usable.index[rows[split:]])}


def run_theta(close: pd.Series, split: int) -> dict:
    """Theta method (Assimakopoulos & Nikolopoulos 2000): the M4 competition's
    strong statistical benchmark that N-BEATS reports itself against. Walk-forward
    scored on log prices like ARIMA and Kalman."""
    from statsmodels.tsa.forecasting.theta import ThetaModel

    log_close = np.log(close)

    def fit_at(series):
        return np.asarray(ThetaModel(np.asarray(series), period=1, deseasonalize=False)
                          .fit().forecast(FORECAST_DAYS))

    scores, holdout = _walk_forward(log_close, split, fit_at)
    path = np.exp(fit_at(log_close.values) - log_close.iloc[-1]) - 1
    return {"scores": scores, "path": path, "params": {"method": "theta, no seasonality"},
            "holdout": holdout}


# --------------------------------------------------------------------------
# Fusion: learn, per ticker, how to combine the base models
# --------------------------------------------------------------------------

# a base model joins the fusion / best-two pick only if it has holdout predictions on
# this share of the holdout dates. 0.85 keeps WSAE-LSTM (its causal wavelet window
# costs it ~12% of the dates); RegimeSwitch, scored at ~15 sparse origins, stays out.
FUSION_MIN_COVERAGE = 0.85
FUSION_TOP_K = 3


def _fusion_inputs(results: dict, labeled: pd.DataFrame, split: int) -> tuple:
    """Every base model's out-of-sample predictions on the same holdout dates,
    plus what actually happened on those dates."""
    dates = labeled.index[split:]
    frames = {}
    for key, res in results.items():
        h = res.get("holdout")
        if h is None:
            continue
        h = h.reindex(dates)
        if h.notna().all(axis=1).mean() >= FUSION_MIN_COVERAGE:
            frames[key] = h
    if not frames:
        return {}, pd.DataFrame()
    complete = np.all([f.notna().all(axis=1).values for f in frames.values()], axis=0)
    common = dates[complete]
    return {k: f.loc[common] for k, f in frames.items()}, labeled.loc[common, TARGETS]


def _stack_weights(preds: dict, actual: pd.DataFrame) -> dict:
    """Non-negative weights summing to 1 that minimise squared error. "naive"
    (predict no change) is one of the candidates, so when the base models add
    nothing the fusion can shrink all the way back to the random walk."""
    from scipy.optimize import minimize

    names = list(preds) + ["naive"]
    P = np.column_stack([preds[k].values.ravel() for k in preds] + [np.zeros(actual.size)])
    y = actual.values.ravel()
    k = P.shape[1]
    scale = float(np.mean(y ** 2)) or 1.0   # raw squared returns (~1e-4) are below SLSQP's tolerance
    res = minimize(lambda w: np.mean((y - P @ w) ** 2) / scale, np.full(k, 1 / k), method="SLSQP",
                   bounds=[(0, 1)] * k, constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1}],
                   options={"ftol": 1e-10, "maxiter": 500})
    w = np.clip(res.x, 0, None)
    return dict(zip(names, w / w.sum()))


def _top_k_weights(preds: dict, actual: pd.DataFrame, k: int = FUSION_TOP_K) -> dict:
    """The k base models with the lowest error on these dates, weighted by 1/MAE."""
    mae = {m: float(np.mean(np.abs(actual.values - p.values))) for m, p in preds.items()}
    top = sorted(mae, key=mae.get)[:k]
    inv = {m: 1 / mae[m] for m in top}
    return {m: v / sum(inv.values()) for m, v in inv.items()}


def _combine(parts: dict, weights: dict):
    return sum(w * parts[m] for m, w in weights.items() if m != "naive" and w > 0)


ENSEMBLE_SIZE = 2
TOP_MODELS_SHOWN = 10


def _best_two_weights(preds: dict, actual: pd.DataFrame, k: int = ENSEMBLE_SIZE) -> dict:
    """The Ensemble: a plain average of the k base models with the lowest error
    on these dates."""
    mae = {m: float(np.mean(np.abs(actual.values - p.values))) for m, p in preds.items()}
    return {m: 1 / k for m in sorted(mae, key=mae.get)[:k]}


FUSION_METHODS = {"ensemble": _best_two_weights, "fusion": _stack_weights, "fusion_top3": _top_k_weights}


def run_fusion(results: dict, labeled: pd.DataFrame, split: int) -> tuple:
    """Fit each fusion on the first half of the common holdout and score it on
    the second half (with a FORECAST_DAYS embargo between them), next to every
    base model, the equal-weight Ensemble and naive on those same dates. The
    live fusion weights are then refit on the whole common holdout.

    Returns ({fusion key: result dict}, like-for-like report DataFrame)."""
    preds, actual = _fusion_inputs(results, labeled, split)
    if len(actual) < 30 or len(preds) < 2:
        return {}, pd.DataFrame()

    cut = len(actual) // 2
    fit_idx, test_idx = actual.index[:cut - FORECAST_DAYS], actual.index[cut:]
    fit_p = {k: p.loc[fit_idx] for k, p in preds.items()}
    test_p = {k: p.loc[test_idx] for k, p in preds.items()}
    y_test = actual.loc[test_idx].values
    naive_mae = float(np.mean(np.abs(y_test)))

    candidates = {MODEL_LABELS[k]: p.values for k, p in test_p.items()}
    candidates[f"Mean of all {len(test_p)}"] = np.mean([p.values for p in test_p.values()], axis=0)
    fusions = {}
    for key, method in FUSION_METHODS.items():
        w_fit = method(fit_p, actual.loc[fit_idx])
        test_pred = np.asarray(_combine({k: p.values for k, p in test_p.items()}, w_fit))
        candidates[MODEL_LABELS[key]] = test_pred
        w_live = method(preds, actual)
        live_path = np.asarray(_combine({k: np.asarray(results[k]["path"], dtype=float) for k in preds},
                                        w_live), dtype=float)
        fusions[key] = {"scores": _score_path(y_test, test_pred), "path": live_path,
                        "params": {"weights": {("Naive" if m == "naive" else MODEL_LABELS[m]): round(float(w), 3)
                                               for m, w in w_live.items() if w > 1e-3}},
                        "holdout": None, "n_scored": len(test_idx)}

    rows = []
    for name, pred in candidates.items():
        s = _score_path(y_test, pred)
        rows.append({"model": name, "mae": s["mae"], "mae_vs_naive": s["mae"] / naive_mae,
                     "direction_acc": s["direction_acc"]})
    rows.append({"model": "Naive (no change)", "mae": naive_mae, "mae_vs_naive": 1.0,
                 "direction_acc": np.nan})
    rows.append({"model": "Always UP", "mae": np.nan, "mae_vs_naive": np.nan,
                 "direction_acc": float(np.mean(y_test > 0))})
    report = pd.DataFrame(rows).set_index("model").sort_values("mae_vs_naive")
    # base models ranked by error over the whole common holdout -- what the
    # live Ensemble and the "top 10" display are chosen from
    full_mae = {MODEL_LABELS[k]: float(np.mean(np.abs(actual.values - p.values))) for k, p in preds.items()}
    report.attrs["ranking"] = pd.DataFrame(
        {"holdout_mae": full_mae,
         "mae_vs_naive": {m: v / float(np.mean(np.abs(actual.values))) for m, v in full_mae.items()}}
    ).sort_values("holdout_mae").rename_axis("model")
    report.attrs.update({"fit_origins": len(fit_idx), "test_origins": len(test_idx),
                         "test_from": str(test_idx[0].date()), "test_to": str(test_idx[-1].date())})
    return fusions, report


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

MODEL_LABELS = {"ridge": "Ridge", "xgboost": "XGBoost", "lstm": "LSTM",
                "arima": "ARIMA", "kalman": "Kalman",
                # from the papers in paper/
                "lasso": "LASSO", "random_forest": "RandomForest", "dnn": "DNN",
                "wsae_lstm": "WSAE-LSTM", "nbeats": "N-BEATS",
                # advanced_forecast.py
                "regime": "RegimeSwitch", "arimax": "ARIMAX",
                # stronger base models
                "tft": "TFT", "theta": "Theta",
                # learned per-ticker combinations of the base models (run_fusion)
                "fusion": "Fusion", "fusion_top3": "Fusion-Top3",
                # average of the best ENSEMBLE_SIZE base models (run_fusion)
                "ensemble": "Ensemble"}
FUSION_LABELS = ("Fusion", "Fusion-Top3")
BASE_MODELS = [k for k in MODEL_LABELS if k not in ("fusion", "fusion_top3", "ensemble")]


def future_trading_days(last_date: pd.Timestamp, n: int = FORECAST_DAYS,
                        weekend: tuple = (5, 6)) -> list:
    """Next n trading days after last_date, skipping the exchange's weekend
    (Fri/Sat for Tadawul and DSE). Exchange holidays are not modelled, so a
    forecast dated on a holiday simply never gets graded."""
    days, cursor = [], pd.Timestamp(last_date)
    while len(days) < n:
        cursor += pd.Timedelta(days=1)
        if cursor.weekday() not in weekend:
            days.append(cursor)
    return days


def forecast_ticker(ticker: str, verbose: bool = True, models: list | None = None) -> dict:
    """Train every model on the combined history and return their 7-day paths.
    `models` (MODEL_LABELS keys) restricts the run to a subset, e.g. for speed."""
    raw = load_combined_history(ticker)
    feat = make_features(raw)
    labeled = feat.dropna(subset=FEATURES + TARGETS)
    if len(labeled) < 120:
        raise ValueError(f"{ticker}: only {len(labeled)} labeled rows -- need at least 120")

    split = int(len(labeled) * SPLIT_RATIO)
    last_row = feat.dropna(subset=FEATURES).iloc[[-1]]
    last_price = float(raw["Close"].iloc[-1])
    n_daily = int((raw.get("source") == "daily_log").sum()) if "source" in raw else 0

    close = raw["Close"]
    runners = {
        "ridge": lambda: run_ridge(labeled, split, last_row),
        "xgboost": lambda: run_xgboost(labeled, split, last_row),
        "lstm": lambda: run_lstm(feat),
        "arima": lambda: run_arima(close, split),
        "kalman": lambda: run_kalman(close, split),
        "lasso": lambda: run_lasso(labeled, split, last_row),
        "random_forest": lambda: run_random_forest(labeled, split, last_row),
        "dnn": lambda: run_dnn(labeled, split, last_row),
        "wsae_lstm": lambda: run_wsae_lstm(feat),
        "tft": lambda: run_tft(feat),
        "theta": lambda: run_theta(close, split),
    }
    if _HAS_ADVANCED:
        runners["nbeats"] = lambda: af.run_nbeats(close, FORECAST_DAYS)
        runners["regime"] = lambda: af.run_regime_switching(close, split, FORECAST_DAYS)
        runners["arimax"] = lambda: af.run_arimax(close, split, FORECAST_DAYS)
    if models is not None:
        runners = {k: v for k, v in runners.items() if k in models}

    if verbose:
        print(f"{ticker} ({pl.TICKERS[ticker]['name']}): {len(raw)} rows "
              f"({len(raw) - n_daily} snapshot + {n_daily} daily-log), "
              f"{raw.index[0].date()} to {raw.index[-1].date()}")
        print(f"  training {len(runners)} multi-step models on {len(labeled)} labeled rows ...")

    # one model failing (an optional dependency, a fit that will not converge)
    # drops that model from this run instead of losing the whole forecast
    results, failed = {}, {}
    for key, run in runners.items():
        try:
            results[key] = run()
        except Exception as e:
            failed[key] = f"{type(e).__name__}: {e}"
            if verbose:
                print(f"  {MODEL_LABELS[key]} skipped -- {failed[key][:120]}")

    dates = future_trading_days(raw.index[-1], weekend=pl.weekend_days(ticker))
    paths = pd.DataFrame(index=pd.DatetimeIndex(dates, name="date"))
    for key, res in results.items():
        paths[MODEL_LABELS[key]] = last_price * (1 + np.asarray(res["path"], dtype=float))
    # fallback Ensemble if the fusion step cannot run: best ENSEMBLE_SIZE by own holdout MAE.
    # run_fusion replaces it with the pick made on the common holdout dates.
    by_mae = sorted(results, key=lambda k: results[k]["scores"]["mae"]
                    if np.isfinite(results[k]["scores"]["mae"]) else np.inf)
    paths["Ensemble"] = paths[[MODEL_LABELS[k] for k in by_mae[:ENSEMBLE_SIZE]]].mean(axis=1)
    ensemble_members = [MODEL_LABELS[k] for k in by_mae[:ENSEMBLE_SIZE]]
    top_models = [MODEL_LABELS[k] for k in by_mae[:TOP_MODELS_SHOWN]]

    fusions, fusion_report = ({}, pd.DataFrame())
    if models is None or any(k in models for k in FUSION_METHODS):
        try:
            fusions, fusion_report = run_fusion(results, labeled, split)
        except Exception as e:
            failed["fusion"] = f"{type(e).__name__}: {e}"
    for key, res in fusions.items():
        paths[MODEL_LABELS[key]] = last_price * (1 + res["path"])
    results.update(fusions)
    if "ensemble" in fusions:
        ensemble_members = list(fusions["ensemble"]["params"]["weights"])
        top_models = list(fusion_report.attrs["ranking"].index[:TOP_MODELS_SHOWN])

    # the bar every model has to clear: "no change" over the same holdout
    holdout_true = labeled[TARGETS].iloc[split:].values
    naive_mae = float(np.mean(np.abs(holdout_true)))
    rows = []
    for k, res in results.items():
        if k in fusions:   # scored on the second half of the holdout only -- see fusion_report
            ref = fusion_report.loc["Naive (no change)", "mae"]
            n_scored = res["n_scored"]
        else:
            ref = naive_mae
            n_scored = len(res["holdout"]) if res.get("holdout") is not None else np.nan
        rows.append({"model": MODEL_LABELS[k], "mean_mae": res["scores"]["mae"],
                     "mae_vs_naive": res["scores"]["mae"] / ref,
                     "mean_direction_acc": res["scores"]["direction_acc"],
                     "holdout_origins": n_scored, "params": res["params"]})
    scoreboard = pd.DataFrame(rows).set_index("model").sort_values("mean_direction_acc", ascending=False)

    best = scoreboard.index[0]
    final_price = float(paths[best].iloc[-1])

    # A single current spot rate, applied uniformly to history and forecast
    # alike -- not a historical FX series (none is fetched), so this is an
    # approximation, not what those past days were worth in USD at the time.
    # Good enough for comparing companies side by side, not for FX-adjusted
    # return analysis.
    currency = pl.TICKERS[ticker]["currency"]
    fx = pl.get_fx_rate_to_usd(currency)
    paths_usd = paths / fx

    return {
        "ticker": ticker,
        "company": pl.TICKERS[ticker]["name"],
        "currency": currency,
        "fx_rate_to_usd": fx,
        "last_price": last_price,
        "last_price_usd": last_price / fx,
        "last_date": raw.index[-1],
        "n_rows": len(raw),
        "n_daily_log_rows": n_daily,
        "paths": paths,
        "paths_usd": paths_usd,
        "scoreboard": scoreboard,
        "best_model": best,
        "final_price": final_price,
        "final_price_usd": final_price / fx,
        "total_return_pct": (final_price / last_price - 1) * 100,
        "direction": classify_move(final_price, last_price),
        "raw": raw,
        "results": results,
        "failed_models": failed,
        "fusion_report": fusion_report,
        "ensemble_members": ensemble_members,
        "top_models": top_models,
        "naive_holdout_mae": naive_mae,
        "always_up_holdout_acc": float(np.mean(holdout_true > 0)),
    }


FLAT_THRESHOLD = 0.0005   # |move| under 0.05% over 7 days is not a direction


def classify_move(final_price: float, last_price: float) -> str:
    move = final_price / last_price - 1
    if abs(move) < FLAT_THRESHOLD:
        return "FLAT"
    return "UP" if move > 0 else "DOWN"


def direction_mix(forecast: dict) -> pd.Series:
    """How many models point up, flat, or down -- the direction chart's input.

    FLAT is its own bucket rather than being folded into DOWN: a Kalman local-level
    fit forecasts no change at all, and calling that a bearish vote would read
    a view into a model that is explicitly declining to express one."""
    last_price = forecast["last_price"]
    votes = {"UP": 0, "FLAT": 0, "DOWN": 0}
    for model in forecast["paths"].columns.drop(["Ensemble", *FUSION_LABELS], errors="ignore"):
        votes[classify_move(float(forecast["paths"][model].iloc[-1]), last_price)] += 1
    return pd.Series(votes, name=forecast["ticker"])


# --------------------------------------------------------------------------
# Forecast tracking: save every prediction, then grade it against what the
# price actually did. This is the "was yesterday's call right?" record -- the
# only measure of the system that does not depend on a backtest.
# --------------------------------------------------------------------------

FORECAST_LOG_DIR = os.path.join(BASE_DIR, "forecast_log")
TOLERANCE_PCT = 1.0      # a call within this % of the actual close counts as a hit

FORECAST_LOG_COLUMNS = [
    "made_on", "target_date", "horizon_day", "model",
    "price_when_made", "predicted_price",
    "actual_price", "abs_pct_error", "within_tolerance", "direction_correct",
]


def forecast_log_path(ticker: str) -> str:
    os.makedirs(FORECAST_LOG_DIR, exist_ok=True)
    return os.path.join(FORECAST_LOG_DIR, f"{pl.safe_filename(ticker)}_forecasts.csv")


def load_forecast_log(ticker: str) -> pd.DataFrame:
    path = forecast_log_path(ticker)
    if not os.path.exists(path):
        return pd.DataFrame(columns=FORECAST_LOG_COLUMNS)
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=FORECAST_LOG_COLUMNS)


def record_forecast(ticker: str, forecast: dict) -> int:
    """Archive every model's predicted price for each of the next 7 trading
    days, so a later run can check what actually happened on those dates.

    Re-forecasting from the same origin replaces that origin's rows instead of
    duplicating them -- running the notebook twice in a day should not make the
    track record twice as long."""
    made_on = str(pd.Timestamp(forecast["last_date"]).date())
    paths, price_now = forecast["paths"], forecast["last_price"]

    rows = []
    for model in paths.columns:
        for h, (target_date, predicted) in enumerate(paths[model].items(), start=1):
            rows.append({
                "made_on": made_on,
                "target_date": str(pd.Timestamp(target_date).date()),
                "horizon_day": h,
                "model": model,
                "price_when_made": round(float(price_now), 4),
                "predicted_price": round(float(predicted), 4),
                "actual_price": np.nan,
                "abs_pct_error": np.nan,
                "within_tolerance": np.nan,
                "direction_correct": np.nan,
            })

    log = load_forecast_log(ticker)
    log = log[log["made_on"].astype(str) != made_on]        # replace this origin
    log = pd.concat([log, pd.DataFrame(rows)], ignore_index=True)
    log = log.reindex(columns=FORECAST_LOG_COLUMNS).sort_values(["made_on", "model", "horizon_day"])
    log.to_csv(forecast_log_path(ticker), index=False)
    return len(rows)


def grade_forecasts(ticker: str, close: pd.Series | None = None) -> int:
    """Fill in the actual close for any archived prediction whose target date
    has since arrived. Returns how many rows were newly graded."""
    log = load_forecast_log(ticker)
    if log.empty:
        return 0

    if close is None:
        close = load_combined_history(ticker)["Close"]
    actual_by_date = {str(d.date()): float(v) for d, v in close.items()}

    graded = 0
    for i, row in log.iterrows():
        if pd.notna(row.get("actual_price")):
            continue
        actual = actual_by_date.get(str(row["target_date"]))
        if actual is None:
            continue

        predicted = float(row["predicted_price"])
        made_at = float(row["price_when_made"])
        log.at[i, "actual_price"] = round(actual, 4)
        log.at[i, "abs_pct_error"] = round(abs(predicted - actual) / actual * 100, 4)
        log.at[i, "within_tolerance"] = bool(abs(predicted - actual) / actual * 100 <= TOLERANCE_PCT)

        # A forecast of "no change" makes no directional call, so it is neither
        # right nor wrong -- scoring it as wrong (which a raw sign comparison
        # does) understates a flat model like Kalman local-level to the point of
        # absurdity: it read 0.3% on AAPL, meaning "almost never right" about a
        # call it never made.
        predicted_move = (predicted - made_at) / made_at
        if abs(predicted_move) < FLAT_THRESHOLD:
            log.at[i, "direction_correct"] = np.nan
        else:
            log.at[i, "direction_correct"] = bool(
                np.sign(predicted - made_at) == np.sign(actual - made_at))
        graded += 1

    if graded:
        log.to_csv(forecast_log_path(ticker), index=False)
    return graded


def accuracy_summary(ticker: str) -> pd.DataFrame:
    """Per model: how close its past calls landed, and how often they were
    right. Empty until enough target dates have actually arrived."""
    log = load_forecast_log(ticker)
    if log.empty:
        return pd.DataFrame()
    done = log.dropna(subset=["actual_price"])
    if done.empty:
        return pd.DataFrame()

    rows = []
    for model, grp in done.groupby("model"):
        called = grp[grp["direction_correct"].notna()]
        rows.append({
            "model": model,
            "graded": len(grp),
            "mean_abs_pct_error": round(float(grp["abs_pct_error"].mean()), 3),
            "median_abs_pct_error": round(float(grp["abs_pct_error"].median()), 3),
            f"within_{TOLERANCE_PCT}pct": round(float(_as_bool(grp["within_tolerance"]).mean()) * 100, 1),
            "made_a_call": len(called),
            "direction_acc": (round(float(_as_bool(called["direction_correct"]).mean()) * 100, 1)
                              if len(called) else np.nan),
        })
    return pd.DataFrame(rows).set_index("model").sort_values("mean_abs_pct_error")


def _as_bool(s: pd.Series) -> pd.Series:
    return s.astype(str).str.lower().isin(["true", "1", "1.0"])


def graded_points(ticker: str, model: str = "Ensemble") -> pd.DataFrame:
    """Past predictions for one model that now have an actual, ready to plot
    against the real price line."""
    log = load_forecast_log(ticker)
    if log.empty:
        return pd.DataFrame()
    d = log[(log["model"] == model) & log["actual_price"].notna()].copy()
    if d.empty:
        return d
    d["target_date"] = pd.to_datetime(d["target_date"])
    d["within_tolerance"] = _as_bool(d["within_tolerance"])
    d["direction_correct"] = _as_bool(d["direction_correct"])
    return d.sort_values("target_date")


def accuracy_by_horizon(ticker: str, model: str = "Ensemble") -> pd.DataFrame:
    """How accuracy decays with distance: a call about tomorrow should land
    much closer than a call about next week. If it does not, the model is not
    really using the near-term information it has."""
    log = load_forecast_log(ticker)
    if log.empty:
        return pd.DataFrame()
    done = log[(log["model"] == model) & log["actual_price"].notna()]
    if done.empty:
        return pd.DataFrame()

    rows = []
    for h, grp in done.groupby("horizon_day"):
        called = grp[grp["direction_correct"].notna()]
        rows.append({
            "days_ahead": int(h),
            "graded": len(grp),
            "mean_abs_pct_error": round(float(grp["abs_pct_error"].mean()), 3),
            f"within_{TOLERANCE_PCT}pct": round(float(_as_bool(grp["within_tolerance"]).mean()) * 100, 1),
            "direction_acc": (round(float(_as_bool(called["direction_correct"]).mean()) * 100, 1)
                              if len(called) else np.nan),
        })
    return pd.DataFrame(rows).set_index("days_ahead")


def head_to_head(ticker: str, model: str = "Ensemble") -> dict:
    """Who was actually right -- the LLM panel, or the quant model?

    Both are scored on the same question (which way did the price go over the
    next FORECAST_DAYS?) and, crucially, only on the dates where BOTH made a
    directional call. Comparing their separately-measured hit rates would not
    be fair: they would be answering about different days, in different market
    conditions. A like-for-like scoreboard needs the same days.

    The panel's HOLDs are excluded -- declining to call a direction is not a
    prediction, and counting it either way would distort the comparison.
    """
    agent = pl.grade_agent_calls(ticker)
    log = load_forecast_log(ticker)

    out = {"ticker": ticker, "model": model, "n_shared_dates": 0,
           "llm_correct": 0, "quant_correct": 0, "both_correct": 0, "both_wrong": 0,
           "llm_hit_rate": np.nan, "quant_hit_rate": np.nan, "verdict": "",
           "detail": pd.DataFrame()}

    if agent.empty or log.empty:
        out["verdict"] = ("No directional call from the LLM panel yet -- it has only "
                          "returned HOLD so far, so there is nothing to compare.")
        return out

    quant = log[(log["model"] == model)
                & (log["horizon_day"] == FORECAST_DAYS)
                & log["actual_price"].notna()].copy()
    if quant.empty:
        out["verdict"] = "No graded quant forecast at the full horizon yet."
        return out

    quant["date"] = quant["made_on"].astype(str)
    quant["quant_correct"] = _as_bool(quant["direction_correct"])
    merged = agent.merge(quant[["date", "quant_correct", "predicted_price", "price_when_made"]],
                         on="date", how="inner")

    out["n_shared_dates"] = len(merged)
    if merged.empty:
        out["verdict"] = ("The two have no graded dates in common yet -- the panel's calls and "
                          "the model's forecasts were made on different days.")
        return out

    llm_ok = merged["was_correct"].astype(bool)
    quant_ok = merged["quant_correct"].astype(bool)
    out["llm_correct"] = int(llm_ok.sum())
    out["quant_correct"] = int(quant_ok.sum())
    out["both_correct"] = int((llm_ok & quant_ok).sum())
    out["both_wrong"] = int((~llm_ok & ~quant_ok).sum())
    out["llm_hit_rate"] = round(float(llm_ok.mean()) * 100, 1)
    out["quant_hit_rate"] = round(float(quant_ok.mean()) * 100, 1)
    out["detail"] = merged[["date", "final_signal", "actual_return", "was_correct", "quant_correct"]]

    n = len(merged)
    gap = out["llm_hit_rate"] - out["quant_hit_rate"]
    if n < pl.AGENT_FEATURE_MIN_GRADED:
        out["verdict"] = (f"Only {n} shared date(s) -- too few to call a winner. "
                          f"Need at least {pl.AGENT_FEATURE_MIN_GRADED}.")
    elif abs(gap) < 10:
        out["verdict"] = f"Too close to separate ({out['llm_hit_rate']}% vs {out['quant_hit_rate']}%) over {n} dates."
    else:
        winner = "LLM panel" if gap > 0 else "quant model"
        out["verdict"] = (f"{winner} ahead: {max(out['llm_hit_rate'], out['quant_hit_rate'])}% "
                          f"vs {min(out['llm_hit_rate'], out['quant_hit_rate'])}% over {n} dates.")
    return out


# --------------------------------------------------------------------------
# Hit-rate report card: "are the 7-day forecasts any good?"
# --------------------------------------------------------------------------

def _graded_log(ticker: str) -> pd.DataFrame:
    log = load_forecast_log(ticker)
    done = log[log["actual_price"].notna()].copy() if not log.empty else log
    if done.empty:
        return done
    done["hit"] = _as_bool(done["within_tolerance"])
    done["called"] = done["direction_correct"].notna()
    done["dir_ok"] = _as_bool(done["direction_correct"])
    done["went_up"] = done["actual_price"].astype(float) > done["price_when_made"].astype(float)
    return done


def _hit_rates(grp: pd.DataFrame) -> dict:
    called = grp[grp["called"]]
    return {
        "graded_calls": len(grp),
        f"hit_within_{TOLERANCE_PCT:g}pct_%": round(grp["hit"].mean() * 100, 1),
        "mean_abs_error_%": round(float(grp["abs_pct_error"].astype(float).mean()), 2),
        "direction_calls": len(called),
        "direction_hit_%": round(called["dir_ok"].mean() * 100, 1) if len(called) else np.nan,
        # the bar to beat: stocks drift, so "always say UP" is right this often
        # on the very same calls -- beating 50% alone proves nothing
        "always_up_%": round(called["went_up"].mean() * 100, 1) if len(called) else np.nan,
    }


def hit_rate_report(ticker: str, model: str = "Ensemble", recent: int = 10) -> dict:
    """Every graded forecast in forecast_log/, scored two ways.

    * hit within TOLERANCE_PCT: the predicted price landed within that % of the
      actual close on the target date.
    * direction hit: the forecast said up (or down) from the price when it was
      made, and the price went that way. Flat "no change" forecasts make no
      call and are left out rather than counted as wrong.

    The p-value uses only each forecast's final-day (day-7) call, one per
    origin: calls from the same origin for days 1..7 are strongly correlated,
    so pooling them would make the test look far more certain than it is.
    """
    from scipy.stats import binomtest

    done = _graded_log(ticker)
    log = load_forecast_log(ticker)
    ungraded = log[log["actual_price"].isna()] if not log.empty else log
    # a target date already inside the price history that still has no actual
    # was a holiday / non-trading day -- it will never be graded
    last_known = load_combined_history(ticker, sync=False).index[-1]
    pending = ungraded[pd.to_datetime(ungraded["target_date"]) > last_known] if len(ungraded) else ungraded
    out = {"ticker": ticker, "model": model, "per_model": pd.DataFrame(),
           "by_horizon": pd.DataFrame(), "recent_calls": pd.DataFrame(),
           "n_forecasts": 0, "pending_calls": len(pending),
           "ungradable_calls": len(ungraded) - len(pending),
           "next_grading_date": (str(pending["target_date"].min()) if len(pending) else None),
           "verdict": "No forecast has reached its target date yet -- nothing to grade."}
    if done.empty:
        return out

    rows = []
    for m, grp in done.groupby("model"):
        r = {"model": m, "forecasts": grp["made_on"].nunique(), **_hit_rates(grp)}
        final = grp[(grp["horizon_day"] == FORECAST_DAYS) & grp["called"]]
        r["edge_vs_always_up_pts"] = (round(r["direction_hit_%"] - r["always_up_%"], 1)
                                     if len(grp[grp["called"]]) else np.nan)
        r["p_value_day7"] = (round(float(binomtest(int(final["dir_ok"].sum()), len(final), 0.5).pvalue), 3)
                             if len(final) else np.nan)
        rows.append(r)
    # most-tested first: a 90% hit rate from 2 forecasts says nothing yet
    per_model = (pd.DataFrame(rows).set_index("model")
                 .sort_values(["forecasts", "direction_hit_%"], ascending=False))
    out["per_model"] = per_model

    mine = done[done["model"] == model]
    if mine.empty:
        out["verdict"] = f"No graded {model} forecasts yet."
        return out
    out["n_forecasts"] = int(mine["made_on"].nunique())
    out["by_horizon"] = pd.DataFrame(
        [{"days_ahead": int(h), **_hit_rates(g)} for h, g in mine.groupby("horizon_day")]
    ).set_index("days_ahead")

    last = mine.sort_values(["target_date", "horizon_day"]).tail(recent)
    out["recent_calls"] = pd.DataFrame({
        "made_on": last["made_on"].astype(str),
        "target_date": last["target_date"].astype(str),
        "days_ahead": last["horizon_day"].astype(int),
        "price_then": last["price_when_made"].astype(float).round(2),
        "predicted": last["predicted_price"].astype(float).round(2),
        "actual": last["actual_price"].astype(float).round(2),
        "error_%": last["abs_pct_error"].astype(float).round(2),
        f"within_{TOLERANCE_PCT:g}pct": np.where(last["hit"], "HIT", "miss"),
        "direction": np.where(~last["called"], "flat call",
                              np.where(last["dir_ok"], "right", "wrong")),
    }).reset_index(drop=True)

    r = per_model.loc[model]
    p = r["p_value_day7"]
    if pd.isna(r["direction_hit_%"]):
        luck = "it has made no directional calls yet"
    elif p < 0.05 and r["edge_vs_always_up_pts"] > 0:
        luck = f"p={p:.3f} on the day-7 calls: better than a coin flip, and ahead of always-UP"
    elif p < 0.05:
        luck = (f"p={p:.3f} on the day-7 calls, but it does not beat simply always saying UP, "
                f"so the 'skill' is just the market's drift")
    else:
        luck = f"p={p:.3f} on the day-7 calls: not yet distinguishable from luck"
    out["verdict"] = (
        f"{model}: {r[f'hit_within_{TOLERANCE_PCT:g}pct_%']:.1f}% of {int(r['graded_calls'])} graded calls "
        f"landed within {TOLERANCE_PCT:g}% of the actual close. Direction was right "
        f"{r['direction_hit_%']:.1f}% of the time vs {r['always_up_%']:.1f}% for always-UP "
        f"({r['edge_vs_always_up_pts']:+.1f} pts) over {out['n_forecasts']} forecasts -- {luck}."
    )
    return out



def plot_forecast_report(forecast: dict, report: dict, history_days: int = 30):
    """Left: the real price, past Ensemble calls marked hit/miss, and every
    model's path for the next FORECAST_DAYS. Right: hit rate by days ahead,
    against the 50% coin-flip line and the always-UP bar. Native currency."""
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    ink, muted, grid, surf = "#0b0b0b", "#898781", "#e1e0d9", "#fcfcfb"
    hit_c, miss_c = "#0ca30c", "#d03b3b"
    ticker, cur = forecast["ticker"], forecast["currency"]
    style = {
        "figure.facecolor": surf, "axes.facecolor": surf, "axes.edgecolor": "#c3c2b7",
        "axes.linewidth": 0.8, "axes.labelcolor": "#52514e", "text.color": ink,
        "xtick.color": muted, "ytick.color": muted, "grid.color": grid,
        "grid.linewidth": 0.8, "font.size": 10, "figure.dpi": 110,
    }
    with plt.rc_context(style):
        fig, (ax, bx) = plt.subplots(1, 2, figsize=(15, 5.2), gridspec_kw={"width_ratios": [2.3, 1]})

        hist = forecast["raw"]["Close"].iloc[-history_days:]
        paths, last_date, last_price = forecast["paths"], forecast["last_date"], forecast["last_price"]
        ax.grid(True, axis="y")
        ax.set_axisbelow(True)
        ax.plot(hist.index, hist.values, color=muted, linewidth=2, label=f"Actual (last {history_days} days)")

        g = graded_points(ticker, model="Ensemble")
        if not g.empty:
            g = g[g["target_date"] >= hist.index[0]]
            for flag, colour, marker, label in [
                (True, hit_c, "o", f"past call within {TOLERANCE_PCT:g}%"),
                (False, miss_c, "X", f"past call off by >{TOLERANCE_PCT:g}%"),
            ]:
                sub = g[g["within_tolerance"] == flag]
                if sub.empty:
                    continue
                ax.vlines(sub["target_date"], sub["predicted_price"], sub["actual_price"],
                          color=colour, linewidth=0.8, alpha=0.45, zorder=3)
                ax.scatter(sub["target_date"], sub["predicted_price"], s=36, c=colour, marker=marker,
                           edgecolors=surf, linewidths=1.1, zorder=5, label=label)

        xs = [last_date] + list(paths.index)
        shown = [m for m in paths.columns if m != "Ensemble" and m not in FUSION_LABELS]
        for i, m in enumerate(shown):
            ax.plot(xs, [last_price] + list(paths[m].values), color="#c3c2b7", linewidth=1.1,
                    zorder=3, label=f"all {len(shown)} base models" if i == 0 else None)
        best = forecast["best_model"]
        if best != "Fusion":
            ax.plot(xs, [last_price] + list(paths[best].values), color="#2a78d6",
                    linewidth=2, zorder=5, label=f"{best} (best direction on holdout)")
        if "Fusion" in paths:
            ax.plot(xs, [last_price] + list(paths["Fusion"].values), color="#eb6834", linewidth=2.2,
                    zorder=5, label="Fusion (learned blend)")
        members = forecast.get("ensemble_members", [])
        ax.plot(xs, [last_price] + list(paths["Ensemble"].values), color=ink, linewidth=2.6, marker="o",
                markersize=5.5, markeredgecolor=surf, markeredgewidth=1.2, zorder=6,
                label=f"Ensemble = avg of {' + '.join(members)}" if members else "Ensemble")
        end = paths["Ensemble"].iloc[-1]
        ax.annotate(f"{end:,.2f} {cur} ({end / last_price - 1:+.2%})", xy=(paths.index[-1], end),
                    xytext=(8, 0), textcoords="offset points", va="center", fontweight="bold")
        ax.axvline(last_date, color=grid, linewidth=1)
        ax.annotate("today", xy=(last_date, ax.get_ylim()[0]), xytext=(4, 6),
                    textcoords="offset points", color=muted, fontsize=8)
        span = ax.get_ylim()[1] - ax.get_ylim()[0]
        dp = 0 if span > 20 else (1 if span > 2 else 2)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _, d=dp: f"{v:,.{d}f}"))
        ax.set_ylabel(f"Price ({cur})")
        ax.set_title(f"{forecast['company']} ({ticker}) - next {FORECAST_DAYS} trading days",
                     loc="left", fontsize=11.5, fontweight="bold", pad=10)
        ax.legend(frameon=False, fontsize=8.5, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.1))
        ax.set_xlim(right=paths.index[-1] + pd.Timedelta(days=4))

        bh = report["by_horizon"]
        bx.set_title("Ensemble hit rate by days ahead", loc="left", fontsize=11.5, fontweight="bold", pad=10)
        if bh.empty:
            bx.text(0.5, 0.5, "No graded forecasts yet", ha="center", va="center",
                    color=muted, transform=bx.transAxes)
            bx.set_xticks([])
            bx.set_yticks([])
        else:
            x = np.arange(len(bh))
            tol_col = f"hit_within_{TOLERANCE_PCT:g}pct_%"
            bx.bar(x - 0.2, bh["direction_hit_%"], 0.38, color="#2a78d6", edgecolor=surf,
                   linewidth=1, label="direction right", zorder=3)
            bx.bar(x + 0.2, bh[tol_col], 0.38, color="#9ec5f0", edgecolor=surf,
                   linewidth=1, label=f"within {TOLERANCE_PCT:g}%", zorder=3)
            bx.axhline(50, color=muted, linewidth=1, linestyle=":", zorder=2, label="coin flip (50%)")
            bx.plot(x, bh["always_up_%"], color=ink, linewidth=1.4, linestyle="--", marker="_",
                    markersize=12, zorder=4, label="always-UP baseline")
            bx.set_xticks(x, [f"+{d}" for d in bh.index])
            bx.set_xlabel("trading days ahead")
            bx.set_ylim(0, 100)
            bx.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
            bx.grid(True, axis="y")
            bx.set_axisbelow(True)
            bx.legend(frameon=False, fontsize=8.5, ncol=2, loc="upper center", bbox_to_anchor=(0.5, -0.16))
        for a in (ax, bx):
            for s in ("top", "right"):
                a.spines[s].set_visible(False)
        fig.autofmt_xdate(rotation=0, ha="center")
        fig.tight_layout()
    return fig


def all_models_table(forecast: dict) -> pd.DataFrame:
    """One row per predicted path -- the Ensemble, both fusions and every base
    model -- with its day-1 and day-7 prices, the 7-day move, and its error
    relative to "no change". Base models are scored on the common holdout
    dates (the ranking the Ensemble is picked from); the Ensemble and fusions
    on the later test dates their picks never saw (fusion_report)."""
    paths, last = forecast["paths"], forecast["last_price"]
    report = forecast.get("fusion_report", pd.DataFrame())
    ranking = report.attrs.get("ranking") if not report.empty else None
    board = forecast["scoreboard"]
    members = forecast.get("ensemble_members", [])

    combined = ["Ensemble", *[m for m in FUSION_LABELS if m in paths]]
    base = [m for m in paths.columns if m not in combined]
    if ranking is not None:
        base = [m for m in ranking.index if m in base] + [m for m in base if m not in ranking.index]

    rows = []
    for m in combined + base:
        if m in combined:
            role = f"avg of {' + '.join(members)}" if m == "Ensemble" else "fusion"
            err = report.loc[m, "mae_vs_naive"] if m in report.index else np.nan
            scored = "unseen test dates"
        else:
            rank = (list(ranking.index).index(m) + 1) if ranking is not None and m in ranking.index else None
            role = f"base #{rank}" if rank else "base (unranked)"
            if m in members:
                role += ", in Ensemble"
            err = ranking.loc[m, "mae_vs_naive"] if rank else board.loc[m, "mae_vs_naive"]
            scored = "common holdout" if rank else "own holdout"
        rows.append({"model": m, "role": role,
                     f"day1_{forecast['currency']}": round(float(paths[m].iloc[0]), 4),
                     f"day{FORECAST_DAYS}_{forecast['currency']}": round(float(paths[m].iloc[-1]), 4),
                     "move_7d_%": round((float(paths[m].iloc[-1]) / last - 1) * 100, 2),
                     "error_vs_naive": round(float(err), 3) if pd.notna(err) else np.nan,
                     "scored_on": scored,
                     "holdout_direction_acc": round(float(board.loc[m, "mean_direction_acc"]), 3)
                     if m in board.index else np.nan})
    return pd.DataFrame(rows).set_index("model")


def plot_all_models(forecast: dict, history_days: int = 10, ncols: int = 4):
    """Small multiples: one panel per predicted path (Ensemble, fusions, every
    base model), all on the same y-scale so paths compare directly. Each base
    model's panel also carries the Ensemble as a dashed reference line."""
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    ink, muted, grid, surf = "#0b0b0b", "#898781", "#e1e0d9", "#fcfcfb"
    accent, fusion_c = "#2a78d6", "#eb6834"
    table = all_models_table(forecast)
    paths, last_date, last_price = forecast["paths"], forecast["last_date"], forecast["last_price"]
    hist = forecast["raw"]["Close"].iloc[-history_days:]
    xs = [last_date] + list(paths.index)

    lo = min(hist.min(), paths.min().min())
    hi = max(hist.max(), paths.max().max())
    pad = (hi - lo) * 0.06 or abs(hi) * 0.01

    n = len(table)
    nrows = int(np.ceil(n / ncols))
    style = {"figure.facecolor": surf, "axes.facecolor": surf, "axes.edgecolor": "#c3c2b7",
             "axes.linewidth": 0.7, "text.color": ink, "xtick.color": muted, "ytick.color": muted,
             "grid.color": grid, "grid.linewidth": 0.6, "font.size": 8.5, "figure.dpi": 110}
    with plt.rc_context(style):
        fig, axes = plt.subplots(nrows, ncols, figsize=(3.6 * ncols, 2.5 * nrows), sharex=True, sharey=True)
        axes = np.atleast_1d(axes).ravel()
        for ax, (m, row) in zip(axes, table.iterrows()):
            colour = ink if m == "Ensemble" else fusion_c if m in FUSION_LABELS else accent
            ax.grid(True, axis="y")
            ax.set_axisbelow(True)
            ax.plot(hist.index, hist.values, color=muted, linewidth=1.3)
            if m != "Ensemble":
                ax.plot(xs, [last_price] + list(paths["Ensemble"].values), color=ink,
                        linewidth=1, linestyle="--", alpha=0.6)
            ax.plot(xs, [last_price] + list(paths[m].values), color=colour, linewidth=2)
            ax.axvline(last_date, color=grid, linewidth=0.8)
            err = "–" if pd.isna(row["error_vs_naive"]) else f"{row['error_vs_naive']:.3f}× naive"
            ax.set_title(f"{m}  ({row['role']})\n{row['move_7d_%']:+.2f}% in 7 days · {err}",
                         loc="left", fontsize=8.5, fontweight="bold" if m == "Ensemble" else "normal")
            ax.set_ylim(lo - pad, hi + pad)
            ax.xaxis.set_major_locator(mdates.DayLocator(interval=4))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
            for s in ("top", "right"):
                ax.spines[s].set_visible(False)
        for ax in axes[n:]:
            ax.set_visible(False)
        fig.suptitle(f"{forecast['company']} ({forecast['ticker']}) - every model's {FORECAST_DAYS}-day path "
                     f"({forecast['currency']}; dashed = Ensemble; grey = last {history_days} days)",
                     x=0.01, ha="left", fontsize=11, fontweight="bold")
        fig.tight_layout()
    return fig
