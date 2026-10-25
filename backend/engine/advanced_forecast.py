"""Advanced forecasting methods -- optional extra models for forecast_lib's
7-day multi-horizon ensemble.

Added here, in the order discussed (effort-to-payoff, highest first):

  1. run_arimax()          -- ARIMA + exogenous macro regressors (VIX, 10Y
                               Treasury yield, DXY), reusing the same macro
                               tickers the LLM macro-regime analyst already
                               pulls (^VIX, ^TNX, DX-Y.NYB).
  2. run_regime_switching() -- 2-regime Markov-switching model on daily
                               returns (calm/turbulent), forecast blended by
                               each regime's persistence probability. This is
                               the piece that most directly matches the
                               "Regime-Adaptive" framing of the RAEM thesis.
  3. run_garch_bands()     -- GARCH(1,1) volatility forecast, turned into
                               confidence bands around a given center path
                               (e.g. the Ensemble) rather than a directional
                               model of its own.
  4. run_quantile_gbm()    -- direct multi-horizon quantile regression
                               (p10 / p50 / p90) on the same FEATURES/TARGETS
                               forecast_lib.py already builds.
  5. run_nbeats()          -- lightweight generic N-BEATS block stack
                               (pure PyTorch, no extra dependency beyond
                               torch, which run_lstm() already requires).
  6. fit_weighted_ensemble() -- a lightweight "stacking-lite": replaces the
                               plain paths.mean(axis=1) in forecast_ticker()
                               with weights derived from each model's own
                               holdout MAE / direction accuracy, instead of
                               giving every model an equal vote.

Every run_xxx() model function follows forecast_lib.py's existing contract --
it returns {"scores": {...}, "path": np.ndarray of FORECAST_DAYS returns,
"params": {...}} -- so any of them can be dropped straight into
forecast_ticker()'s `results` dict next to ridge/xgboost/lstm/arima/kalman.

New dependencies beyond what forecast_lib.py already imports: `arch` (GARCH).
statsmodels' regime-switching module ships with statsmodels itself, so
run_regime_switching() needs nothing extra. run_arimax()'s macro fetch needs
`yfinance` (predictor_lib.py already imports it) and network access to Yahoo
Finance; if either is unavailable it logs a note and quietly falls back to a
plain ARIMA, so a run never hard-fails for lack of macro data.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error
from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression
from statsmodels.tsa.statespace.sarimax import SARIMAX

try:
    from arch import arch_model
    _HAS_ARCH = True
except ImportError:
    _HAS_ARCH = False

try:
    import torch
    import torch.nn as nn
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False

MACRO_TICKERS = ["^VIX", "^TNX", "DX-Y.NYB"]  # same tickers the macro-regime analyst uses


# --------------------------------------------------------------------------
# Shared small helpers (mirrors forecast_lib._score_path / _walk_forward so
# these models plug into the same scoring convention without importing
# forecast_lib itself, which would create a circular import).
# --------------------------------------------------------------------------

def _direction_acc(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.mean(np.sign(y_pred) == np.sign(y_true))) if len(y_true) else float("nan")


def _score_path(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    per_step = []
    for h in range(y_true.shape[1]):
        per_step.append({
            "horizon_day": h + 1,
            "mae": float(mean_absolute_error(y_true[:, h], y_pred[:, h])),
            "direction_acc": _direction_acc(y_true[:, h], y_pred[:, h]),
        })
    steps = pd.DataFrame(per_step)
    return {"per_step": steps, "mae": float(steps["mae"].mean()),
            "direction_acc": float(steps["direction_acc"].mean())}


def _holdout_frame(preds, dates, forecast_days: int) -> pd.DataFrame | None:
    """Out-of-sample predicted returns by origin date, in forecast_lib's
    target_h1..target_hN layout, so forecast_lib's fusion models can use them."""
    if not len(preds):
        return None
    return pd.DataFrame(np.asarray(preds, dtype=float), index=pd.DatetimeIndex(dates),
                        columns=[f"target_h{h}" for h in range(1, forecast_days + 1)])


