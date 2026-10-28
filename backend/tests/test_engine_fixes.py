"""Regression tests for the forecasting-engine fixes (split alignment, sequence windows, RSI, FX fallback, cache version,
reviewer calibration). They need the numeric stack, so they are skipped where it is not installed (e.g. the slim CI job).
torch is stubbed when absent: none of these tests train a network."""
import json
import sys
import types
from pathlib import Path

import pytest

for mod in ("numpy", "pandas", "sklearn", "statsmodels", "xgboost", "yfinance", "dateutil"):
    pytest.importorskip(mod)

try:
    import torch  # noqa: F401
except ImportError:
    _t, _nn = types.ModuleType("torch"), types.ModuleType("torch.nn")

    class _Stub:
        def __init__(self, *a, **k):
            pass

    _nn.Module = _nn.LSTM = _nn.Linear = _nn.MSELoss = _nn.Sequential = _Stub
    _t.nn, _t.Tensor = _nn, object
    sys.modules["torch"], sys.modules["torch.nn"] = _t, _nn

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
import forecast_agent as fa  # noqa: E402
import forecast_lib as fl  # noqa: E402
import predictor_lib as pl  # noqa: E402


def _prices(n=320, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=n)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close,
                         "Volume": rng.integers(1e5, 1e6, n)}, index=idx)


def test_walk_forward_models_start_on_the_same_date_as_ml_models():
    raw = _prices()
    labeled = fl.make_features(raw).dropna(subset=fl.FEATURES + fl.TARGETS)
    split = int(len(labeled) * fl.SPLIT_RATIO)
    split_pos = int(raw.index.get_loc(labeled.index[split]))
    assert split_pos - split >= 40          # the offset that used to shift ARIMA's holdout ~49 rows earlier
    res = fl.run_arima(raw["Close"], split_pos)
    assert res["holdout"].dropna().index[0] >= labeled.index[split]


def test_forecast_lib_sequences_end_on_the_origin_row():
    scaled = np.arange(30, dtype=float).reshape(-1, 1)
    targets = np.arange(30, dtype=float).reshape(-1, 1) * 10
    X, y, rows = fl._build_sequences(scaled, targets, 5, with_rows=True)
    assert X.shape[1] == 5
    assert np.all(X[:, -1, 0] == rows)       # the last row of each window is the origin itself
    assert X[0, 0, 0] == 0 and rows[0] == 4  # first window starts at row 0


def test_predictor_lib_sequences_match_the_live_window():
    scaled = np.arange(30, dtype=float).reshape(-1, 1)
    targets = np.arange(30, dtype=float) * 10
    X, y = pl.build_sequences(scaled, targets, 5)
    assert np.all(X[:, -1, 0] * 10 == y)     # window ends on the row whose target it predicts
    assert np.allclose(X[-1, :, 0], np.arange(25, 30))   # same shape of window as scaled[-5:] used for the live forecast


def test_rsi_with_no_down_days_is_100_not_missing():
    close = pd.Series(np.arange(1.0, 61.0))
    df = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1000.0})
    assert pl.price_features(df)["rsi_14"].iloc[-1] == 100.0


def test_fx_failure_falls_back_to_last_good_rate_then_nan(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "CACHE_DIR", str(tmp_path))
    cur = next(c for c in pl.FX_PAIRS if pl.FX_PAIRS[c])

    class Down:
        def __init__(self, *_):
            pass

        def history(self, **_):
            raise RuntimeError("offline")

    class Up(Down):
        def history(self, **_):
            return pd.DataFrame({"Close": [1.0, 122.5]})

    monkeypatch.setattr(pl.yf, "Ticker", Down)
    assert np.isnan(pl.get_fx_rate_to_usd(cur))          # no cache yet: NaN, not an exception
    monkeypatch.setattr(pl.yf, "Ticker", Up)
    assert pl.get_fx_rate_to_usd(cur) == 122.5           # good answer is cached
    monkeypatch.setattr(pl.yf, "Ticker", Down)
    assert pl.get_fx_rate_to_usd(cur) == 122.5           # later failure uses it
    assert pl.get_fx_rate_to_usd("USD") == 1.0


def test_old_model_cache_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "CACHE_DIR", str(tmp_path))
    pl.save_model_results("T", "2026-01-01", 100, {"xgb": {"pred_return": 0.01}})
    assert pl.load_model_results("T", "2026-01-01", 100) is not None
    f = next(tmp_path.glob("*_results.json"))
    d = json.loads(f.read_text())
    d["model_version"] = pl.MODEL_VERSION - 1
    f.write_text(json.dumps(d))
    assert pl.load_model_results("T", "2026-01-01", 100) is None


def test_reviewer_shrink_is_picked_on_first_half_and_scored_on_second():
    rng = np.random.default_rng(0)
    n = 80
    actual = rng.normal(0, 0.02, (n, fl.FORECAST_DAYS))
    pred = actual * 0 + rng.normal(0, 0.05, (n, fl.FORECAST_DAYS))      # pure noise: over-confident, shrinking should help
    cut = n // 2
    grid = fa.SHRINK_GRID
    fit_maes = {s: float(np.mean(np.abs(actual[:cut] - s * pred[:cut]))) for s in grid}
    assert min(fit_maes, key=fit_maes.get) < 1.0
    # the production code must use that rule: the source selects on [:cut] and reports on [cut:]
    src = Path(fa.__file__).read_text(encoding="utf-8")
    assert "pred[:cut]" in src and "pred[cut:]" in src


