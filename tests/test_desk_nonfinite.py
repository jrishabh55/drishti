"""NaN/Infinity never reach saved desk state; legacy rows keep loading."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from apps.api.desk.store import reset_desk_store_for_tests
from apps.api.routes import desk as desk_routes, watch_rules
from tests.desk_utils import desk_client

NOW = datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc)
PLAN = {"symbol": "RELIANCE", "date": "2026-10-28", "setup": "level", "level": 1000, "entry": 1000,
        "entry_time": "10:00", "stop_rule": "price", "stop_param": 990, "target_rule": "r", "target_param": 2}


def _raw(client, method, url, body):
    """JSON with bare NaN/Infinity literals, as a hand-made API call can send."""
    return client.request(method, url, content=json.dumps(body), headers={"content-type": "application/json"})


@pytest.fixture()
def env(tmp_path, monkeypatch):
    store = reset_desk_store_for_tests(tmp_path / "desk.sqlite")
    monkeypatch.setattr(desk_routes, "_now", lambda: NOW)
    client, _ = desk_client(desk_routes.router)
    return client, store


@pytest.mark.parametrize("field,value", [("target_param", float("nan")), ("stop_param", float("nan")),
                                         ("entry", float("inf")), ("level", float("-inf"))])
def test_plan_rejects_non_finite_numbers(env, field, value):
    client, store = env
    assert _raw(client, "POST", "/api/desk/plans", dict(PLAN, **{field: value})).status_code == 422
    assert client.get("/api/desk/plans").json() == []
    assert _raw(client, "POST", "/api/desk/plans", PLAN).status_code == 201


def test_a_legacy_nan_plan_cannot_become_a_ready_ticket(env):
    client, store = env
    pid = _raw(client, "POST", "/api/desk/plans", PLAN).json()["id"]
    with store._conn() as c:
        doc = json.loads(c.execute("SELECT doc FROM desk_plans WHERE id=?", (pid,)).fetchone()[0])
        doc["target"] = float("nan")
        c.execute("UPDATE desk_plans SET doc=? WHERE id=?", (json.dumps(doc), pid))
    r = client.post("/api/desk/tickets", json={"plan_id": pid})
    assert r.status_code == 422 and "target" in r.json()["detail"]


@pytest.mark.parametrize("url,body", [
    ("/api/desk/alerts", {"type": "above", "symbol": "TCS", "price": float("nan")}),
    ("/api/desk/alerts", {"type": "pct_move", "symbol": "TCS", "pct": float("inf")}),
    ("/api/desk/timemachine", {"symbol": "TCS", "date": "2026-10-27", "entry": float("inf"), "stop": 1, "target": 2}),
    ("/api/desk/risk", {"orders": [{"symbol": "TCS", "side": "BUY", "qty": 1, "price": float("nan")}]}),
    ("/api/desk/paper/p1/close", {"price": float("inf")}),
])
def test_money_inputs_reject_non_finite(env, url, body):
    client, _ = env
    assert _raw(client, "POST", url, body).status_code == 422


@pytest.mark.parametrize("q", ["level=nan", "level=inf", "min_volume_x=inf"])
def test_entry_timer_rejects_non_finite_query(env, q):
    client, _ = env
    assert client.get(f"/api/desk/entry/TCS?setup=level&{q}").status_code == 422


def test_profile_rejects_infinity_and_keeps_legacy_profiles(env):
    client, store = env
    prof = client.get("/api/desk/profile").json()
    assert _raw(client, "PUT", "/api/desk/profile", dict(prof, max_order_inr=float("inf"))).status_code == 422
    good = dict(prof, capital=250_000, max_order_inr=40_000)
    assert client.put("/api/desk/profile", json=good).json()["capital"] == 250_000
    assert client.get("/api/desk/profile").json() == good  # a good saved profile is untouched
    store.put_profile("user_a", dict(good, max_daily_loss_inr=float("inf")))  # saved before the fix
    loaded = client.get("/api/desk/profile").json()
    assert loaded["capital"] == 250_000 and loaded["max_order_inr"] == 40_000
    assert loaded["max_daily_loss_inr"] == 1_500  # only the bad value falls back


def test_watch_rule_rejects_non_finite_and_skips_legacy_bad_rows(tmp_path):
    reset_desk_store_for_tests(tmp_path / "desk.sqlite")
    client, _ = desk_client(watch_rules.router, {"id": "u"})
    assert _raw(client, "PUT", "/api/desk/watch-rules/TCS", {"setup": "level", "level": float("inf")}).status_code == 422
    assert _raw(client, "PUT", "/api/desk/watch-rules/TCS", {"min_volume_x": float("nan")}).status_code == 422
    assert client.put("/api/desk/watch-rules/INFY", json={"setup": "level", "level": 1500}).status_code == 200
    from apps.api.desk.store import get_desk_store
    with get_desk_store()._conn() as c:  # a row saved before the fix
        c.execute("INSERT INTO desk_watch_rules VALUES ('u','TCS',?)",
                  (json.dumps({"setup": "level", "level": float("inf"), "interval": "5m", "min_volume_x": None}),))
    r = client.get("/api/desk/watch-rules")
    assert r.status_code == 200 and list(r.json()) == ["INFY"] and r.json()["INFY"]["level"] == 1500
