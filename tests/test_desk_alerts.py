"""Alert rules (pure) + the alerts API + the background sweep."""
from __future__ import annotations

from datetime import date, timezone

import pandas as pd
import pytest

from apps.api.desk import alerts, loop
from apps.api.desk.market.session import NSE
from apps.api.desk.store import reset_desk_store_for_tests
from apps.api.routes import desk as desk_routes
from tests.desk_utils import desk_client, session_rows
from tests.scanner_utils import make_store

DAY = date(2026, 10, 28)


def ist(h, m, d=DAY):
    return NSE.local(d, h * 60 + m).astimezone(timezone.utc)


BAR = {"start": "10:00", "o": 800.0, "h": 805.0, "l": 795.0, "c": 801.0}


def test_above_below_ring_once_on_bar_high_low_and_rearm():
    r = alerts.validate({"type": "target", "symbol": "sbin", "price": 804})
    msg, r2 = alerts.evaluate(r, now=ist(10, 6), last_bar=BAR)
    assert msg and "reached your target ₹804.00" in msg and r2["rang_at"]
    assert alerts.evaluate(r2, now=ist(10, 11), last_bar=BAR)[0] is None   # once
    re = alerts.validate({"type": "below", "symbol": "SBIN", "price": 796, "rearm": True})
    msg, re2 = alerts.evaluate(re, now=ist(10, 6), last_bar=BAR)
    assert msg
    calm = dict(BAR, l=799.0)
    _, re3 = alerts.evaluate(re2, now=ist(10, 11), last_bar=calm)
    assert "rang_at" not in re3
    assert alerts.evaluate(re3, now=ist(10, 16), last_bar=BAR)[0]


def test_time_bell_pct_and_date_scoping():
    t = alerts.validate({"type": "time", "at": "3:10 PM", "symbol": "SBIN", "label": "flat by"})
    assert alerts.evaluate(t, now=ist(15, 5))[0] is None
    msg, t2 = alerts.evaluate(t, now=ist(15, 10))
    assert "Flat by: it's 15:10 IST" in msg and "SBIN is still open" in msg
    assert alerts.evaluate(t2, now=ist(15, 20))[0] is None
    bell = alerts.validate({"type": "opening_bell"})
    assert alerts.evaluate(bell, now=ist(9, 10))[0] is None
    assert "NSE is open" in alerts.evaluate(bell, now=ist(9, 15))[0]
    assert alerts.evaluate(bell, now=ist(9, 20, date(2026, 10, 2)))[0] is None   # holiday
    pct = alerts.validate({"type": "pct_move", "symbol": "SBIN", "pct": 2, "direction": "up"})
    assert alerts.evaluate(pct, now=ist(10, 6), last_bar=dict(BAR, c=817), prev_close=800)[0]
    assert alerts.evaluate(pct, now=ist(10, 6), last_bar=dict(BAR, c=780), prev_close=800)[0] is None
    old = alerts.validate({"type": "stop", "symbol": "SBIN", "price": 900, "date": "2026-10-27"})
    assert alerts.evaluate(old, now=ist(10, 6), last_bar=BAR)[0] is None


@pytest.mark.parametrize("rule,msg", [({"type": "nope"}, "type must"),
                                      ({"type": "above", "symbol": "X"}, "needs a price"),
                                      ({"type": "pct_move", "symbol": "X", "pct": 200}, "between"),
                                      ({"type": "time", "at": "25:00"}, "needs at")])
def test_validation(rule, msg):
    with pytest.raises(alerts.AlertError, match=msg):
        alerts.validate(rule)


@pytest.fixture()
def api(tmp_path, monkeypatch):
    reset_desk_store_for_tests(tmp_path / "desk.sqlite")
    scan = make_store(tmp_path, None)
    rows = session_rows(DAY, [800.0] * 10, highs=[800.5] * 9 + [809.0], lows=[799.5] * 10)
    scan.upsert_bars("5m", pd.DataFrame([{"symbol": "SBIN", "ts": r[0].isoformat(),
                                          "open": r[1], "high": r[2], "low": r[3],
                                          "close": r[4], "volume": r[5]} for r in rows]))
    monkeypatch.setattr(desk_routes, "_now", lambda: ist(10, 6))
    client, user = desk_client(desk_routes.router)
    return client, user


def test_alerts_api_from_plan_events_and_sweep(api):
    client, user = api
    plan = client.post("/api/desk/plans", json={"symbol": "SBIN", "entry": 800.0,
                                                "date": "2026-10-28", "entry_time": "09:40",
                                                "setup": "level", "stop_rule": "price",
                                                "stop_param": 796}).json()
    made = client.post(f"/api/desk/alerts/from-plan/{plan['id']}").json()
    assert [a["type"] for a in made] == ["stop", "target", "time"]
    assert client.post("/api/desk/alerts", json={"type": "above", "symbol": "SBIN"}
                       ).status_code == 422
    # The 10:00 bar's high is 809: the 808 target rings, once.
    assert loop.sweep() == 1
    ev = client.get("/api/desk/alerts/events?check=false").json()
    assert len(ev) == 1 and "target" in ev[0]["message"] and not ev[0]["seen"]
    client.get("/api/desk/alerts/events")
    assert len(client.get("/api/desk/alerts/events").json()) == 1
    assert client.post("/api/desk/alerts/events/seen").status_code == 204
    assert client.get("/api/desk/alerts/events?unseen_only=true").json() == []
    user["id"] = "user_b"
    assert client.get("/api/desk/alerts").json() == []
    assert client.delete(f"/api/desk/alerts/{made[0]['id']}").status_code == 404