def _fake_fcast(noise=0.05, seed=1):
    """Three members whose out-of-sample predictions are noise around 0: a fit that shrinking toward 'no change' should fix."""
    raw = _prices(400, seed)
    actual = fl.make_features(raw)[fl.TARGETS].dropna()
    idx = actual.index[-100:]
    rng = np.random.default_rng(seed)
    labels = [lab for key, lab in fl.MODEL_LABELS.items() if key in fl.BASE_MODELS][:3]
    key_of = {lab: key for key, lab in fl.MODEL_LABELS.items()}
    results = {}
    for lab in labels:
        hold = pd.DataFrame(rng.normal(0, noise, (len(idx), fl.FORECAST_DAYS)), index=idx, columns=fl.TARGETS)
        results[key_of[lab]] = {"path": rng.normal(0.02, 0.01, fl.FORECAST_DAYS).cumsum(), "holdout": hold}
    ranked = pd.DataFrame({"holdout_mae": [0.02, 0.021, 0.022], "mae_vs_naive": [1.3, 1.4, 1.5]}, index=labels)
    fcast = {"ticker": "ZZZ", "last_price": float(raw["Close"].iloc[-1]), "last_date": raw.index[-1], "raw": raw,
             "results": results}
    plan = {"members": labels, "ranked": ranked}
    return fcast, plan


def test_reviewer_runs_and_reports_the_split_honestly():
    fcast, plan = _fake_fcast()
    out = fa.reviewer_agent(fcast, plan)
    cal = next(r for r in out["log"] if r["check"] == "Calibration")
    assert "holdout dates" in cal["detail"]
    assert "first 50 of 100" in cal["detail"] or "too few to split" in cal["detail"]
    assert 0.0 <= out["shrink"] <= 1.0
    # pure-noise members never beat 'no change': confidence must not be HIGH
    assert out["confidence"] in ("LOW", "MEDIUM")


def test_dse_top_up_appends_archive_days_in_the_file_layout(tmp_path, monkeypatch):
    import dse_archive
    head = "date,symbol,ltp,high,low,open,close,ycp,trade,value,volume,rsi,macd,boll_ub,boll_lb,sma_50\n"
    old = head + "2026-09-16,ABBANK,4.8,4.8,4.4,4.5,4.8,4.4,302,5.826,1259205,59.0,-0.08,4.87,4.32,4.8\n"
    (tmp_path / "AB_Bank_ABBANK_2y.csv").write_text(old, encoding="utf-8")
    monkeypatch.setattr(pl, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(pl, "last_final_session", lambda t: pd.Timestamp("2026-09-20").date())
    asked = []

    def history(symbol, start, end):
        asked.append((symbol, str(start)[:10], str(end)))
        cols = ["symbol", "ltp", "high", "low", "open", "close", "ycp", "trade", "value", "volume"]
        return pd.DataFrame([["ABBANK", 4.7, 4.8, 4.6, 4.8, 4.7, 4.8, 147.0, 1.057, 224183.0],
                             ["ABBANK", 4.6, 4.7, 4.5, 4.7, 4.6, 4.7, 90.0, 0.5, 100.0]], columns=cols,
                            index=pd.DatetimeIndex(["2026-09-17", "2026-09-21"], name="date"))   # the 21st is not final yet

    monkeypatch.setattr(dse_archive, "history", history)
    assert pl.top_up_data_csv("ABBANK", verbose=False) == 1
    assert asked == [("ABBANK", "2026-09-17", "2026-09-20")]
    text = (tmp_path / "AB_Bank_ABBANK_2y.csv").read_text(encoding="utf-8")
    assert text == old + "2026-09-17,ABBANK,4.7,4.8,4.6,4.8,4.7,4.8,147,1.057,224183,,,,,\n"
    assert pl.load_data_csv("ABBANK")["Close"].tolist() == [4.8, 4.7]


def test_dse_archive_reads_the_day_end_table(monkeypatch):
    pytest.importorskip("bs4")
    pytest.importorskip("lxml")
    import dse_archive
    page = """<table class="table table-bordered background-white shares-table fixedHeader">
      <thead><tr><th>#</th><th>DATE</th><th>TRADING CODE</th><th>LTP*</th><th>HIGH</th><th>LOW</th><th>OPENP*</th>
      <th>CLOSEP*</th><th>YCP</th><th>TRADE</th><th>VALUE (mn)</th><th>VOLUME</th></tr></thead>
      <tbody><tr><td>1</td><td>2026-10-27</td><td>SQURPHARMA</td><td>218.8</td><td>220.4</td><td>218.2</td><td>219.6</td>
      <td>218.8</td><td>218.8</td><td>1,998</td><td>77.972</td><td>355,398</td></tr>
      <tr><td>2</td><td>2026-10-26</td><td>SQURPHARMA</td><td>218.8</td><td>219.7</td><td>217.9</td><td>219</td>
      <td>218.8</td><td>218.9</td><td>1,550</td><td>53.212</td><td>243,592</td></tr></tbody></table>"""
    calls = []
    monkeypatch.setattr(dse_archive, "_get", lambda path, params: calls.append(params) or page)
    df = dse_archive.history("squrpharma", pd.Timestamp("2026-10-26").date(), pd.Timestamp("2026-10-27").date())
    assert calls[0]["inst"] == "SQURPHARMA" and len(calls) == 1
    assert [str(d)[:10] for d in df.index] == ["2026-10-26", "2026-10-27"]
    assert df["close"].tolist() == [218.8, 218.8] and df["volume"].tolist() == [243592, 355398]
    assert df["value"].iloc[1] == 77.972 and df["trade"].iloc[1] == 1998