def _walk_forward_scalar(log_close: pd.Series, split: int, forecast_days: int, fit_at) -> tuple:
    """Same idea as forecast_lib._walk_forward: score a natively multi-step
    model from many forecast origins across the holdout rather than trusting
    a single arbitrary split point. Returns (scores, holdout frame)."""
    y_true, y_pred, dates = [], [], []
    for pos in range(split, len(log_close) - forecast_days):
        try:
            fc = fit_at(log_close.iloc[:pos + 1])
        except Exception:
            continue
        base = log_close.iloc[pos]
        y_pred.append(np.exp(fc - base) - 1)
        y_true.append(np.exp(log_close.iloc[pos + 1:pos + 1 + forecast_days].values - base) - 1)
        dates.append(log_close.index[pos])
    if not y_true:
        return {"per_step": pd.DataFrame(), "mae": np.nan, "direction_acc": np.nan}, None
    return _score_path(np.array(y_true), np.array(y_pred)), _holdout_frame(y_pred, dates, forecast_days)


# --------------------------------------------------------------------------
# 1. ARIMAX -- ARIMA with macro exogenous regressors
# --------------------------------------------------------------------------

ARIMAX_ORDER_GRID = [(1, 1, 0), (2, 1, 0), (1, 1, 1)]  # smaller than ARIMA_GRID: exog adds params


def fetch_macro_exog(dates: pd.DatetimeIndex) -> pd.DataFrame | None:
    """VIX / 10Y yield / DXY, forward-filled onto `dates`. Returns None (never
    raises) if yfinance or network access isn't available -- callers must
    treat that as "no macro data" and fall back to a plain model."""
    try:
        import yfinance as yf
        raw = yf.download(MACRO_TICKERS, start=dates.min() - pd.Timedelta(days=10),
                           end=dates.max() + pd.Timedelta(days=1),
                           progress=False, auto_adjust=True)["Close"]
        raw = raw.reindex(dates, method="ffill").ffill().bfill()
        raw.columns = ["vix", "tnx", "dxy"]
        if raw.isna().any().any():
            return None
        return raw
    except Exception as e:  # offline, ticker change, rate limit, etc.
        warnings.warn(f"fetch_macro_exog: macro data unavailable ({e}); ARIMAX will fall back to ARIMA")
        return None


def run_arimax(close: pd.Series, split: int, forecast_days: int) -> dict:
    """ARIMA with VIX/10Y-yield/DXY as exogenous regressors, fit on log(Close).

    Future exogenous values are unknown, so the forecast holds each macro
    series at its last observed value for the horizon (the standard
    naive-persistence assumption for exogenous regressors) -- this is noted
    in the returned params so it's never silently assumed elsewhere.
    """
    log_close = np.log(close)
    exog = fetch_macro_exog(close.index)
    if exog is None:
        # No macro data -- degrade to plain ARIMA rather than fail the run.
        from statsmodels.tsa.arima.model import ARIMA
        train = log_close.iloc[:split]
        best_order, best_aic = ARIMAX_ORDER_GRID[0], np.inf
        for order in ARIMAX_ORDER_GRID:
            try:
                fit = ARIMA(train.values, order=order).fit()
            except Exception:
                continue
            if fit.aic < best_aic:
                best_aic, best_order = fit.aic, order

        def fit_at(series):
            return np.asarray(ARIMA(series.values, order=best_order).fit().forecast(steps=forecast_days))

        scores, holdout = _walk_forward_scalar(log_close, split, forecast_days, fit_at)
        fit_full = ARIMA(log_close.values, order=best_order).fit()
        fc_full = fit_full.forecast(steps=forecast_days)
        path = np.exp(fc_full - log_close.iloc[-1]) - 1
        return {"scores": scores, "path": np.asarray(path), "holdout": holdout,
                "params": {"order": best_order, "exog": "unavailable -- fell back to ARIMA"}}

    exog_arr = exog.values
    train_end = split
    best_order, best_aic = ARIMAX_ORDER_GRID[0], np.inf
    for order in ARIMAX_ORDER_GRID:
        try:
            fit = SARIMAX(log_close.values[:train_end], exog=exog_arr[:train_end], order=order,
                           enforce_stationarity=False, enforce_invertibility=False).fit(disp=False)
        except Exception:
            continue
        if fit.aic < best_aic:
            best_aic, best_order = fit.aic, order

    def fit_at(pos_end: int):
        fit = SARIMAX(log_close.values[:pos_end], exog=exog_arr[:pos_end], order=best_order,
                       enforce_stationarity=False, enforce_invertibility=False).fit(disp=False)
        future_exog = np.repeat(exog_arr[pos_end - 1:pos_end], forecast_days, axis=0)  # hold-last-value
        return np.asarray(fit.forecast(steps=forecast_days, exog=future_exog))

    y_true, y_pred, dates = [], [], []
    for pos in range(split, len(log_close) - forecast_days):
        try:
            fc = fit_at(pos + 1)
        except Exception:
            continue
        base = log_close.iloc[pos]
        y_pred.append(np.exp(fc - base) - 1)
        y_true.append(np.exp(log_close.iloc[pos + 1:pos + 1 + forecast_days].values - base) - 1)
        dates.append(log_close.index[pos])
    scores = (_score_path(np.array(y_true), np.array(y_pred)) if y_true
              else {"per_step": pd.DataFrame(), "mae": np.nan, "direction_acc": np.nan})

    fit_full = SARIMAX(log_close.values, exog=exog_arr, order=best_order,
                        enforce_stationarity=False, enforce_invertibility=False).fit(disp=False)
    future_exog_full = np.repeat(exog_arr[-1:], forecast_days, axis=0)
    fc_full = fit_full.forecast(steps=forecast_days, exog=future_exog_full)
    path = np.exp(fc_full - log_close.iloc[-1]) - 1
    return {"scores": scores, "path": np.asarray(path),
            "holdout": _holdout_frame(y_pred, dates, forecast_days),
            "params": {"order": best_order, "exog": ["vix", "tnx", "dxy"],
                       "future_exog_assumption": "held at last observed value"}}


