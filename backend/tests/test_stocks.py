"""Stocks page: the listing joined with names and sectors, and the per-company detail (no network)."""
import pandas as pd

from app.services import market, stocks

SUMMARY = {"ok": True, "date": "2026-11-12", "stale": False, "summary": {"stocks": [
    {"code": "BATBC", "ltp": 300.0, "close": 299.0, "ycp": 304.0, "change_pct": -1.6, "trade": 800, "value_mn": 120.0, "volume": 400000},
    {"code": "ABBANK", "ltp": 10.5, "close": 10.5, "ycp": 10.0, "change_pct": 5.0, "trade": 1200, "value_mn": 55.5, "volume": 5300000}]}}


ANNUAL_ACROSS = '<table><tr><th>Particulars</th><th>2020</th><th>2021</th><th>2022</th></tr><tr><td>Turnover</td><td>5</td><td>6</td><td>7</td></tr><tr><td>Basic EPS</td><td>0.28</td><td>2.54</td><td>0.24</td></tr><tr><td>Diluted EPS</td><td>9</td><td>9</td><td>9</td></tr></table>'
ANNUAL_DOWN = '<table><tr><th>Year</th><th>Basic EPS (Tk)</th></tr><tr><td>2021</td><td>2.54</td></tr><tr><td>2022</td><td>(0.24)</td></tr></table>'
NEWS = ["JANATAMF | EPS was Tk. (0.38) for January-March 2025 and more", "JANATAMF | EPS was Tk. 0.33 for July-September 2024",
        "JANATAMF | EPS was Tk. (0.49) for October-December 2024", "JANATAMF | EPS of the company was Tk. 1.10 for the year ended June 30, 2024",
        "JANATAMF | EPS was Tk. 9.99 for January-March 2025 (a repeat of the same period)"]


def _setup(monkeypatch, tmp_path):
    monkeypatch.setattr(market, "LEARNED_SECTORS_FILE", tmp_path / "learned.json")
    monkeypatch.setattr(market, "SECTORS_FILE", tmp_path / "none.json")
    monkeypatch.setattr(market, "overview", lambda refresh=False: SUMMARY)
    monkeypatch.setattr(stocks, "_names", lambda: {"BATBC": "British American Tobacco Bangladesh Company Limited"})
    stocks._detail.clear()


def test_listing_joins_names_and_sectors(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    (tmp_path / "learned.json").write_text('{"ABBANK": "Bank"}')
    r = stocks.listing()
    assert r["ok"] and r["count"] == 2 and [s["code"] for s in r["stocks"]] == ["ABBANK", "BATBC"]    # sorted by code
    assert r["stocks"][1]["name"].startswith("British American") and r["stocks"][0]["name"] == ""
    assert r["sectors"] == [{"sector": "Bank", "count": 1}] and r["sectors_known"] == 1


def test_range_52w():
    assert stocks._range_52w("2.20 - 4.30") == (2.2, 4.3)
    assert stocks._range_52w("1,200.5 - 1,500") == (1200.5, 1500.0)
    assert stocks._range_52w(None) == (None, None) and stocks._range_52w("n/a") == (None, None)


def test_detail_builds_stats_and_learns_the_sector(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    idx = pd.to_datetime(["2026-11-11", "2026-11-12"])
    df = pd.DataFrame({"Open": [3.2, 3.3], "High": [3.4, 3.4], "Low": [3.1, 3.2], "Close": [3.2, 3.3], "Volume": [700000.0, 800886.0]}, index=idx)
    monkeypatch.setattr(stocks, "_snapshot", lambda code: {"Sector": "Mutual Funds", "Market Category": "A", "Market Capitalization (mn)": "956.70",
                                                           "Paid-up Capital (mn)": "2,899.20", "52 Weeks' Moving Range": "2.20 - 4.30"})
    monkeypatch.setattr(stocks, "_ohlcv", lambda code, a, b: df)
    monkeypatch.setattr(stocks, "_page_html", lambda code: ANNUAL_ACROSS)
    monkeypatch.setattr(stocks, "_eps_news", lambda code: NEWS)
    d = stocks.detail("1janatamf")
    assert d["ok"] and d["code"] == "1JANATAMF" and d["sector"] == "Mutual Funds" and d["category"] == "A"
    assert d["last_close"] == 3.3 and d["prev_close"] == 3.2 and round(d["change"], 2) == 0.1 and round(d["change_pct"], 2) == 3.12
    assert (d["day_high"], d["day_low"], d["volume"]) == (3.4, 3.2, 800886.0)
    assert (d["week52_low"], d["week52_high"]) == (2.2, 4.3) and d["market_cap_mn"] == 956.7 and d["paid_up_mn"] == 2899.2
    assert len(d["candles"]) == 2 and d["candles"][-1] == ["2026-11-12", 3.3, 3.4, 3.2, 3.3, 800886.0]
    assert '"1JANATAMF": "Mutual Funds"' in (tmp_path / "learned.json").read_text()                    # learned for the list and sector breadth
    assert market._sectors()["1JANATAMF"] == "Mutual Funds"
    assert d["annual_eps"][-1] == {"year": 2022, "eps": 0.24} and [r["period"] for r in d["quarterly_eps"]][0].endswith("2025")


def test_detail_survives_a_dead_profile_page(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    df = pd.DataFrame({"Open": [10.0], "High": [11.0], "Low": [9.0], "Close": [10.5], "Volume": [1000.0]}, index=pd.to_datetime(["2026-11-12"]))
    monkeypatch.setattr(stocks, "_snapshot", lambda code: {"error": "403"})
    monkeypatch.setattr(stocks, "_ohlcv", lambda code, a, b: df)
    d = stocks.build_detail("ABBANK")
    assert d["ok"] and d["errors"] and d["week52_high"] == 11.0 and d.get("market_cap_mn") is None      # 52-week range falls back to the candles


def test_routes(client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(stocks, "build_detail", lambda code: {"ok": True, "code": code.upper(), "candles": [], "errors": []})
    assert client.get("/api/stocks").json()["count"] == 2
    assert client.get("/api/stocks/abbank").json()["code"] == "ABBANK"
    assert client.get("/api/stocks/AMCL(PRAN)").json()["code"] == "AMCL(PRAN)"   # DSE codes can have brackets
    assert client.get("/api/stocks/bad%20code!").status_code in (404, 422)


def test_eps_and_holding_parsers():
    assert stocks.parse_annual_eps(ANNUAL_ACROSS) == [{"year": 2020, "eps": 0.28}, {"year": 2021, "eps": 2.54}, {"year": 2022, "eps": 0.24}]   # Basic, not Diluted
    assert stocks.parse_annual_eps(ANNUAL_DOWN) == [{"year": 2021, "eps": 2.54}, {"year": 2022, "eps": -0.24}]       # one row per year, parentheses negative
    assert stocks.parse_annual_eps("<html></html>") == []
    q = stocks.quarterly_eps(NEWS)
    assert [(r["period"], r["eps"]) for r in q] == [("January-March 2025", -0.38), ("October-December 2024", -0.49), ("July-September 2024", 0.33)]   # newest first, parentheses negative, annual and repeats left out
    snap = {"Share Holding: Sponsor/Director": "25.00", "Institute": "26.95%", "Public": "48.05", "Govt": "0.00"}
    assert stocks.shareholding(snap) == [{"label": "Sponsor/Director", "pct": 25.0}, {"label": "Institute", "pct": 26.95}, {"label": "Public", "pct": 48.05}]
    assert stocks.last_dividend("7% 2022, 10% 2021") == {"pct": 7.0, "year": 2022} and stocks.last_dividend("-") is None
