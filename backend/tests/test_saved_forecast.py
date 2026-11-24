"""The last forecast run is kept as CSV files and loaded again from them (app/pipeline/saved_forecast.py)."""
import pytest

from app.pipeline import saved_forecast as sf


@pytest.fixture()
def result():
    dates = ["2026-11-24", "2026-11-25", "2026-11-26"]
    return {"ticker": "ZZZ", "daily": {"final_signal": "HOLD"}, "run_info": {"models": ["ridge"], "generated_at": "2026-11-23 10:00:00"},
            "forecast": {"last_price": 4.2, "last_date": "2026-11-23", "currency": "BDT", "company": "Zed", "n_rows": 521, "dates": dates,
                         "paths": {"Ridge": [4.21, 4.22, 4.23], "Ensemble": [4.2, 4.2, 4.2]}, "failed_models": {"LASSO": "x"},
                         "best_model": "Ridge", "direction": "UP", "total_return_pct": 0.7, "final_price": 4.23},
            "agent": {"plan": {"path_ret": [0.0, 0.01, 0.02], "band_ret": [[-0.01, 0.01], [-0.02, 0.02], [-0.03, 0.03]]}}}


def test_write_then_read_gives_the_same_run(tmp_path, monkeypatch, result):
    monkeypatch.setattr(sf, "DIR", str(tmp_path))
    assert sf.read("ZZZ") is None
    sf.write("ZZZ", result, [("2026-11-22", 4.1), ("2026-11-23", 4.2)])
    for kind in ("summary", "prices", "forecast"):
        assert (tmp_path / f"ZZZ_{kind}.csv").exists()
    r = sf.read("ZZZ")
    s = r["summary"]
    assert (s["last_close"], s["last_date"], s["daily_rows"], s["signal"], s["currency"]) == (4.2, "2026-11-23", 521.0, "HOLD", "BDT")
    assert s["failed_models"] == "LASSO" and s["main_forecast"] == "Reviewed" and s["models_fitted"] == 1
    assert r["prices"] == [["2026-11-22", 4.1], ["2026-11-23", 4.2]]
    assert r["dates"] == result["forecast"]["dates"]
    assert r["paths"]["Ridge"] == [4.21, 4.22, 4.23]
    assert r["paths"]["Reviewed"][2] == pytest.approx(4.2 * 1.02)
    assert r["band"][1] == [pytest.approx(4.2 * (1 + 0.01 - 0.02)), pytest.approx(4.2 * (1 + 0.01 + 0.02))]


def test_a_newer_run_replaces_the_files(tmp_path, monkeypatch, result):
    monkeypatch.setattr(sf, "DIR", str(tmp_path))
    sf.write("ZZZ", result, [("2026-11-23", 4.2)])
    result["forecast"]["last_price"] = 5.0
    result["forecast"]["paths"]["Ridge"] = [5.1, 5.2, 5.3]
    sf.write("ZZZ", result, [("2026-11-23", 5.0)])
    r = sf.read("ZZZ")
    assert r["summary"]["last_close"] == 5.0 and r["paths"]["Ridge"][0] == 5.1 and len(r["prices"]) == 1


def test_without_a_reviewed_forecast_there_is_no_band(tmp_path, monkeypatch, result):
    monkeypatch.setattr(sf, "DIR", str(tmp_path))
    result["agent"] = {"error": "x"}
    sf.write("ZZZ", result, [("2026-11-23", 4.2)])
    r = sf.read("ZZZ")
    assert r["band"] is None and "Reviewed" not in r["paths"] and r["summary"]["main_forecast"] == "Ensemble"


def test_route_loads_saved_csv_and_backfills_an_old_run(client, tmp_path, monkeypatch):
    monkeypatch.setattr(sf, "DIR", str(tmp_path))
    t = client.get("/api/tickers").json()[0]["ticker"]
    assert client.get("/api/forecast/saved/NOPE").status_code == 404
    r = client.get(f"/api/forecast/saved/{t}")           # no CSV yet: written out from the saved page data
    assert r.status_code == 200 and (tmp_path / f"{t.replace('.', '_')}_summary.csv").exists()
    body = r.json()
    assert body["summary"]["ticker"] == t and len(body["dates"]) == 7 and body["prices"]
    assert client.get(f"/api/forecast/saved/{t}").json() == body    # the second answer comes from the CSV files


def test_members_and_ranking_are_saved_and_a_run_without_ranking_drops_the_old_one(tmp_path, monkeypatch, result):
    monkeypatch.setattr(sf, "DIR", str(tmp_path))
    result["agent"]["plan"]["members"] = ["Ridge", "Theta"]
    result["agent"]["ranking"] = [{"model": "Ridge", "member": True, "mae_vs_naive": 0.9, "live_mae_vs_naive": None, "live_origins": None,
                                   "live_calls": None, "live_weight": 0.0, "score": 0.9},
                                  {"model": "LASSO", "member": False, "mae_vs_naive": 1.1, "live_mae_vs_naive": 1.3, "live_origins": 12,
                                   "live_calls": 84, "live_weight": 0.545, "score": 1.2}]
    result["forecast"]["blend_weights"] = {"Fusion": {"Ridge": 0.6, "Naive": 0.4}, "Ensemble": {"Ridge": 0.5, "Theta": 0.5}}
    sf.write("ZZZ", result, [("2026-11-23", 4.2)])
    r = sf.read("ZZZ")
    assert r["blends"]["Fusion"] == {"Ridge": 0.6, "Naive": 0.4}
    assert r["members"] == ["Ridge", "Theta"] and [x["model"] for x in r["ranking"]] == ["Ridge", "LASSO"]
    assert r["ranking"][0]["member"] is True and r["ranking"][0]["live_vs_naive"] is None and r["ranking"][1]["live_origins"] == 12
    del result["agent"]["ranking"]
    sf.write("ZZZ", result, [("2026-11-23", 4.2)])
    assert sf.read("ZZZ")["ranking"] == [] and not (tmp_path / "ZZZ_ranking.csv").exists()