# --------------------------------------------------------------------------
# 2. Regime-switching -- 2-state Markov-switching mean model on returns
# --------------------------------------------------------------------------

def run_regime_switching(close: pd.Series, split: int, forecast_days: int,
                          k_regimes: int = 2) -> dict:
    """Fits a Markov-switching model with regime-dependent mean and variance
    on daily log-returns (calm vs. turbulent regime, typically), then
    forecasts forward by propagating today's regime probabilities through the
    estimated transition matrix and taking the regime-probability-weighted
    mean return at each step -- so the forecast reflects "how likely are we
    to stay in the current regime" rather than one fixed set of dynamics.
    """
    log_ret = np.log(close).diff().dropna()

    def fit_regime(train_ret: pd.Series):
        model = MarkovRegression(train_ret.values, k_regimes=k_regimes, trend="c",
                                  switching_variance=True)
        res = model.fit(disp=False)
        # res.params is a plain ndarray (no names) -- pair it with the model's
        # param_names to pull out the regime-conditional means by label
        # instead of guessing a fixed position in the array.
        res.named_params = pd.Series(res.params, index=model.param_names)
        return res

    def forecast_from(train_ret: pd.Series) -> np.ndarray:
        res = fit_regime(train_ret)
        mus = res.named_params[[f"const[{i}]" for i in range(k_regimes)]].values
        trans = res.regime_transition[:, :, 0]  # k x k transition matrix
        probs = np.asarray(res.filtered_marginal_probabilities)[-1]  # today's regime probs
        cum_log_ret = np.zeros(forecast_days)
        p = probs.copy()
        running = 0.0
        for h in range(forecast_days):
            p = p @ trans  # propagate one step
            step_ret = float(np.dot(p, mus))
            running += step_ret
            cum_log_ret[h] = running
        return cum_log_ret

    # Walk-forward: refit at a handful of origins across the holdout (a full
    # regime-switching fit is expensive, so this steps by a few days at a
    # time rather than scoring every single day like the other models).
    y_true, y_pred, dates = [], [], []
    step = max(1, (len(log_ret) - split) // 15)
    for pos in range(split, len(log_ret) - forecast_days, step):
        try:
            cum_log = forecast_from(log_ret.iloc[:pos + 1])
        except Exception:
            continue
        base_price = close.iloc[pos + 1]  # log_ret index is shifted by 1 vs close
        pred = np.exp(cum_log) - 1
        actual_prices = close.iloc[pos + 2:pos + 2 + forecast_days].values
        if len(actual_prices) < forecast_days:
            continue
        y_pred.append(pred)
        y_true.append(actual_prices / base_price - 1)
        dates.append(close.index[pos + 1])
    scores = (_score_path(np.array(y_true), np.array(y_pred)) if y_true
              else {"per_step": pd.DataFrame(), "mae": np.nan, "direction_acc": np.nan})

    res_full = fit_regime(log_ret)
    cum_log_full = forecast_from(log_ret)
    path = np.exp(cum_log_full) - 1
    current_probs = np.asarray(res_full.filtered_marginal_probabilities)[-1]
    return {"scores": scores, "path": np.asarray(path),
            "holdout": _holdout_frame(y_pred, dates, forecast_days),
            "params": {"k_regimes": k_regimes,
                       "current_regime_probs": current_probs.tolist(),
                       "regime_means_daily_logret": res_full.named_params[
                           [f"const[{i}]" for i in range(k_regimes)]].values.tolist()}}


# --------------------------------------------------------------------------
# 3. GARCH(1,1) -- volatility forecast -> confidence bands (not a directional
#    model; wraps a given center path, e.g. the Ensemble, in +/- bands)
# --------------------------------------------------------------------------

def run_garch_bands(close: pd.Series, split: int, forecast_days: int,
                     center_path_returns: np.ndarray, z: float = 1.645) -> dict:
    """z=1.645 -> ~90% band. Returns cumulative-return bands around
    `center_path_returns` (e.g. the Ensemble path expressed as returns from
    last price), built from a GARCH(1,1) daily-volatility forecast."""
    if not _HAS_ARCH:
        return {"available": False, "note": "`arch` package not installed -- pip install arch"}

    ret_pct = close.pct_change().dropna() * 100  # arch expects returns in %, not decimals
    train = ret_pct.iloc[:split]
    am = arch_model(train, vol="Garch", p=1, q=1, dist="normal")
    fit = am.fit(disp="off")
    fc = fit.forecast(horizon=forecast_days, reindex=False)
    daily_var_pct2 = fc.variance.values[-1]  # variance of daily % return, per horizon day
    daily_sigma = np.sqrt(daily_var_pct2) / 100.0  # back to decimal-return scale

    cum_sigma = np.sqrt(np.cumsum(daily_sigma ** 2))  # variance of independent steps adds
    lower = center_path_returns - z * cum_sigma
    upper = center_path_returns + z * cum_sigma
    return {"available": True, "daily_sigma": daily_sigma.tolist(),
            "cum_sigma": cum_sigma.tolist(), "lower_return_path": lower,
            "upper_return_path": upper, "band_confidence": 1 - 2 * (1 - 0.9500)
            if z == 1.645 else None, "params": {"p": 1, "q": 1, "z": z}}


# --------------------------------------------------------------------------
# 4. Quantile regression (GradientBoostingRegressor, loss="quantile")
# --------------------------------------------------------------------------

QUANTILE_GBM_PARAMS = dict(n_estimators=200, max_depth=3, learning_rate=0.05,
                            subsample=0.8, random_state=42)


def run_quantile_gbm(labeled: pd.DataFrame, split: int, last_row: pd.DataFrame,
                      features: list, targets: list, forecast_days: int,
                      quantiles: tuple = (0.1, 0.5, 0.9)) -> dict:
    """Direct multi-horizon quantile regression: for each forecast day and
    each requested quantile, fits an independent GradientBoostingRegressor
    with quantile loss. The median (0.5) path is returned as this model's
    "path" so it can join the Ensemble/weighted-ensemble like any other
    model; the low/high quantile paths go in params for position sizing /
    the risk-management debate stage."""
    fit_end = split - forecast_days  # same embargo forecast_lib._tune_on_inner_split uses
    X_tr = labeled[features].iloc[:fit_end]
    X_te = labeled[features].iloc[split:]

    def fit_q(q, target_col):
        model = GradientBoostingRegressor(loss="quantile", alpha=q, **QUANTILE_GBM_PARAMS)
        return model.fit(X_tr, labeled[target_col].iloc[:fit_end])

    holdout_by_q = {q: [] for q in quantiles}
    path_by_q = {q: [] for q in quantiles}
    for t in targets:
        for q in quantiles:
            m = fit_q(q, t)
            holdout_by_q[q].append(m.predict(X_te))
            m_full = GradientBoostingRegressor(loss="quantile", alpha=q, **QUANTILE_GBM_PARAMS)
            m_full.fit(labeled[features], labeled[t])
            path_by_q[q].append(float(m_full.predict(last_row[features])[0]))

    holdout_median = np.column_stack(holdout_by_q[0.5])
    y_true = labeled[targets].iloc[split:].values
    scores = _score_path(y_true, holdout_median)
    median_path = np.array(path_by_q[0.5])
    return {"scores": scores, "path": median_path,
            "params": {"quantiles": list(quantiles),
                       "low_path": np.array(path_by_q[min(quantiles)]).tolist(),
                       "high_path": np.array(path_by_q[max(quantiles)]).tolist()}}


# --------------------------------------------------------------------------
# 5. N-BEATS (generic, single-stack) -- pure PyTorch, mirrors run_lstm's
#    tune/train/holdout structure. NOT executable in the sandbox this was
#    written in (no torch install fit there -- see chat); written to match
#    forecast_lib.MultiStepLSTM's conventions closely and should be
#    smoke-tested locally (`python -c "import advanced_forecast"` then a
#    small forecast_ticker() run) before relying on it.
# --------------------------------------------------------------------------

if _HAS_TORCH:
    class _NBeatsBlock(nn.Module):
        """One generic N-BEATS block: a small FC stack maps the input window
        to a hidden representation, then two linear heads read off a
        "backcast" (reconstruction of the input, subtracted from the next
        block's input -- this residual routing is what lets stacked blocks
        each explain a different part of the signal) and a "forecast"
        (this block's contribution to the output path)."""

        def __init__(self, input_size: int, hidden: int, forecast_size: int):
            super().__init__()
            self.fc = nn.Sequential(
                nn.Linear(input_size, hidden), nn.ReLU(),
                nn.Linear(hidden, hidden), nn.ReLU(),
            )
            self.backcast_head = nn.Linear(hidden, input_size)
            self.forecast_head = nn.Linear(hidden, forecast_size)

        def forward(self, x):
            h = self.fc(x)
            return self.backcast_head(h), self.forecast_head(h)

    class NBeatsNet(nn.Module):
        def __init__(self, input_size: int, hidden: int, forecast_size: int, n_blocks: int = 3):
            super().__init__()
            self.blocks = nn.ModuleList(
                [_NBeatsBlock(input_size, hidden, forecast_size) for _ in range(n_blocks)])

        def forward(self, x):
            residual = x
            forecast = torch.zeros(x.shape[0], self.blocks[0].forecast_head.out_features,
                                    device=x.device)
            for block in self.blocks:
                backcast, block_forecast = block(residual)
                residual = residual - backcast
                forecast = forecast + block_forecast
            return forecast

    NBEATS_GRID = [{"hidden": h, "lr": lr} for h in [32, 64] for lr in [1e-3, 5e-4]]


def _build_univariate_windows(log_close: np.ndarray, seq_len: int, forecast_days: int):
    """N-BEATS is classically univariate: the input window is just the
    series' own recent log-returns (not the full FEATURES set used by the
    tree/linear models), which is what lets its backcast head reconstruct
    the input directly."""
    ret = np.diff(log_close)
    X, y = [], []
    for i in range(seq_len, len(ret) - forecast_days + 1):
        X.append(ret[i - seq_len:i])
        y.append(ret[i:i + forecast_days])
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)


