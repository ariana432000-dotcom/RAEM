"""Shared pipeline for the daily Buy/Sell system.

Each company has its own notebook (notebooks/<TICKER>.ipynb) that imports this
module and runs the pipeline for one ticker: 5 models on data/*.csv, one
cumulative HORIZON-day return, one BUY/HOLD/SELL. daily.py is the same thing
from the command line, without the LLM panel.

The 7-day day-by-day price path and its track record live in forecast_lib.py
and Forecast.ipynb -- a different question, different models. Both halves
share price_features() below, so they describe the market the same way.
"""

from __future__ import annotations

import os
import time
import warnings
from datetime import datetime

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

import torch
import torch.nn as nn
import yfinance as yf
from dateutil.relativedelta import relativedelta
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.statespace.structural import UnobservedComponents
from xgboost import XGBRegressor

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

# tz / final_after: a day's bar is only treated as final once the exchange's
# local clock has passed `final_after` (session close plus a margin for the
# closing auction). weekend: weekday numbers (Mon=0) the exchange does not
# trade -- Tadawul and DSE trade Sunday-Thursday.
_US = {"tz": "America/New_York", "final_after": "16:30", "weekend": (5, 6)}
_DSE = {"currency": "BDT", "source": "dse", "tz": "Asia/Dhaka", "final_after": "15:00", "weekend": (4, 5)}

TICKERS = {
    "AAPL":      {"name": "Apple",        "currency": "USD", "source": "yfinance", **_US},
    "IBM":       {"name": "IBM",          "currency": "USD", "source": "yfinance", **_US},
    "2222.SR":   {"name": "Saudi Aramco", "currency": "SAR", "source": "yfinance",
                  "tz": "Asia/Riyadh", "final_after": "15:30", "weekend": (4, 5)},
    "0300.HK":   {"name": "Midea Group",  "currency": "HKD", "source": "yfinance",
                  "tz": "Asia/Hong_Kong", "final_after": "16:30", "weekend": (5, 6)},
    "ABBANK":    {"name": "AB Bank",                              **_DSE},
    "BATBC":     {"name": "British American Tobacco Bangladesh", **_DSE},
    "BPML":      {"name": "Bashundhara Paper Mills",             **_DSE},
    "WALTONHIL": {"name": "Walton Hi-Tech Industries",           **_DSE},
}

HORIZON = 7    # predict cumulative return over the next 7 trading days
SEQ_LEN = 20   # LSTM lookback window (trading days)
SPLIT_RATIO = 0.85

FEATURES = ["lag_ret_1", "lag_ret_2", "lag_ret_3", "lag_ret_5", "lag_ret_10",
            "sma_10", "sma_50", "vol_10", "vol_30", "rsi_14", "vol_chg"]

FX_PAIRS = {"USD": None, "SAR": "USDSAR=X", "HKD": "USDHKD=X", "BDT": "USDBDT=X"}

RUNS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs")


def safe_filename(ticker: str) -> str:
    return ticker.replace(".", "_")


def weekend_days(ticker: str) -> tuple:
    return tuple(TICKERS.get(ticker, {}).get("weekend", (5, 6)))


def last_final_session(ticker: str):
    """The latest exchange-local date whose daily bar is final: today once the
    session has closed, otherwise yesterday. A bar fetched mid-session carries
    a partial close and volume, and top_up_data_csv() never rewrites a row once
    written, so appending one would freeze the wrong price into the file."""
    meta = TICKERS.get(ticker, {})
    now = pd.Timestamp.now(tz=meta.get("tz", "UTC"))
    hh, mm = (int(x) for x in meta.get("final_after", "23:59").split(":"))
    today = now.date()
    return today if (now.hour, now.minute) >= (hh, mm) else (now - pd.Timedelta(days=1)).date()


# --------------------------------------------------------------------------
# Data fetching -- reads each ticker's local 2-year snapshot from data/
# instead of hitting a live API, so the pipeline works fully offline.
# --------------------------------------------------------------------------

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _data_csv_path(ticker: str) -> str:
    import glob as _glob

    matches = _glob.glob(os.path.join(DATA_DIR, f"*_{safe_filename(ticker)}_2y.csv"))
    if not matches:
        raise FileNotFoundError(
            f"No CSV in {DATA_DIR} for ticker {ticker!r} "
            f"(expected a file matching *_{safe_filename(ticker)}_2y.csv)"
        )
    return matches[0]


def load_data_csv(ticker: str, years: int | None = None) -> pd.DataFrame:
    """Normalize this ticker's data/ CSV to the Open/High/Low/Close/Volume
    shape the rest of the pipeline expects. Two source layouts exist: the
    yfinance export (AAPL/IBM/2222.SR/0300.HK) and the DSE/bdshare export
    (the Bangladesh tickers), told apart by their column casing.

    `years=None` (the default) uses every row in the file, so the history
    genuinely grows as top_up_data_csv() appends new trading days. Passing a
    number trims to a rolling window instead -- that used to be the default,
    which meant each appended day silently pushed an equally old day out of
    use and the training set never actually got bigger."""
    df = pd.read_csv(_data_csv_path(ticker))

    if "Date" in df.columns:
        # yfinance export: tz-suffixed timestamps that are always local
        # midnight -- taking just the date portion avoids a UTC-conversion
        # day rollover for positive-offset (e.g. HKD) tickers.
        idx = pd.to_datetime(df["Date"].astype(str).str[:10])
        out = df.set_index(idx)[["Open", "High", "Low", "Close", "Volume"]]
    else:
        idx = pd.to_datetime(df["date"])
        out = df.set_index(idx).rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume",
        })[["Open", "High", "Low", "Close", "Volume"]]
        # drop no-trade days: Volume==0 makes pct_change() produce inf downstream
        out = out[out["Volume"] > 0]

    out = out[~out.index.duplicated(keep="first")].sort_index().dropna()
    if years is not None:
        out = out[out.index >= datetime.now() - relativedelta(years=years)]
    return out


def fetch_history(ticker: str, years: int | None = None, use_cache: bool = True) -> pd.DataFrame:
    """Load prices for `ticker`. `use_cache` is kept for signature
    compatibility with existing callers; reading a local CSV needs no cache."""
    return load_data_csv(ticker, years)


def top_up_data_csv(ticker: str, verbose: bool = True) -> int:
    """Append any trading days that have happened since data/*.csv was last
    written, so the snapshot keeps itself current instead of needing a manual
    re-download every few weeks.

    Only whole new rows are appended. Existing rows are never rewritten --
    the file is rebuilt as (original bytes) + (new lines) and swapped in
    atomically, so a crash or a bad fetch cannot leave a half-written or
    shortened file behind. A .bak copy is kept the first time each file is
    touched.

    Returns the number of rows added (0 if already current or offline).
    """
    path = _data_csv_path(ticker)
    with open(path, encoding="utf-8") as f:
        original = f.read()

    lines = original.splitlines()
    if len(lines) < 2:
        raise ValueError(f"{path} looks empty -- refusing to touch it")

    header = lines[0].split(",")
    if header[0] != "Date":
        # DSE/bdshare layout has no yfinance equivalent to top up from
        if verbose:
            print(f"  {ticker}: not a yfinance-format CSV -- skipping top-up")
        return 0

    last_date = pd.to_datetime(lines[-1].split(",")[0])

    try:
        fresh = yf.Ticker(ticker).history(period="3mo", auto_adjust=True)
    except Exception as e:
        if verbose:
            print(f"  {ticker}: could not reach the price feed ({type(e).__name__}) -- keeping existing data")
        return 0

    if fresh.empty:
        return 0

    new = fresh[fresh.index > last_date]
    # Yahoo sometimes serves a volume-only bar with blank prices; appending it
    # would advance last_date and block the real bar for that day forever.
    new = new.dropna(subset=["Open", "High", "Low", "Close"])
    final_through = last_final_session(ticker)
    in_progress = new[[d.date() > final_through for d in new.index]]
    new = new[[d.date() <= final_through for d in new.index]]
    if new.empty:
        if verbose:
            note = (f" -- {in_progress.index[-1].date()} is still trading, will add it after the close"
                    if not in_progress.empty else "")
            print(f"  {ticker}: already current (last row {last_date.date()}){note}")
        return 0

    # Build the new lines in the file's own column order, so the schema and
    # the tz-suffixed date format stay exactly as they were.
    out_lines = []
    for idx, row in new.iterrows():
        vals = [str(idx)]
        for col in header[1:]:
            vals.append("" if col not in new.columns or pd.isna(row[col]) else str(row[col]))
        out_lines.append(",".join(vals))

    if not os.path.exists(path + ".bak"):
        with open(path + ".bak", "w", encoding="utf-8", newline="") as f:
            f.write(original)

    body = original if original.endswith("\n") else original + "\n"
    updated = body + "\n".join(out_lines) + "\n"

    # atomic swap -- never leave the real file partially written
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(updated)
    if len(updated.splitlines()) <= len(lines):
        os.remove(tmp)
        raise RuntimeError(f"{ticker}: top-up would not grow the file -- aborted")
    os.replace(tmp, path)

    if verbose:
        print(f"  {ticker}: +{len(out_lines)} new trading day(s) "
              f"({new.index[0].date()} -> {new.index[-1].date()})")
    return len(out_lines)


