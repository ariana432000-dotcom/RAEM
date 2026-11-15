"""Market overview: the archive and front-page parsers, the day summary and the cached /api/market route (no network)."""
from app.services import market

ARCHIVE = """<html><body><table class="shares-table"><thead><tr>
<th>#</th><th>DATE</th><th>TRADING CODE</th><th>LTP*</th><th>HIGH</th><th>LOW</th><th>OPENP*</th><th>CLOSEP*</th><th>YCP</th><th>TRADE</th><th>VALUE (mn)</th><th>VOLUME</th></tr></thead><tbody>
<tr><td>1</td><td>2026-11-12</td><td>ABBANK</td><td>10.5</td><td>10.6</td><td>10.2</td><td>10.3</td><td>10.5</td><td>10.0</td><td>1,200</td><td>55.5</td><td>5,300,000</td></tr>
<tr><td>2</td><td>2026-11-12</td><td>BATBC</td><td>300</td><td>305</td><td>298</td><td>301</td><td>299</td><td>304</td><td>800</td><td>120.25</td><td>400,000</td></tr>
<tr><td>3</td><td>2026-11-12</td><td>BPML</td><td>20</td><td>20</td><td>19</td><td>20</td><td>20</td><td>20</td><td>300</td><td>9</td><td>450,000</td></tr>
<tr><td>4</td><td>2026-11-12</td><td>NOTRADE</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>5</td><td>0</td><td>0</td><td>0</td></tr>
<tr><td>bad row with too few cells</td></tr>
</tbody></table></body></html>"""

FRONT = """<html><body><div>DSEX Index 5,012.34 -12.50 -0.25%</div><div>DSES Index 1,150.10 +3.20 0.28%</div></body></html>"""


def test_parse_archive_reads_columns_by_header():
    rows = market.parse_archive(ARCHIVE)
    assert [r["code"] for r in rows] == ["ABBANK", "BATBC", "BPML", "NOTRADE"]
    assert rows[0]["trade"] == 1200 and rows[0]["value"] == 55.5 and rows[0]["volume"] == 5300000
    assert market.parse_archive("<html></html>") == []


def test_summarise_counts_and_ranks():
    s = market.summarise(market.parse_archive(ARCHIVE), {"ABBANK": "Bank", "BATBC": "Tobacco"})
    assert (s["advanced"], s["declined"], s["unchanged"]) == (1, 1, 1)      # NOTRADE is not counted
    assert s["total_trade"] == 2300 and s["traded_issues"] == 3 and s["value_mn"] == 184.75
    assert [t["code"] for t in s["top"]] == ["BATBC", "ABBANK", "BPML"]
    assert round(s["top"][1]["change_pct"], 1) == 5.0 and len(s["stocks"]) == 3
    assert {x["sector"]: x["total"] for x in s["sectors"]} == {"Bank": 1, "Tobacco": 1}


def test_parse_indices_finds_what_is_there():
    idx = {i["name"]: i for i in market.parse_indices(FRONT)}
    assert set(idx) == {"DSEX", "DSES"}                                     # DS30 is not on this page: left out, no error
    assert idx["DSEX"]["value"] == 5012.34 and idx["DSEX"]["change"] == -12.5 and idx["DSEX"]["change_pct"] == -0.25


def test_route_caches_and_survives_a_failed_fetch(client, monkeypatch, tmp_path):
    monkeypatch.setattr(market, "CACHE_FILE", tmp_path / "market_cache.json")
    monkeypatch.setattr(market, "_mem", {"at": 0.0, "data": None})
    monkeypatch.setattr(market, "_news", lambda *a, **k: [{"date": "2026-11-12", "code": "ABBANK", "headline": "Dividend", "text": ""}])
    calls = []

    def fake_get(path, params=None):
        calls.append(path)
        if path == "":
            return FRONT
        if fake_get.down:
            raise OSError("exchange down")
        return ARCHIVE
    fake_get.down = False
    monkeypatch.setattr(market, "_get", fake_get)
    monkeypatch.setattr(market.time, "sleep", lambda s: None)

    a = client.get("/api/market").json()
    assert a["ok"] and a["summary"]["advanced"] == 1 and len(a["indices"]) == 2 and a["news"][0]["code"] == "ABBANK"
    n = len(calls)
    assert client.get("/api/market").json() == a and len(calls) == n          # served from the cache, no new fetch

    fake_get.down = True
    b = client.get("/api/market?refresh=true").json()                          # exchange down: the last good copy, flagged
    assert b["ok"] and b["stale"] and b["summary"] == a["summary"] and any("archive" in e for e in b["errors"])


def test_route_when_nothing_was_ever_fetched(client, monkeypatch, tmp_path):
    monkeypatch.setattr(market, "CACHE_FILE", tmp_path / "none.json")
    monkeypatch.setattr(market, "_mem", {"at": 0.0, "data": None})
    monkeypatch.setattr(market, "_news", lambda *a, **k: [])
    monkeypatch.setattr(market, "_get", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    d = client.get("/api/market").json()
    assert d["ok"] is False and d["summary"] is None and d["indices"] == [] and d["errors"]