def run_nbeats(close: pd.Series, forecast_days: int, seq_len: int = 20,
               epochs: int = 150, split_ratio: float = 0.85) -> dict:
    """N-BEATS builds its own (close-price-only) windowed array, which has a
    different length/alignment than the FEATURES-based `labeled` DataFrame
    the other models' `split` index refers to -- so this computes its own
    train/holdout split from `split_ratio` rather than accepting an external
    split index that wouldn't line up with these windows anyway."""
    if not _HAS_TORCH:
        raise RuntimeError("run_nbeats requires torch (pip install torch); "
                            "not available in this environment.")

    log_close = np.log(close.values)
    X, y = _build_univariate_windows(log_close, seq_len, forecast_days)
    if len(X) < 60:
        raise ValueError(f"only {len(X)} usable windows -- not enough history to train N-BEATS")

    split = int(len(X) * split_ratio)
    X_tr, y_tr, X_te, y_te = X[:split], y[:split], X[split:], y[split:]
    inner = int(split * split_ratio)
    X_in, y_in = X_tr[:inner - forecast_days], y_tr[:inner - forecast_days]
    X_val, y_val = X_tr[inner:], y_tr[inner:]

    def train_once(Xtr, ytr, hidden, lr, n_epochs, seed=42):
        torch.manual_seed(seed)
        model = NBeatsNet(input_size=seq_len, hidden=hidden, forecast_size=forecast_days)
        opt = torch.optim.Adam(model.parameters(), lr=lr)
        loss_fn = nn.MSELoss()
        xb, yb = torch.tensor(Xtr), torch.tensor(ytr)
        model.train()
        for _ in range(n_epochs):
            opt.zero_grad()
            loss = loss_fn(model(xb), yb)
            loss.backward()
            opt.step()
        model.eval()
        return model

    best_params, best_mae = NBEATS_GRID[0], np.inf
    for params in NBEATS_GRID:
        m = train_once(X_in, y_in, params["hidden"], params["lr"], epochs)
        with torch.no_grad():
            preds = m(torch.tensor(X_val)).numpy()
        mae = float(np.mean(np.abs(y_val - preds)))
        if mae < best_mae:
            best_mae, best_params = mae, params

    scored = train_once(X_tr[:split - forecast_days], y_tr[:split - forecast_days],
                         best_params["hidden"], best_params["lr"], epochs)
    with torch.no_grad():
        holdout_pred_logret = scored(torch.tensor(X_te)).numpy()
    holdout_pred = np.exp(np.cumsum(holdout_pred_logret, axis=1)) - 1
    holdout_true = np.exp(np.cumsum(y_te, axis=1)) - 1
    scores = _score_path(holdout_true, holdout_pred)

    final = train_once(X, y, best_params["hidden"], best_params["lr"], epochs)
    last_window = np.diff(log_close)[-seq_len:][None, :].astype(np.float32)
    with torch.no_grad():
        path_logret = final(torch.tensor(last_window)).numpy()[0]
    path = np.exp(np.cumsum(path_logret)) - 1
    # window k's forecast origin is close row seq_len + k (its input ends there)
    origins = close.index[seq_len + split:seq_len + len(X)]
    return {"scores": scores, "path": path, "params": best_params,
            "holdout": _holdout_frame(holdout_pred, origins, forecast_days)}