# --------------------------------------------------------------------------
# daily_data/: what each notebook run saw and decided, one row per date.
# Written by forecast_lib.sync_from_trading_logs() out of the notebooks'
# trading logs; read here because the agent's BUY/HOLD/SELL per date is what
# gives the LLM panel a track record (see agent_track_record()).
# --------------------------------------------------------------------------

DAILY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily_data")

DAILY_COLUMNS = ["date", "ticker", "close", "rsi", "macd", "close_50_sma",
                 "boll_ub", "boll_lb", "final_signal", "recorded_at"]


def daily_path(ticker: str) -> str:
    return os.path.join(DAILY_DIR, f"{safe_filename(ticker)}_daily.csv")


def load_daily(ticker: str) -> pd.DataFrame:
    path = daily_path(ticker)
    if not os.path.exists(path):
        return pd.DataFrame(columns=DAILY_COLUMNS)
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=DAILY_COLUMNS)


def top_up_all(verbose: bool = True) -> dict:
    """Top up every active ticker's CSV. Safe to call on every notebook run."""
    added = {}
    for t in TICKERS:
        try:
            added[t] = top_up_data_csv(t, verbose=verbose)
        except Exception as e:
            print(f"  {t}: top-up failed ({type(e).__name__}: {e}) -- existing data untouched")
            added[t] = 0
    return added


def get_fx_rate_to_usd(currency: str) -> float:
    pair = FX_PAIRS.get(currency)
    if pair is None:
        return 1.0
    return float(yf.Ticker(pair).history(period="5d")["Close"].iloc[-1])


# --------------------------------------------------------------------------
# Feature engineering
# --------------------------------------------------------------------------

def price_features(df: pd.DataFrame) -> pd.DataFrame:
    """Close-only technical features, shared by both halves of the system.

    forecast_lib builds on these too, so the daily signal and the 7-day path
    are describing the market the same way rather than drifting apart. Volume
    and the prediction target are deliberately left out -- they differ between
    the two (forecast_lib's daily-log rows have no volume, and it needs one
    target per forecast day rather than a single cumulative one)."""
    d = df.copy()
    d["ret_1d"] = d["Close"].pct_change()
    for lag in [1, 2, 3, 5, 10]:
        d[f"lag_ret_{lag}"] = d["ret_1d"].shift(lag - 1).rolling(lag).sum()
    d["sma_10"] = d["Close"].rolling(10).mean() / d["Close"] - 1
    d["sma_50"] = d["Close"].rolling(50).mean() / d["Close"] - 1
    d["vol_10"] = d["ret_1d"].rolling(10).std()
    d["vol_30"] = d["ret_1d"].rolling(30).std()
    delta = d["Close"].diff()
    up = delta.clip(lower=0).rolling(14).mean()
    down = (-delta.clip(upper=0)).rolling(14).mean()
    d["rsi_14"] = 100 - (100 / (1 + up / down.replace(0, np.nan)))
    return d


def make_features(df: pd.DataFrame) -> pd.DataFrame:
    d = price_features(df)
    d["vol_chg"] = d["Volume"].pct_change().rolling(5).mean()
    d["target"] = d["Close"].shift(-HORIZON) / d["Close"] - 1
    return d


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------

def direction_acc(y_true, y_pred) -> float:
    return float(np.mean(np.sign(y_pred) == np.sign(y_true))) if len(y_true) else float("nan")


XGB_PARAM_GRID = [
    {"max_depth": d, "learning_rate": lr, "n_estimators": n,
     "subsample": 0.8, "colsample_bytree": 0.8, "random_state": 42}
    for d in [2, 3, 4] for lr in [0.02, 0.05] for n in [300, 500]
]

LSTM_PARAM_GRID = [{"hidden": h, "lr": lr} for h in [16, 32, 64] for lr in [1e-3, 5e-4]]
ARIMA_ORDER_GRID = [(1, 1, 0), (2, 1, 0), (1, 1, 1), (2, 1, 1), (5, 1, 0)]

RIDGE_ALPHA_GRID = [0.1, 1.0, 10.0, 100.0]
KALMAN_LEVEL_GRID = ["local level", "local linear trend", "smooth trend"]


CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
TUNING_MAX_AGE_DAYS = 30   # re-tune roughly monthly; hyperparameters drift slowly


