"""Trade Desk REST API: profile, watchlist, time machine (+ later phases)."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from apps.api.desk import bars as bars_mod, bars_source
from apps.api.desk.store import reset_desk_store_for_tests
from apps.api.routes import desk as desk_routes
from tests.desk_utils import daily_rows, desk_client, random_walk_sessions, session_rows
from tests.scanner_utils import make_store

NOW = datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc)   # 15:30 IST, after the close


@pytest.fixture()
def env(tmp_path, monkeypatch):
    store = reset_desk_store_for_tests(tmp_path / "desk.sqlite")
    make_store(tmp_path, None)
    monkeypatch.setattr(desk_routes, "_now", lambda: NOW)
    client, user = desk_client(desk_routes.router)
    yield client, store, user, monkeypatch
    bars_source.clear_live_providers_for_tests()


def test_profile_defaults_validation_and_isolation(env):
    client, _, user, _ = env
    prof = client.get("/api/desk/profile").json()
    assert prof["flat_by"] == "15:15" and prof["max_daily_loss_inr"] == 1500
    prof["max_loss_per_trade_inr"] = 800
    assert client.put("/api/desk/profile", json=prof).status_code == 200
    bad = dict(prof, flat_by="15:25")   # after square-off
    assert client.put("/api/desk/profile", json=bad).status_code == 422
    assert client.put("/api/desk/profile", json=dict(prof, nope=1)).status_code == 422
    user["id"] = "user_b"
    assert client.get("/api/desk/profile").json()["max_loss_per_trade_inr"] == 500


def test_watchlist(env):
    client, _, user, _ = env
    assert client.post("/api/desk/watchlist", json={"symbol": "sbin.ns"}).json() == ["SBIN"]
    assert client.post("/api/desk/watchlist", json={"symbol": "bad sym"}).status_code == 422
    user["id"] = "user_b"
    assert client.get("/api/desk/watchlist").json() == []
    user["id"] = "user_a"
    assert client.delete("/api/desk/watchlist/SBIN").status_code == 204
    assert client.get("/api/desk/watchlist").json() == []


def _intraday_rows():
    rows = random_walk_sessions(date(2026, 10, 27), 25, start_price=800.0)
    rows += session_rows(date(2026, 10, 28), [800.0, 801.0, 799.5, 803.0] + [803.0] * 71)
    return rows


def test_timemachine_intraday_endpoint_and_cache(env):
    client, store, _, mp = env
    calls = []

    def fake_fetch(symbol, interval, now=None, store=None, yahoo=False):
        calls.append((symbol, interval, yahoo))
        return _intraday_rows()

    mp.setattr(bars_mod, "fetch_history", fake_fetch)
    body = {"symbol": "SBIN", "date": "2026-10-28", "entry": 803.0, "stop": 800.25,
            "target": 808.5, "entry_time": "09:35", "replays": 500}
    r = client.post("/api/desk/timemachine", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["mode"] == "intraday" and out["mc"]["until"] == "15:15"   # profile flat_by
    assert out["source"] == "delayed:yahoo" and out["data_as_of"]
    again = client.post("/api/desk/timemachine", json=body).json()
    assert again["key"] == out["key"]
    assert client.post("/api/desk/timemachine",
                       json=dict(body, entry_time="09:33")).status_code == 422


def test_timemachine_swing_endpoint_accepts_any_yahoo_ticker(env):
    client, _, _, mp = env
    closes = [100 + i * 0.05 + (i % 7) * 0.3 for i in range(300)]

    def fake_fetch(symbol, interval, now=None, store=None, yahoo=False):
        assert yahoo and interval == "1d" and symbol == "AAPL"
        return daily_rows(date(2026, 10, 28), closes)

    mp.setattr(bars_mod, "fetch_history", fake_fetch)
    last = closes[-1]
    r = client.post("/api/desk/timemachine", json={
        "mode": "swing", "symbol": "aapl", "date": "2026-10-28", "entry": last,
        "stop": round(last * 0.97, 2), "target": round(last * 1.06, 2), "horizon": 10,
        "replays": 500})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "swing" and r.json()["history"]["rule"]["setup"] == "levels"


def test_timemachine_data_errors_are_422(env):
    client, _, _, mp = env

    def boom(*a, **k):
        raise bars_mod.BarsError("Yahoo returned no 5m bars for ZZZ.NS")

    mp.setattr(bars_mod, "fetch_history", boom)
    r = client.post("/api/desk/timemachine", json={
        "symbol": "ZZZ", "date": "2026-10-28", "entry": 10, "stop": 9, "target": 12,
        "entry_time": "09:35"})
    assert r.status_code == 422 and "ZZZ" in r.json()["detail"]