# --------------------------------------------------------------------------
# 6. Weighted ensemble ("stacking-lite") -- replaces paths.mean(axis=1)
# --------------------------------------------------------------------------

def fit_weighted_ensemble(results: dict, eps: float = 1e-4) -> dict:
    """Inverse-holdout-MAE weights, zeroed out for any model whose holdout
    direction accuracy isn't above chance (<=0.5) -- a below-chance model
    gets no vote no matter how small its MAE looks.

    This is a lighter-weight stand-in for full stacking (a learned per-
    horizon regression on each model's raw holdout predictions): it reuses
    the scores every run_xxx() already returns, so it needs no changes to
    the existing model functions' contracts. A fuller version would fit e.g.
    sklearn.linear_model.Ridge per horizon on each model's holdout_pred
    array against the actual holdout return -- doing that means having each
    run_xxx() also return its raw holdout_pred/holdout_true arrays (they
    currently only return the aggregated scores), which is the natural next
    step if this weighting isn't enough of an improvement over the plain
    mean.
    """
    weights = {}
    for key, res in results.items():
        mae = res["scores"].get("mae", np.nan)
        dacc = res["scores"].get("direction_acc", np.nan)
        if not np.isfinite(mae) or not np.isfinite(dacc) or dacc <= 0.5:
            weights[key] = 0.0
        else:
            weights[key] = 1.0 / (mae + eps)
    total = sum(weights.values())
    if total <= 0:
        # nothing beat chance -- fall back to an equal-weight mean rather
        # than dividing by zero or returning an all-zero ensemble
        n = len(results)
        return {k: 1.0 / n for k in results}
    return {k: v / total for k, v in weights.items()}