def _cache_file(ticker: str, kind: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{safe_filename(ticker)}_{kind}.json")


def _read_cache(ticker: str, kind: str) -> dict | None:
    import json

    path = _cache_file(ticker, kind)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_cache(ticker: str, kind: str, payload: dict) -> None:
    import json

    with open(_cache_file(ticker, kind), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)


def load_tuned_params(ticker: str, max_age_days: int = TUNING_MAX_AGE_DAYS) -> dict | None:
    """Reuse previously-searched hyperparameters.

    Tuning is 2/3 of the pipeline's runtime but the winning configuration barely
    moves from day to day, so it is cached and refreshed on a schedule rather
    than re-searched every run. Returns None once stale, which forces a re-tune.
    """
    cached = _read_cache(ticker, "tuning")
    if not cached:
        return None
    try:
        age = (datetime.now() - datetime.strptime(cached["tuned_on"], "%Y-%m-%d")).days
    except Exception:
        return None
    return None if age > max_age_days else cached


def save_tuned_params(ticker: str, xgb_params: dict, lstm_params: dict, arima_order,
                      data_end_date: str, ridge_params: dict | None = None,
                      kalman_spec: str | None = None) -> None:
    _write_cache(ticker, "tuning", {
        "ticker": ticker,
        "tuned_on": datetime.now().strftime("%Y-%m-%d"),
        "data_end_date": data_end_date,
        "xgb": xgb_params,
        "lstm": lstm_params,
        "arima_order": list(arima_order),
        "ridge": ridge_params,
        "kalman": kalman_spec,
    })


def load_model_results(ticker: str, data_end_date: str, n_rows: int) -> dict | None:
    """Model outputs are deterministic given the same price history, so a re-run
    on unchanged data can skip the fitting entirely.

    Only the *model* half is cached. The memory correction and the final
    BUY/HOLD/SELL are always recomputed, because episodic memory and the agent
    track record can change between runs even when prices have not.
    """
    cached = _read_cache(ticker, "results")
    if not cached:
        return None
    if (cached.get("data_end_date") != data_end_date or cached.get("n_rows") != n_rows
            or cached.get("horizon") != HORIZON):
        return None  # a HORIZON change invalidates predictions fit against the old target
    return cached.get("results")


def save_model_results(ticker: str, data_end_date: str, n_rows: int, results: dict) -> None:
    _write_cache(ticker, "results", {
        "horizon": HORIZON,
        "ticker": ticker, "data_end_date": data_end_date,
        "n_rows": n_rows, "cached_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "results": results,
    })


def clear_cache(ticker: str | None = None) -> int:
    """Force everything to be recomputed on the next run."""
    import glob as _glob

    pattern = f"{safe_filename(ticker)}_*.json" if ticker else "*.json"
    files = _glob.glob(os.path.join(CACHE_DIR, pattern))
    for f in files:
        os.remove(f)
    return len(files)


def cache_status() -> pd.DataFrame:
    """What is currently cached, and how stale it is."""
    rows = []
    for t in TICKERS:
        tune = _read_cache(t, "tuning")
        res = _read_cache(t, "results")
        age = None
        if tune:
            try:
                age = (datetime.now() - datetime.strptime(tune["tuned_on"], "%Y-%m-%d")).days
            except Exception:
                age = None
        rows.append({
            "ticker": t,
            "tuned": bool(tune),
            "tuned_on": tune.get("tuned_on") if tune else "",
            "tuning_age_days": age,
            "tuning_stale": (age is not None and age > TUNING_MAX_AGE_DAYS),
            "results_cached_for": res.get("data_end_date") if res else "",
        })
    return pd.DataFrame(rows).set_index("ticker")


def tune_xgboost(X_train, y_train, n_splits: int = 3):
    """Grid search scored by TimeSeriesSplit CV inside the training portion only."""
    tscv = TimeSeriesSplit(n_splits=n_splits)
    best_params, best_score = XGB_PARAM_GRID[0], np.inf
    for params in XGB_PARAM_GRID:
        fold_maes = []
        for tr_idx, val_idx in tscv.split(X_train):
            m = XGBRegressor(**params)
            m.fit(X_train.iloc[tr_idx], y_train.iloc[tr_idx])
            fold_maes.append(mean_absolute_error(y_train.iloc[val_idx], m.predict(X_train.iloc[val_idx])))
        score = float(np.mean(fold_maes))
        if score < best_score:
            best_score, best_params = score, params
    return best_params


def run_xgboost(labeled: pd.DataFrame, feat: pd.DataFrame, split: int, params: dict | None = None):
    X_train, y_train = labeled[FEATURES].iloc[:split], labeled["target"].iloc[:split]
    X_test, y_test = labeled[FEATURES].iloc[split:], labeled["target"].iloc[split:]

    # `params` from cache skips the grid search -- the single biggest cost here
    best_params = params or tune_xgboost(X_train, y_train)

    model = XGBRegressor(**best_params)
    model.fit(X_train, y_train)
    preds_test = model.predict(X_test)
    mae = mean_absolute_error(y_test, preds_test)
    dacc = direction_acc(y_test.values, preds_test)

    model = XGBRegressor(**best_params)
    model.fit(labeled[FEATURES], labeled["target"])
    pred_return = float(model.predict(feat[FEATURES].dropna().iloc[[-1]])[0])
    return pred_return, mae, dacc, best_params


def tune_ridge(X_train, y_train, n_splits: int = 3):
    """Grid search scored by TimeSeriesSplit CV inside the training portion only."""
    tscv = TimeSeriesSplit(n_splits=n_splits)
    best_alpha, best_score = RIDGE_ALPHA_GRID[0], np.inf
    for alpha in RIDGE_ALPHA_GRID:
        fold_maes = []
        for tr_idx, val_idx in tscv.split(X_train):
            m = Ridge(alpha=alpha)
            m.fit(X_train.iloc[tr_idx], y_train.iloc[tr_idx])
            fold_maes.append(mean_absolute_error(y_train.iloc[val_idx], m.predict(X_train.iloc[val_idx])))
        score = float(np.mean(fold_maes))
        if score < best_score:
            best_score, best_alpha = score, alpha
    return {"alpha": best_alpha}


def run_ridge(labeled: pd.DataFrame, feat: pd.DataFrame, split: int, params: dict | None = None):
    """Linear baseline on the same feature set as XGBoost. Cheap, and a useful
    check: if a linear model matches the tree/deep models, the extra
    complexity of those isn't earning its keep on this ticker."""
    X_train, y_train = labeled[FEATURES].iloc[:split], labeled["target"].iloc[:split]
    X_test, y_test = labeled[FEATURES].iloc[split:], labeled["target"].iloc[split:]

    best_params = params or tune_ridge(X_train, y_train)

    model = Ridge(**best_params)
    model.fit(X_train, y_train)
    preds_test = model.predict(X_test)
    mae = mean_absolute_error(y_test, preds_test)
    dacc = direction_acc(y_test.values, preds_test)

    model = Ridge(**best_params)
    model.fit(labeled[FEATURES], labeled["target"])
    pred_return = float(model.predict(feat[FEATURES].dropna().iloc[[-1]])[0])
    return pred_return, mae, dacc, best_params


def pick_kalman_level(train_log_close):
    best = None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for level in KALMAN_LEVEL_GRID:
            try:
                fit = UnobservedComponents(train_log_close, level=level).fit(disp=False)
                if best is None or fit.aic < best[1]:
                    best = (level, fit.aic)
            except Exception:
                continue
    return best[0] if best else KALMAN_LEVEL_GRID[0]


def run_kalman(raw: pd.DataFrame, labeled: pd.DataFrame, split: int, level: str | None = None):
    """Walk-forward Kalman-filtered state-space model on log(Close): at each
    held-out day, fit only on data up to that day and forecast HORIZON days
    ahead -- same walk-forward discipline as run_arima(), a different model
    (a drifting level/trend updated by the filter, instead of fixed AR terms)."""
    close = raw["Close"].reset_index(drop=True)
    log_close = np.log(close)
    date_to_pos = {d: p for p, d in enumerate(raw.index)}

    if level is None:
        level = pick_kalman_level(log_close.iloc[:date_to_pos[labeled.index[split]]].values)

    preds, actuals = [], []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for d in labeled.index[split:]:
            pos = date_to_pos[d]
            try:
                fit = UnobservedComponents(log_close.iloc[:pos + 1].values, level=level).fit(disp=False)
                fc = fit.forecast(steps=HORIZON)
                preds.append(float(np.exp(fc[-1] - log_close.iloc[pos]) - 1))
            except Exception:
                continue
            actuals.append(labeled.loc[d, "target"])

        mae = mean_absolute_error(actuals, preds) if preds else float("nan")
        dacc = direction_acc(np.array(actuals), np.array(preds))

        fit_full = UnobservedComponents(log_close.values, level=level).fit(disp=False)
        fc_full = fit_full.forecast(steps=HORIZON)
        pred_return_final = float(np.exp(fc_full[-1] - log_close.iloc[-1]) - 1)
    return pred_return_final, mae, dacc, level


class LSTMNet(nn.Module):
    def __init__(self, n_features: int, hidden: int = 32):
        super().__init__()
        self.lstm = nn.LSTM(n_features, hidden, num_layers=1, batch_first=True)
        self.head = nn.Linear(hidden, 1)

    def forward(self, x):
        out, _ = self.lstm(x)
        return self.head(out[:, -1, :]).squeeze(-1)


def build_sequences(feat_scaled, targets, seq_len: int):
    X, y = [], []
    for i in range(seq_len, len(feat_scaled)):
        if np.isnan(targets[i]):
            continue
        X.append(feat_scaled[i - seq_len:i])
        y.append(targets[i])
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)


def train_lstm_once(X_train, y_train, n_features, hidden, lr, epochs, seed=42):
    torch.manual_seed(seed)
    model = LSTMNet(n_features=n_features, hidden=hidden)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    lossf = nn.MSELoss()
    Xtr, ytr = torch.tensor(X_train), torch.tensor(y_train)
    for _ in range(epochs):
        model.train()
        opt.zero_grad()
        lossf(model(Xtr), ytr).backward()
        opt.step()
    return model


def tune_lstm(X_train_full, y_train_full, n_features, epochs: int = 100):
    """Inner train/validation split carved from the training portion only --
    the true holdout is never touched here."""
    inner_split = int(len(X_train_full) * SPLIT_RATIO)
    X_tr, y_tr = X_train_full[:inner_split], y_train_full[:inner_split]
    X_val, y_val = X_train_full[inner_split:], y_train_full[inner_split:]

    best_params, best_score = LSTM_PARAM_GRID[0], np.inf
    for params in LSTM_PARAM_GRID:
        model = train_lstm_once(X_tr, y_tr, n_features, params["hidden"], params["lr"], epochs)
        model.eval()
        with torch.no_grad():
            preds_val = model(torch.tensor(X_val)).numpy() if len(X_val) else np.array([])
        score = mean_absolute_error(y_val, preds_val) if len(X_val) else np.inf
        if score < best_score:
            best_score, best_params = score, params
    return best_params


def run_lstm(feat: pd.DataFrame, epochs: int = 150, seed: int = 42, params: dict | None = None):
    valid = feat.dropna(subset=FEATURES)
    scaler = StandardScaler()
    scaled_all = scaler.fit_transform(valid[FEATURES])

    X, y = build_sequences(scaled_all, valid["target"].values, SEQ_LEN)
    split = int(len(X) * SPLIT_RATIO)
    X_train, y_train = X[:split], y[:split]
    X_test, y_test = X[split:], y[split:]

    best_params = params or tune_lstm(X_train, y_train, n_features=len(FEATURES))

    model = train_lstm_once(X_train, y_train, len(FEATURES),
                            best_params["hidden"], best_params["lr"], epochs, seed=seed)
    model.eval()
    with torch.no_grad():
        preds_test = model(torch.tensor(X_test)).numpy() if len(X_test) else np.array([])
    mae = mean_absolute_error(y_test, preds_test) if len(X_test) else float("nan")
    dacc = direction_acc(y_test, preds_test)

    last_window = scaler.transform(feat[FEATURES].dropna().iloc[-SEQ_LEN:])
    with torch.no_grad():
        pred_return = float(model(torch.tensor(last_window[None, :, :].astype(np.float32))).numpy()[0])
    return pred_return, mae, dacc, best_params


def pick_arima_order(train_log_close):
    best = None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for order in ARIMA_ORDER_GRID:
            try:
                fit = ARIMA(train_log_close, order=order).fit()
                if best is None or fit.aic < best[1]:
                    best = (order, fit.aic)
            except Exception:
                continue
    return best[0] if best else (1, 1, 0)


def run_arima(raw: pd.DataFrame, labeled: pd.DataFrame, split: int, order=None):
    """Walk-forward ARIMA on log(Close): at each held-out day, fit only on data
    up to that day and forecast HORIZON days ahead."""
    close = raw["Close"].reset_index(drop=True)
    log_close = np.log(close)
    date_to_pos = {d: p for p, d in enumerate(raw.index)}

    if order is None:
        order = pick_arima_order(log_close.iloc[:date_to_pos[labeled.index[split]]].values)
    order = tuple(order)

    preds, actuals = [], []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for d in labeled.index[split:]:
            pos = date_to_pos[d]
            try:
                fit = ARIMA(log_close.iloc[:pos + 1].values, order=order).fit()
                fc = fit.forecast(steps=HORIZON)
                preds.append(float(np.exp(fc[-1] - log_close.iloc[pos]) - 1))
            except Exception:
                continue
            actuals.append(labeled.loc[d, "target"])

        mae = mean_absolute_error(actuals, preds) if preds else float("nan")
        dacc = direction_acc(np.array(actuals), np.array(preds))

        fit_full = ARIMA(log_close.values, order=order).fit()
        fc_full = fit_full.forecast(steps=HORIZON)
        pred_return_final = float(np.exp(fc_full[-1] - log_close.iloc[-1]) - 1)
    return pred_return_final, mae, dacc, order


# --------------------------------------------------------------------------
# Run log (one CSV per company, one row appended per run)
# --------------------------------------------------------------------------

RUN_LOG_COLUMNS = [
    # --- identity / inputs ---
    "run_id", "run_timestamp", "ticker", "company", "currency", "data_source",
    "data_start_date", "data_end_date", "n_rows",
    "horizon_days", "seq_len", "split_ratio", "n_features", "features",
    "train_rows", "holdout_rows",
    "xgb_grid_size", "lstm_grid_size", "arima_grid_size", "ridge_grid_size",
    # --- outputs: per model ---
    "xgb_pred_return", "xgb_holdout_mae", "xgb_direction_acc", "xgb_best_params",
    "lstm_pred_return", "lstm_holdout_mae", "lstm_direction_acc", "lstm_best_params",
    "arima_pred_return", "arima_holdout_mae", "arima_direction_acc", "arima_order",
    "ridge_pred_return", "ridge_holdout_mae", "ridge_direction_acc", "ridge_best_params",
    "kalman_pred_return", "kalman_holdout_mae", "kalman_direction_acc", "kalman_level",
    # --- outputs: model decision (before memory) ---
    "winner_model", "winner_direction_acc", "raw_pred_return_7d",
    # --- outputs: after episodic-memory self-correction ---
    "pred_return_7d", "pred_price_7d",
    "memory_n_similar", "memory_similar_hit_rate", "memory_mean_residual",
    "memory_correction", "memory_correction_applied", "memory_note",
    # --- context ---
    "last_price", "fx_rate_to_usd", "last_price_usd", "volatility_30d",
    "all_5_agree_on_direction", "cache_mode", "runtime_seconds",
    # --- final BUY/HOLD/SELL decision (model + earned agent weight) ---
    "final_signal", "final_expected_return", "signal_threshold", "signal_basis",
    "agent_blend_weight", "agent_graded_rows", "agent_hit_rate", "agent_edge",
    # --- graded later by backfill_actuals() ---
    "actual_return_1d", "actual_price_1d", "signal_correct_1d",
    "actual_return_7d", "actual_price_7d", "direction_was_correct",
    "raw_direction_was_correct", "signal_correct_7d", "signal_pnl_7d",
    "outcome_status",
]

# NOTE: decide_signal()/backfill_actuals()/log_performance() below still
# reference "agent_*" fields (agent_direction, agent_final_signal, etc.) via
# .get() with defaults, so they degrade gracefully without a dedicated agent
# column block here -- no live caller currently populates those columns.
RUN_LOG_COLUMNS = RUN_LOG_COLUMNS + ["agent_direction_was_correct"]


def run_log_path(ticker: str) -> str:
    # No makedirs here -- load_run_log() calls this too, and creating the
    # directory just to read a file that does not exist leaves an empty folder
    # behind on every run. save_run_log() creates it when there is something
    # to write.
    return os.path.join(RUNS_DIR, f"{safe_filename(ticker)}_runs.csv")


def load_run_log(ticker: str) -> pd.DataFrame:
    path = run_log_path(ticker)
    if not os.path.exists(path):
        return pd.DataFrame(columns=RUN_LOG_COLUMNS)
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=RUN_LOG_COLUMNS)


def save_run_log(ticker: str, df: pd.DataFrame) -> None:
    os.makedirs(RUNS_DIR, exist_ok=True)
    df.reindex(columns=RUN_LOG_COLUMNS).to_csv(run_log_path(ticker), index=False)


def append_run(ticker: str, record: dict) -> pd.DataFrame:
    """Append one run's row. Never overwrites earlier rows -- the log accumulates
    so past predictions can be graded and studied later."""
    log = load_run_log(ticker)
    row = pd.DataFrame([record]).reindex(columns=RUN_LOG_COLUMNS)
    log = pd.concat([log, row], ignore_index=True)
    save_run_log(ticker, log)
    return log


def backfill_actuals(ticker: str, price_df: pd.DataFrame) -> int:
    """Grade past predictions whose HORIZON has now elapsed.

    For each PENDING row, find its data_end_date in the freshly fetched price
    history; if HORIZON more trading days exist after it, record what actually
    happened and whether the predicted direction was right. Both the
    memory-corrected prediction and the raw model prediction are graded, so you
    can see whether the self-correction is actually helping.
    """
    log = load_run_log(ticker)
    if log.empty:
        return 0

    closes = price_df["Close"]
    pos_of = {str(d.date()): i for i, d in enumerate(price_df.index)}

    filled = 0
    for i, row in log.iterrows():
        if str(row.get("outcome_status", "")) == "GRADED":
            continue
        end_date = str(row.get("data_end_date", ""))
        if end_date not in pos_of:
            continue
        pos = pos_of[end_date]
        if pos + HORIZON >= len(closes):
            log.at[i, "outcome_status"] = "PENDING"
            continue

        base = float(closes.iloc[pos])

        # next-day check first: faster feedback than waiting the full horizon
        if pos + 1 < len(closes):
            nxt = float(closes.iloc[pos + 1])
            r1 = nxt / base - 1
            log.at[i, "actual_return_1d"] = r1
            log.at[i, "actual_price_1d"] = nxt
            sig = str(row.get("final_signal", "") or "")
            if sig in ("BUY", "SELL"):
                log.at[i, "signal_correct_1d"] = bool(
                    (sig == "BUY" and r1 > 0) or (sig == "SELL" and r1 < 0))

        future = float(closes.iloc[pos + HORIZON])
        actual_ret = future / base - 1
        log.at[i, "actual_return_7d"] = actual_ret
        log.at[i, "actual_price_7d"] = future

        # grade the actionable BUY/HOLD/SELL call over the full horizon
        sig = str(row.get("final_signal", "") or "")
        if sig in ("BUY", "SELL"):
            log.at[i, "signal_correct_7d"] = bool(
                (sig == "BUY" and actual_ret > 0) or (sig == "SELL" and actual_ret < 0))
            log.at[i, "signal_pnl_7d"] = actual_ret if sig == "BUY" else -actual_ret
        elif sig == "HOLD":
            log.at[i, "signal_pnl_7d"] = 0.0

        predicted = row.get("pred_return_7d")
        if pd.notna(predicted):
            log.at[i, "direction_was_correct"] = bool(np.sign(float(predicted)) == np.sign(actual_ret))
        raw_pred = row.get("raw_pred_return_7d")
        if pd.notna(raw_pred):
            log.at[i, "raw_direction_was_correct"] = bool(np.sign(float(raw_pred)) == np.sign(actual_ret))

        # grade the LLM panel's call too (HOLD is scored as no directional call)
        agent_dir = str(row.get("agent_direction", "") or "")
        if agent_dir in ("UP", "DOWN"):
            log.at[i, "agent_direction_was_correct"] = bool(
                (agent_dir == "UP" and actual_ret > 0) or (agent_dir == "DOWN" and actual_ret < 0))

        log.at[i, "outcome_status"] = "GRADED"
        filled += 1

    save_run_log(ticker, log)
    return filled


# --------------------------------------------------------------------------
# BUY / HOLD / SELL decision
# --------------------------------------------------------------------------

SIGNAL_THRESHOLD_SIGMAS = 0.5  # move must beat this share of a HORIZON-day sigma to be actionable
AGENT_FEATURE_MIN_GRADED = 10  # graded agent rows needed before the agent gets any weight


def signal_threshold(vol_30: float) -> float:
    """A move only counts as BUY/SELL if it is big enough to matter relative to
    the stock's own noise. A 0.3% forecast on a stock that moves 5% a week is
    not a trade signal."""
    if not vol_30 or not np.isfinite(vol_30) or vol_30 <= 0:
        return 0.01
    return SIGNAL_THRESHOLD_SIGMAS * float(vol_30) * np.sqrt(HORIZON)


def return_to_signal(pred_return: float, vol_30: float) -> str:
    thr = signal_threshold(vol_30)
    if pred_return > thr:
        return "BUY"
    if pred_return < -thr:
        return "SELL"
    return "HOLD"


def grade_agent_calls(ticker: str, price_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Score every BUY/SELL the LLM panel has made, against what the price
    actually did HORIZON trading days later.

    The panel's calls live in daily_data/ (one row per date, written from the
    notebooks' trading logs). A call can only be graded once HORIZON more
    trading days exist after it, so recent calls stay ungraded until then.
    HOLD is not a directional call and is left out of the scoring -- counting
    it either way would flatter or punish the panel for declining to commit.
    """
    daily = load_daily(ticker)
    cols = ["date", "final_signal", "close_then", "close_later", "actual_return", "was_correct"]
    if daily.empty or "final_signal" not in daily:
        return pd.DataFrame(columns=cols)

    if price_df is None:
        price_df = load_data_csv(ticker)
    closes = price_df["Close"]
    pos_of = {str(d.date()): i for i, d in enumerate(price_df.index)}

    rows = []
    for _, r in daily.iterrows():
        sig = str(r.get("final_signal", "") or "").upper()
        if sig not in ("BUY", "SELL"):
            continue
        d = str(r["date"])
        if d not in pos_of:
            continue                       # the panel ran on a non-trading day
        i = pos_of[d]
        if i + HORIZON >= len(closes):
            continue                       # not enough future yet to grade it
        then, later = float(closes.iloc[i]), float(closes.iloc[i + HORIZON])
        ret = later / then - 1
        rows.append({
            "date": d, "final_signal": sig, "close_then": then, "close_later": later,
            "actual_return": ret,
            "was_correct": bool((sig == "BUY" and ret > 0) or (sig == "SELL" and ret < 0)),
        })
    return pd.DataFrame(rows, columns=cols)


def agent_track_record(ticker: str, price_df: pd.DataFrame | None = None) -> dict:
    """How good has the LLM panel actually been on this ticker so far?

    Returns the measured hit rate over graded calls and the weight the panel's
    opinion has earned. Below AGENT_FEATURE_MIN_GRADED graded calls the weight
    is zero -- an opinion with no track record gets no vote, however confident
    it sounds. It also stays zero if the measured hit rate is no better than a
    coin flip, so a panel that has proven itself wrong cannot move the number.
    """
    out = {"agent_graded_rows": 0, "agent_hit_rate": np.nan, "agent_weight": 0.0,
           "agent_edge": np.nan}

    graded = grade_agent_calls(ticker, price_df)
    n = len(graded)
    out["agent_graded_rows"] = n
    if n == 0:
        return out

    hit = float(graded["was_correct"].mean())
    out["agent_hit_rate"] = round(hit, 3)
    out["agent_edge"] = round(hit - 0.5, 3)

    if n >= AGENT_FEATURE_MIN_GRADED and hit > 0.5:
        # weight grows with both the measured edge and the sample size behind it
        confidence = n / (n + AGENT_FEATURE_MIN_GRADED)
        out["agent_weight"] = round(min(0.5, 2 * (hit - 0.5) * confidence), 3)
    return out


def decide_signal(ticker: str, model_return: float, vol_30: float,
                  agent_signal: str = "", agent_expected_return: float = np.nan,
                  price_df: pd.DataFrame | None = None) -> dict:
    """Final BUY / HOLD / SELL.

    Starts from the quant model's forecast. The LLM panel's view is blended in
    only to the extent it has earned it on this ticker's own graded history --
    see agent_track_record(). With no track record the agents inform you but do
    not move the number, which is the honest default: on run 1 there is no
    evidence the panel is any good.
    """
    tr = agent_track_record(ticker, price_df)
    weight = tr["agent_weight"]

    agent_ret = agent_expected_return
    if not np.isfinite(agent_ret) and agent_signal in ("BUY", "SELL", "HOLD"):
        # no number given: read the categorical call as +/-1 threshold, or flat
        thr = signal_threshold(vol_30)
        agent_ret = {"BUY": thr, "SELL": -thr, "HOLD": 0.0}[agent_signal]

    if weight > 0 and np.isfinite(agent_ret):
        blended = (1 - weight) * model_return + weight * float(agent_ret)
        basis = (f"model {model_return:+.2%} blended with agent {agent_ret:+.2%} "
                 f"at weight {weight:.2f} (agent hit rate {tr['agent_hit_rate']:.0%} "
                 f"over {tr['agent_graded_rows']} graded runs)")
    else:
        blended = model_return
        if tr["agent_graded_rows"] < AGENT_FEATURE_MIN_GRADED:
            basis = (f"model only: agent has {tr['agent_graded_rows']} graded run(s), needs "
                     f"{AGENT_FEATURE_MIN_GRADED} before its opinion is given any weight")
        else:
            basis = (f"model only: agent hit rate {tr['agent_hit_rate']:.0%} is not above "
                     f"chance, so its opinion is given no weight")

    thr = signal_threshold(vol_30)
    signal = return_to_signal(blended, vol_30)
    return {
        "final_signal": signal,
        "final_expected_return": blended,
        "signal_threshold": thr,
        "signal_basis": basis,
        "agent_blend_weight": weight,
        **tr,
    }


# --------------------------------------------------------------------------
# Episodic memory (ChromaDB): embed each run's market state, recall similar
# past states, and self-correct the prediction from what went wrong before.
# --------------------------------------------------------------------------

CHROMA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chroma_memory")
MEMORY_COLLECTION = "prediction_episodes"
MEMORY_TOP_K = 10            # how many similar past states to consult
MEMORY_MIN_EPISODES = 5      # below this, do not correct -- too little evidence
MEMORY_SHRINK_PRIOR = 5.0    # shrinkage strength: weight = n / (n + prior)
MEMORY_MIN_SIMILARITY = 0.2  # ignore recalled states that aren't actually similar
MEMORY_OUTLIER_SIGMAS = 3.0  # clip each residual to this many HORIZON-day sigmas
MEMORY_MAX_CORRECTION_SIGMAS = 1.0  # memory may nudge, not replace, the forecast


def get_memory_collection():
    import chromadb

    os.makedirs(CHROMA_DIR, exist_ok=True)
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    # cosine distance: we care about the shape of the market state, not its magnitude
    return client.get_or_create_collection(
        name=MEMORY_COLLECTION, metadata={"hnsw:space": "cosine"})


def state_embedding(feat: pd.DataFrame) -> list[float]:
    """Embed 'what the market looks like today' as a vector.

    The 11 engineered features are z-scored against the ticker's own 2-year
    window, which puts RSI (0-100) and daily returns (~0.01) on a comparable
    scale so neither dominates the distance. The window shifts by only a few
    days between runs, so this embedding space stays consistent run-to-run.

    Note: a numeric feature vector is used deliberately rather than a text
    embedding (e.g. nomic-embed-text). Text embeddings of numbers cluster by
    wording, not by numeric proximity, so they are the wrong tool for
    "find me past days that looked like today".
    """
    valid = feat.dropna(subset=FEATURES)
    mu = valid[FEATURES].mean()
    sigma = valid[FEATURES].std().replace(0, 1.0)
    z = (valid[FEATURES].iloc[-1] - mu) / sigma
    return [float(v) for v in z.fillna(0.0).values]


def _episode_metadata(ticker: str, record: dict) -> dict:
    """Chroma metadata must be str/int/float/bool -- no None or NaN."""
    return {
        "ticker": str(ticker),
        "run_id": str(record["run_id"]),
        "data_end_date": str(record["data_end_date"]),
        "winner_model": str(record["winner_model"]),
        "winner_direction_acc": float(record["winner_direction_acc"]),
        "raw_pred_return": float(record["raw_pred_return_7d"]),
        "pred_return": float(record["pred_return_7d"]),
        "last_price": float(record["last_price"]),
        "volatility_30d": float(record["volatility_30d"]),
        "outcome_status": "PENDING",
    }


def remember_episode(ticker: str, record: dict, embedding: list[float]) -> str:
    """Store this run as an episode so future runs can recall it."""
    col = get_memory_collection()
    ep_id = f"{safe_filename(ticker)}__{record['run_id']}"
    doc = (f"{record['company']} ({ticker}) on {record['data_end_date']}: "
           f"{record['winner_model']} predicted {record['pred_return_7d']:+.2%} "
           f"over {HORIZON} trading days from {record['last_price']:.2f} "
           f"{record['currency']}; 30d vol {record['volatility_30d']:.4f}.")
    col.upsert(ids=[ep_id], embeddings=[embedding], documents=[doc],
               metadatas=[_episode_metadata(ticker, record)])
    return ep_id


def grade_memory(ticker: str, price_df: pd.DataFrame) -> int:
    """Write realized outcomes back onto PENDING episodes, so recall returns
    states whose consequences are actually known."""
    col = get_memory_collection()
    pending = col.get(where={"$and": [{"ticker": {"$eq": ticker}},
                                      {"outcome_status": {"$eq": "PENDING"}}]})
    if not pending["ids"]:
        return 0

    closes = price_df["Close"]
    pos_of = {str(d.date()): i for i, d in enumerate(price_df.index)}

    ids, metas = [], []
    for ep_id, meta in zip(pending["ids"], pending["metadatas"]):
        end_date = str(meta.get("data_end_date", ""))
        if end_date not in pos_of:
            continue
        pos = pos_of[end_date]
        if pos + HORIZON >= len(closes):
            continue

        base, future = float(closes.iloc[pos]), float(closes.iloc[pos + HORIZON])
        actual = future / base - 1
        updated = dict(meta)
        updated.update({
            "outcome_status": "GRADED",
            "actual_return": float(actual),
            "residual": float(actual - float(meta.get("pred_return", 0.0))),
            "direction_correct": bool(np.sign(float(meta.get("pred_return", 0.0))) == np.sign(actual)),
        })
        ids.append(ep_id)
        metas.append(updated)

    if ids:
        col.update(ids=ids, metadatas=metas)
    return len(ids)


def recall_similar(ticker: str, embedding: list[float], k: int = MEMORY_TOP_K) -> pd.DataFrame:
    """Retrieve the most similar GRADED past states for this ticker."""
    col = get_memory_collection()
    if col.count() == 0:
        return pd.DataFrame()

    res = col.query(
        query_embeddings=[embedding],
        n_results=k,
        where={"$and": [{"ticker": {"$eq": ticker}},
                        {"outcome_status": {"$eq": "GRADED"}}]},
    )
    if not res["ids"] or not res["ids"][0]:
        return pd.DataFrame()

    rows = []
    for ep_id, dist, meta in zip(res["ids"][0], res["distances"][0], res["metadatas"][0]):
        rows.append({
            "episode": ep_id,
            "data_end_date": meta.get("data_end_date"),
            "similarity": round(1.0 - float(dist), 4),
            "winner_model": meta.get("winner_model"),
            "pred_return": meta.get("pred_return"),
            "actual_return": meta.get("actual_return"),
            "residual": meta.get("residual"),
            "direction_correct": meta.get("direction_correct"),
        })
    return pd.DataFrame(rows)


def self_correct(raw_pred: float, similar: pd.DataFrame, vol_30: float | None = None) -> dict:
    """Adjust today's prediction using how wrong the model was in similar past states.

    Correction = similarity-weighted mean residual (actual - predicted) over the
    recalled episodes, then shrunk toward zero by n / (n + MEMORY_SHRINK_PRIOR).

    Three guards keep this from doing more harm than good:
      1. Only episodes above MEMORY_MIN_SIMILARITY count at all -- cosine
         distance can exceed 1, so "nearest" neighbours may still be unlike
         today, and those must not silently pad the evidence count.
      2. Each residual is clipped to +/- MEMORY_OUTLIER_SIGMAS HORIZON-day sigmas, so
         one freak move (or an unadjusted corporate action in the price feed)
         cannot dominate the average.
      3. The final correction is capped at MEMORY_MAX_CORRECTION_SIGMAS HORIZON-day
         sigmas, so memory nudges the model's forecast instead of replacing it.
    Below MEMORY_MIN_EPISODES qualifying episodes nothing is corrected -- with
    that little history any "correction" is just fitting noise.
    """
    out = {
        "memory_n_similar": 0,
        "memory_similar_hit_rate": np.nan,
        "memory_mean_residual": np.nan,
        "memory_correction": 0.0,
        "memory_correction_applied": False,
        "memory_note": "",
        "corrected_pred": raw_pred,
    }

    if similar.empty:
        out["memory_note"] = "No graded episodes in memory yet -- prediction left as-is."
        return out

    usable = similar.dropna(subset=["residual"])
    usable = usable[usable["similarity"].astype(float) >= MEMORY_MIN_SIMILARITY]
    n = len(usable)
    out["memory_n_similar"] = n

    if n == 0:
        out["memory_note"] = (f"{len(similar)} episode(s) recalled but none above "
                              f"similarity {MEMORY_MIN_SIMILARITY} -- nothing comparable to today.")
        return out

    hits = _as_bool_series(usable["direction_correct"])
    out["memory_similar_hit_rate"] = round(float(hits.mean()), 3)

    if n < MEMORY_MIN_EPISODES:
        out["memory_note"] = (f"Only {n} comparable graded episode(s); need "
                              f"{MEMORY_MIN_EPISODES} before trusting a correction.")
        return out

    # expected size of a HORIZON-day move, used to scale the outlier clip and the cap
    sigma_h = (float(vol_30) * np.sqrt(HORIZON)) if vol_30 and np.isfinite(vol_30) and vol_30 > 0 else None

    residuals = usable["residual"].astype(float)
    if sigma_h:
        residuals = residuals.clip(-MEMORY_OUTLIER_SIGMAS * sigma_h, MEMORY_OUTLIER_SIGMAS * sigma_h)

    weights = usable["similarity"].astype(float).clip(lower=0.0)
    if weights.sum() <= 0:
        weights = pd.Series(np.ones(n), index=usable.index)
    mean_residual = float(np.average(residuals, weights=weights))

    shrink = n / (n + MEMORY_SHRINK_PRIOR)
    correction = shrink * mean_residual
    capped = False
    if sigma_h:
        cap = MEMORY_MAX_CORRECTION_SIGMAS * sigma_h
        if abs(correction) > cap:
            correction = float(np.sign(correction) * cap)
            capped = True

    out["memory_mean_residual"] = mean_residual
    out["memory_correction"] = correction
    out["memory_correction_applied"] = True
    out["corrected_pred"] = raw_pred + correction
    out["memory_note"] = (
        f"In {n} comparable past state(s) the model was off by {mean_residual:+.2%} on average "
        f"(direction right {out['memory_similar_hit_rate']:.0%} of the time); "
        f"applied {correction:+.2%} after {shrink:.2f} shrinkage"
        + (" (capped at 1 sigma)" if capped else "") + "."
    )
    return out


def apply_memory_correction(ticker: str, record: dict, embedding: list[float]) -> tuple[dict, pd.DataFrame]:
    """Recall similar states, self-correct the prediction, and update `record`."""
    similar = recall_similar(ticker, embedding)
    corr = self_correct(record["raw_pred_return_7d"], similar, vol_30=record.get("volatility_30d"))

    record.update({k: v for k, v in corr.items() if k != "corrected_pred"})
    record["pred_return_7d"] = corr["corrected_pred"]
    record["pred_price_7d"] = record["last_price"] * (1 + corr["corrected_pred"])
    return record, similar


def memory_stats(ticker: str) -> pd.DataFrame:
    """How much memory exists for this ticker, and how good it has been."""
    col = get_memory_collection()
    if col.count() == 0:
        return pd.DataFrame([{"episodes": 0, "graded": 0, "pending": 0}])

    allep = col.get(where={"ticker": {"$eq": ticker}})
    metas = allep["metadatas"] or []
    graded = [m for m in metas if m.get("outcome_status") == "GRADED"]
    row = {
        "episodes": len(metas),
        "graded": len(graded),
        "pending": len(metas) - len(graded),
    }
    if graded:
        correct = [bool(m.get("direction_correct")) for m in graded]
        row["memory_hit_rate"] = round(sum(correct) / len(correct), 3)
        row["mean_residual"] = round(float(np.mean([float(m.get("residual", 0.0)) for m in graded])), 5)
    return pd.DataFrame([row])


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def run_pipeline(ticker: str, years: int | None = None, verbose: bool = True,
                 use_cache: bool = True, retune: bool = False,
                 raw: pd.DataFrame | None = None) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    """Fetch -> features -> 5 models -> one log-ready record.

    Caching (use_cache=True):
      * unchanged price data  -> reuse the whole model result, no fitting at all
      * new data, fresh tuning cache -> refit with the stored hyperparameters,
        skipping the grid search (which is ~2/3 of the runtime)
      * stale/absent tuning cache, or retune=True -> full search
    """
    if ticker not in TICKERS:
        raise ValueError(f"Unknown ticker {ticker!r}. Known: {list(TICKERS)}")

    meta = TICKERS[ticker]
    started = time.time()
    run_dt = datetime.now()

    if raw is None:
        raw = fetch_history(ticker, years)
    data_end_date = str(raw.index[-1].date())
    if verbose:
        print(f"{ticker} ({meta['name']}): {len(raw)} rows, "
              f"{raw.index[0].date()} to {data_end_date}  [{meta['source']}]")

    feat = make_features(raw)
    labeled = feat.dropna(subset=FEATURES + ["target"])
    split = int(len(labeled) * SPLIT_RATIO)

    cached_results = load_model_results(ticker, data_end_date, len(raw)) if (use_cache and not retune) else None
    if cached_results and "ridge_ret" not in cached_results:
        cached_results = None  # older cache predates Ridge/Kalman -- refit
    cache_mode = "full-cache"

    if cached_results:
        if verbose:
            print("Model results cached for this exact data -- skipping refit.")
        xgb_ret = cached_results["xgb_ret"]; xgb_mae = cached_results["xgb_mae"]; xgb_dacc = cached_results["xgb_dacc"]
        lstm_ret = cached_results["lstm_ret"]; lstm_mae = cached_results["lstm_mae"]; lstm_dacc = cached_results["lstm_dacc"]
        arima_ret = cached_results["arima_ret"]; arima_mae = cached_results["arima_mae"]; arima_dacc = cached_results["arima_dacc"]
        ridge_ret = cached_results["ridge_ret"]; ridge_mae = cached_results["ridge_mae"]; ridge_dacc = cached_results["ridge_dacc"]
        kalman_ret = cached_results["kalman_ret"]; kalman_mae = cached_results["kalman_mae"]; kalman_dacc = cached_results["kalman_dacc"]
        xgb_params = cached_results["xgb_params"]; lstm_params = cached_results["lstm_params"]
        ridge_params = cached_results["ridge_params"]; kalman_level = cached_results["kalman_level"]
        arima_order = tuple(cached_results["arima_order"])
    else:
        tuned = load_tuned_params(ticker) if (use_cache and not retune) else None
        if tuned:
            cache_mode = "tuned-cache"
            if verbose:
                print(f"Reusing hyperparameters tuned on {tuned['tuned_on']} -- refitting on current data ...")
            xgb_p, lstm_p, ar_o = tuned["xgb"], tuned["lstm"], tuple(tuned["arima_order"])
            ridge_p, kalman_lvl = tuned.get("ridge"), tuned.get("kalman")  # older caches predate these
        else:
            cache_mode = "full-tune"
            if verbose:
                print("Tuning + training XGBoost / LSTM / ARIMA / Ridge / Kalman (full hyperparameter search) ...")
            xgb_p = lstm_p = ar_o = ridge_p = kalman_lvl = None

        xgb_ret, xgb_mae, xgb_dacc, xgb_params = run_xgboost(labeled, feat, split, params=xgb_p)
        lstm_ret, lstm_mae, lstm_dacc, lstm_params = run_lstm(feat, params=lstm_p)
        arima_ret, arima_mae, arima_dacc, arima_order = run_arima(raw, labeled, split, order=ar_o)
        ridge_ret, ridge_mae, ridge_dacc, ridge_params = run_ridge(labeled, feat, split, params=ridge_p)
        kalman_ret, kalman_mae, kalman_dacc, kalman_level = run_kalman(raw, labeled, split, level=kalman_lvl)

        if use_cache:
            if not tuned or not tuned.get("ridge") or not tuned.get("kalman"):
                save_tuned_params(ticker, xgb_params, lstm_params, arima_order, data_end_date,
                                  ridge_params, kalman_level)
            save_model_results(ticker, data_end_date, len(raw), {
                "xgb_ret": xgb_ret, "xgb_mae": xgb_mae, "xgb_dacc": xgb_dacc, "xgb_params": xgb_params,
                "lstm_ret": lstm_ret, "lstm_mae": lstm_mae, "lstm_dacc": lstm_dacc, "lstm_params": lstm_params,
                "arima_ret": arima_ret, "arima_mae": arima_mae, "arima_dacc": arima_dacc,
                "arima_order": list(arima_order),
                "ridge_ret": ridge_ret, "ridge_mae": ridge_mae, "ridge_dacc": ridge_dacc,
                "ridge_params": ridge_params,
                "kalman_ret": kalman_ret, "kalman_mae": kalman_mae, "kalman_dacc": kalman_dacc,
                "kalman_level": kalman_level,
            })

    candidates = {
        "xgboost": (xgb_ret, xgb_dacc),
        "lstm": (lstm_ret, lstm_dacc),
        "arima": (arima_ret, arima_dacc),
        "ridge": (ridge_ret, ridge_dacc),
        "kalman": (kalman_ret, kalman_dacc),
    }
    winner = max(candidates, key=lambda k: candidates[k][1])
    winner_ret, winner_dacc = candidates[winner]

    last_price = float(raw["Close"].iloc[-1])
    fx = get_fx_rate_to_usd(meta["currency"])
    signs = {np.sign(xgb_ret), np.sign(lstm_ret), np.sign(arima_ret),
             np.sign(ridge_ret), np.sign(kalman_ret)}

    record = {
        "run_id": run_dt.strftime("%Y-%m-%d_%H-%M-%S"),
        "run_timestamp": run_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "ticker": ticker,
        "company": meta["name"],
        "currency": meta["currency"],
        "data_source": meta["source"],
        "data_start_date": str(raw.index[0].date()),
        "data_end_date": str(raw.index[-1].date()),
        "n_rows": len(raw),
        "horizon_days": HORIZON,
        "seq_len": SEQ_LEN,
        "split_ratio": SPLIT_RATIO,
        "n_features": len(FEATURES),
        "features": "|".join(FEATURES),
        "train_rows": split,
        "holdout_rows": len(labeled) - split,
        "xgb_grid_size": len(XGB_PARAM_GRID),
        "lstm_grid_size": len(LSTM_PARAM_GRID),
        "arima_grid_size": len(ARIMA_ORDER_GRID),
        "ridge_grid_size": len(RIDGE_ALPHA_GRID),
        "xgb_pred_return": xgb_ret,
        "xgb_holdout_mae": xgb_mae,
        "xgb_direction_acc": xgb_dacc,
        "xgb_best_params": str(xgb_params),
        "lstm_pred_return": lstm_ret,
        "lstm_holdout_mae": lstm_mae,
        "lstm_direction_acc": lstm_dacc,
        "lstm_best_params": str(lstm_params),
        "arima_pred_return": arima_ret,
        "arima_holdout_mae": arima_mae,
        "arima_direction_acc": arima_dacc,
        "arima_order": str(arima_order),
        "ridge_pred_return": ridge_ret,
        "ridge_holdout_mae": ridge_mae,
        "ridge_direction_acc": ridge_dacc,
        "ridge_best_params": str(ridge_params),
        "kalman_pred_return": kalman_ret,
        "kalman_holdout_mae": kalman_mae,
        "kalman_direction_acc": kalman_dacc,
        "kalman_level": kalman_level,
        "winner_model": winner,
        "winner_direction_acc": winner_dacc,
        # raw = straight from the winning model; pred_* below may be revised by
        # episodic memory in apply_memory_correction()
        "raw_pred_return_7d": winner_ret,
        "pred_return_7d": winner_ret,
        "pred_price_7d": last_price * (1 + winner_ret),
        "memory_n_similar": 0,
        "memory_similar_hit_rate": np.nan,
        "memory_mean_residual": np.nan,
        "memory_correction": 0.0,
        "memory_correction_applied": False,
        "memory_note": "",
        "last_price": last_price,
        "fx_rate_to_usd": fx,
        "last_price_usd": last_price / fx,
        "volatility_30d": float(feat["vol_30"].iloc[-1]),
        "all_5_agree_on_direction": len(signs) == 1,
        "cache_mode": cache_mode,
        "runtime_seconds": round(time.time() - started, 1),
        "actual_return_1d": np.nan,
        "actual_price_1d": np.nan,
        "signal_correct_1d": np.nan,
        "actual_return_7d": np.nan,
        "actual_price_7d": np.nan,
        "direction_was_correct": np.nan,
        "raw_direction_was_correct": np.nan,
        "signal_correct_7d": np.nan,
        "signal_pnl_7d": np.nan,
        "outcome_status": "PENDING",
    }
    return record, raw, feat


def market_context(ticker: str, raw: pd.DataFrame, feat: pd.DataFrame) -> dict:
    """The minimal facts the agent panel needs, available before any model runs."""
    meta = TICKERS[ticker]
    return {
        "company": meta["name"],
        "currency": meta["currency"],
        "last_price": float(raw["Close"].iloc[-1]),
        "data_end_date": str(raw.index[-1].date()),
        "volatility_30d": float(feat["vol_30"].iloc[-1]),
    }


def summarize_record(record: dict) -> pd.DataFrame:
    """Compact per-model view of one run, for display in the notebook."""
    labels = {"xgb": "XGBoost", "lstm": "LSTM", "arima": "ARIMA",
              "ridge": "Ridge", "kalman": "Kalman"}
    tuned_field = {"arima": "arima_order", "kalman": "kalman_level"}
    winner_key = {"xgb": "xgboost", "lstm": "lstm", "arima": "arima",
                  "ridge": "ridge", "kalman": "kalman"}
    rows = []
    for m, label in labels.items():
        rows.append({
            "model": label,
            "pred_return_7d": record[f"{m}_pred_return"],
            "holdout_mae": record[f"{m}_holdout_mae"],
            "direction_acc": record[f"{m}_direction_acc"],
            "tuned": record[tuned_field.get(m, f"{m}_best_params")],
            "winner": "<<<" if record["winner_model"] == winner_key[m] else "",
        })
    return pd.DataFrame(rows).set_index("model")


def _as_bool_series(s: pd.Series) -> pd.Series:
    return s.astype(str).str.lower().isin(["true", "1", "1.0"])


def log_performance(ticker: str) -> pd.DataFrame | None:
    """Track record across graded runs, comparing the raw model prediction
    against the memory-corrected one -- i.e. is the self-correction helping?"""
    log = load_run_log(ticker)
    graded = log[log.get("outcome_status") == "GRADED"] if not log.empty else log
    if graded.empty:
        return None

    rows = []
    for label, pred_col, ok_col in [
        ("raw model", "raw_pred_return_7d", "raw_direction_was_correct"),
        ("memory-corrected", "pred_return_7d", "direction_was_correct"),
    ]:
        if pred_col not in graded or ok_col not in graded:
            continue
        sub = graded.dropna(subset=[pred_col, "actual_return_7d"])
        if sub.empty:
            continue
        correct = _as_bool_series(sub[ok_col])
        rows.append({
            "prediction": label,
            "graded_runs": len(sub),
            "direction_correct": int(correct.sum()),
            "hit_rate": round(float(correct.mean()), 3),
            "mean_abs_error": round(float((sub[pred_col] - sub["actual_return_7d"]).abs().mean()), 5),
        })

    # the LLM panel makes a directional call, not a numeric one -- no MAE for it
    if "agent_direction_was_correct" in graded:
        sub = graded.dropna(subset=["agent_direction_was_correct"])
        if not sub.empty:
            correct = _as_bool_series(sub["agent_direction_was_correct"])
            rows.append({
                "prediction": "LLM agent panel",
                "graded_runs": len(sub),
                "direction_correct": int(correct.sum()),
                "hit_rate": round(float(correct.mean()), 3),
                "mean_abs_error": np.nan,
            })

    # the actual BUY/SELL calls acted on -- HOLDs excluded, they aren't bets
    for label, ok_col in [("FINAL signal (next day)", "signal_correct_1d"),
                          ("FINAL signal (7 day)", "signal_correct_7d")]:
        if ok_col not in graded:
            continue
        sub = graded.dropna(subset=[ok_col])
        if sub.empty:
            continue
        correct = _as_bool_series(sub[ok_col])
        rows.append({
            "prediction": label,
            "graded_runs": len(sub),
            "direction_correct": int(correct.sum()),
            "hit_rate": round(float(correct.mean()), 3),
            "mean_abs_error": np.nan,
        })
    return pd.DataFrame(rows).set_index("prediction") if rows else None
