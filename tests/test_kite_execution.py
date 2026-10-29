"""Guarded Kite execution against a fake Kite: gates before any network call,
entry → broker-side stop/target OCO → close, idempotent tags, flat-by, kill,
and bounded paper/confirm strategies. No real order is ever placed."""
from __future__ import annotations

from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlparse

import pandas as pd
import pytest
from cryptography.fernet import Fernet

from apps.api.desk import bars_source, feeds
from apps.api.desk.market.session import NSE
from apps.api.desk.store import get_desk_store, reset_desk_store_for_tests
from apps.api.kite import execution, live, rest
from apps.api.kite.store import get_kite_store, reset_kite_store_for_tests
from apps.api.routes import desk as desk_routes
from apps.api.routes import kite as kite_routes
from tests.desk_utils import desk_client, session_rows
from tests.kite_fake import FakeKite
from tests.scanner_utils import make_store

DAY = date(2026, 10, 28)


def ist(h, m, s=0):
    return datetime(DAY.year, DAY.month, DAY.day, h, m, s, tzinfo=NSE.tz).astimezone(timezone.utc)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBAPP_KEY_ENCRYPTION_SECRET", Fernet.generate_key().decode())
    monkeypatch.setenv("KITE_ORDERS_ENABLED", "1")
    monkeypatch.setenv("AUTOTRADE_USER_IDS", "user_a:AB1234")
    reset_kite_store_for_tests(tmp_path / "kite.sqlite")
    reset_desk_store_for_tests(tmp_path / "desk.sqlite")
    scan = make_store(tmp_path, None)
    scan.upsert_instruments([{"symbol": "SBIN", "yf_symbol": "SBIN.NS", "name": "SBI",
                              "index_memberships": ["NIFTY500"], "fno": True}])
    rows = session_rows(DAY, [800.0] * 9, highs=[800.5] * 9, lows=[799.5] * 9)
    scan.upsert_bars("5m", pd.DataFrame([{"symbol": "SBIN", "ts": r[0].isoformat(), "open": r[1],
                                          "high": r[2], "low": r[3], "close": r[4],
                                          "volume": r[5]} for r in rows]))
    feeds.clear_facts_providers_for_tests()
    rest.clear_instruments_for_tests()
    live.clear_for_tests()
    fake = FakeKite()
    clock = {"now": ist(9, 0)}
    monkeypatch.setattr(kite_routes, "_now", lambda: clock["now"])
    monkeypatch.setattr(desk_routes, "_now", lambda: clock["now"])
    monkeypatch.setattr(kite_routes, "_transport", fake)
    client, user = desk_client(kite_routes.router)
    desk_client_, _ = desk_client(desk_routes.router, user)
    desk_client_.put("/api/desk/profile", json={"max_loss_per_trade_inr": 200})
    yield client, desk_client_, user, fake, clock
    bars_source.clear_live_providers_for_tests()
    live.clear_for_tests()


def _login(client, fake):
    client.put("/api/kite/app", json={"api_key": fake.api_key, "api_secret": fake.api_secret})
    url = client.post("/api/kite/login").json()["login_url"]
    state = parse_qs(parse_qs(urlparse(url).query)["redirect_params"][0])["state"][0]
    assert client.post("/api/kite/callback", json={"request_token": "reqtok1",
                                                   "state": state}).status_code == 200


def _ticket(desk):
    plan = desk.post("/api/desk/plans", json={"symbol": "SBIN", "entry": 800.0,
                                              "entry_time": "09:40", "setup": "level",
                                              "stop_rule": "price", "stop_param": 796,
                                              "target_param": 2}).json()
    tk = desk.post("/api/desk/tickets", json={"plan_id": plan["id"]}).json()
    assert tk["status"] == "ready", tk
    return tk


def _armed(client, fake, clock):
    clock["now"] = ist(9, 5)
    _login(client, fake)
    r = client.post("/api/kite/arm", json={"ack": "ARM 2026-10-28"})
    assert r.status_code == 200, r.text
    clock["now"] = ist(10, 0)


def _send(client, tk):
    return client.post("/api/kite/trades", json={"ticket_id": tk["id"],
                                                 "confirm_text": "buy 50 SBIN"})


def test_non_owner_and_disabled_server_refused_before_any_network(env, monkeypatch):
    client, desk, user, fake, clock = env
    clock["now"] = ist(10, 0)
    tk = _ticket(desk)
    monkeypatch.setenv("AUTOTRADE_USER_IDS", "someone_else")
    r = _send(client, tk)
    assert r.status_code == 409
    assert any("owner-only" in x for x in r.json()["detail"]["reasons"])
    monkeypatch.setenv("KITE_ORDERS_ENABLED", "0")
    reasons = _send(client, tk).json()["detail"]["reasons"]
    assert any("disabled" in x for x in reasons) and any("session" in x for x in reasons)
    assert fake.calls == []        # not a single Kite request
    assert client.post("/api/kite/arm", json={"ack": "ARM 2026-10-28"}).status_code == 409
    st = client.get("/api/kite/status").json()
    assert st["orders"]["server_enabled"] is False and st["orders"]["blocked_by"]


def test_arming_requires_session_typed_ack_and_rearm_after_login(env):
    client, desk, _, fake, clock = env
    clock["now"] = ist(9, 5)
    r = client.post("/api/kite/arm", json={"ack": "ARM 2026-10-28"})
    assert any("session" in x for x in r.json()["detail"]["reasons"])
    _login(client, fake)
    assert client.post("/api/kite/arm", json={"ack": "yes"}).status_code == 409
    assert client.post("/api/kite/arm", json={"ack": "ARM 2026-10-28"}).json()["armed"] is True
    clock["now"] = ist(9, 30)
    _login(client, fake)         # a new login after arming needs a fresh arm
    tk = _ticket(desk)
    r = _send(client, tk)
    assert any("arm again" in x for x in r.json()["detail"]["reasons"])
    assert fake.placed() == []


def test_confirm_text_must_match_ticket(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    tk = _ticket(desk)
    r = client.post("/api/kite/trades", json={"ticket_id": tk["id"], "confirm_text": "BUY 5 SBIN"})
    assert r.status_code == 422 and fake.placed() == []


def test_entry_fill_protects_then_target_cancels_stop(env):
    client, desk, user, fake, clock = env
    _armed(client, fake, clock)
    tk = _ticket(desk)
    r = _send(client, tk)
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["status"] == "entry_sent"
    entry = fake.placed()[0]
    assert entry["transaction_type"] == "BUY" and entry["order_type"] == "LIMIT"
    assert entry["quantity"] == 50 and entry["product"] == "MIS" and entry["tag"].endswith("E")
    assert get_desk_store().get_ticket(tk["id"], "user_a")["status"] == "sent"
    # Other users can't see or touch it.
    user["id"] = "user_b"
    assert client.post(f"/api/kite/trades/{t['id']}/refresh").status_code == 404
    assert client.get("/api/kite/trades").json() == []
    user["id"] = "user_a"
    fake.fill("E", 800.0)
    clock["now"] = ist(10, 0, 20)
    t = client.post(f"/api/kite/trades/{t['id']}/refresh").json()
    assert t["status"] == "protected" and t["entry_avg"] == 800.0
    stop, target = fake.by_tag_suffix("S"), fake.by_tag_suffix("T")
    assert stop["order_type"] == "SL-M" and stop["trigger_price"] == 796.0
    assert stop["market_protection"] == -1 and stop["transaction_type"] == "SELL"
    assert target["order_type"] == "LIMIT" and target["price"] == t["target"]
    n = len(fake.placed())
    t = client.post(f"/api/kite/trades/{t['id']}/refresh").json()   # idempotent
    assert len(fake.placed()) == n and t["status"] == "protected"
    fake.fill("T", t["target"])
    t = client.post(f"/api/kite/trades/{t['id']}/refresh").json()
    assert t["status"] == "closed" and t["exit_reason"] == "target"
    assert fake.by_tag_suffix("S")["status"] == "CANCELLED"
    assert t["pnl_gross"] == round((t["target"] - 800.0) * 50, 2) and t["r_gross"] > 0
    perf = client.get("/api/kite/performance").json()
    assert perf["trades"] == 1


def test_oco_race_needs_attention_and_stop_fill_closes(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    t = _send(client, _ticket(desk)).json()
    fake.fill("E", 800.0)
    t = client.post(f"/api/kite/trades/{t['id']}/refresh").json()
    fake.fill("S", 795.5)
    fake.fill("T", t["target"])
    t = client.post(f"/api/kite/trades/{t['id']}/refresh").json()
    assert t["status"] == "needs_attention" and "OCO" in t["attention"]


def test_stop_rejected_after_fill_exits_immediately(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    t = _send(client, _ticket(desk)).json()
    fake.fill("E", 800.0)
    fake.reject_next["SL-M"] = "Trigger price invalid"
    t = client.post(f"/api/kite/trades/{t['id']}/refresh").json()
    assert t["status"] == "exit_sent" and t["exit_reason"] == "protection_failed"
    x = fake.by_tag_suffix("X")
    assert x["order_type"] == "MARKET" and x["quantity"] == 50 and x["market_protection"] == -1
    fake.fill("X", 799.0)
    t = client.post(f"/api/kite/trades/{t['id']}/refresh").json()
    assert t["status"] == "closed" and t["pnl_gross"] == -50.0


def test_unfilled_entry_times_out_and_cancels(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    t = _send(client, _ticket(desk)).json()
    clock["now"] = ist(10, 1, 30)
    t = client.post(f"/api/kite/trades/{t['id']}/refresh").json()
    assert any("cancel sent" in e["what"] for e in t["events"])
    t = client.post(f"/api/kite/trades/{t['id']}/refresh").json()
    assert t["status"] == "entry_cancelled"


def test_flat_by_exits_protected_position(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    t = _send(client, _ticket(desk)).json()
    fake.fill("E", 800.0)
    client.post(f"/api/kite/trades/{t['id']}/refresh")
    clock["now"] = ist(15, 15)
    t = client.post(f"/api/kite/trades/{t['id']}/refresh").json()
    assert t["status"] == "exit_sent" and t["exit_reason"] == "flat_by"
    assert fake.by_tag_suffix("S")["status"] == "CANCELLED"
    assert fake.by_tag_suffix("T")["status"] == "CANCELLED"


def test_retry_adopts_existing_tagged_order(env):
    _, _, _, fake, clock = env
    client = rest.KiteClient(fake.api_key, "unused", fake)
    t = {"id": "abcdef1234567890", "symbol": "SBIN", "product": "MIS"}
    orders = {execution.tag_for(t["id"], "entry"): {"order_id": "ordX", "status": "OPEN"}}
    rec = execution._place(client, t, "entry", orders, transaction_type="BUY")
    assert rec["order_id"] == "ordX" and fake.calls == []


def test_kill_exits_disarms_stops_strategies_and_blocks_rest_of_day(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    s = client.post("/api/kite/strategies", json={"name": "orb", "symbols": ["SBIN"],
                                                  "mode": "paper"}).json()
    t = _send(client, _ticket(desk)).json()
    fake.fill("E", 800.0)
    client.post(f"/api/kite/trades/{t['id']}/refresh")
    out = client.post("/api/kite/kill").json()
    assert out["killed"] is True and out["armed"] is False and out["summary"]["exits_sent"] == 1
    assert fake.by_tag_suffix("X")["order_type"] == "MARKET"
    assert fake.by_tag_suffix("S")["status"] == "CANCELLED"
    assert get_kite_store().get_strategy(s["id"], "user_a")["mode"] == "off"
    r = client.post("/api/kite/arm", json={"ack": "ARM 2026-10-28"})
    assert any("kill switch" in x for x in r.json()["detail"]["reasons"])


def test_live_losses_count_toward_max_daily_loss(env):
    client, desk, _, fake, clock = env
    desk.put("/api/desk/profile", json={"max_loss_per_trade_inr": 200, "max_daily_loss_inr": 300})
    _armed(client, fake, clock)
    get_kite_store().create_trade("user_a", {"status": "closed", "trade_date": "2026-10-28",
                                             "symbol": "RELIANCE", "pnl_net": -150.0})
    r = _send(client, _ticket(desk))
    assert r.status_code == 409 and "max daily loss" in r.text and fake.placed() == []


def test_restart_adopts_entry_that_reached_kite_but_was_never_saved(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    t = _send(client, _ticket(desk)).json()
    stored = get_kite_store().get_trade(t["id"], "user_a")
    stored["legs"] = {}                      # simulate a crash before the order id was saved
    get_kite_store().update_trade("user_a", stored)
    fake.fill("E", 800.0)
    t = client.post(f"/api/kite/trades/{t['id']}/refresh").json()
    assert t["status"] == "protected" and len(fake.placed()) == 3
    assert any("adopted entry" in e["what"] for e in t["events"])


def test_manual_exit_of_pending_entry(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    t = _send(client, _ticket(desk)).json()
    out = client.post(f"/api/kite/trades/{t['id']}/exit").json()
    assert out["status"] == "closed" and fake.by_tag_suffix("E")["status"] == "CANCELLED"


# ---------------------------------------------------------------- strategies

def _breakout_provider(rows):
    def provider(user_id, symbol, interval, kind):
        if kind == "session" and symbol == "SBIN" and user_id == "user_a":
            return bars_source.BarSeries(symbol, interval, rows, "live:kite",
                                         rows[-1][0].isoformat())
        return None
    return provider


def _breakout_rows():
    closes = [800.0, 801.0, 800.5, 802.5]
    return session_rows(DAY, closes, highs=[801.0, 801.5, 801.0, 803.0],
                        lows=[799.0, 800.0, 800.0, 801.0])


def test_strategy_validation_and_isolation(env):
    client, _, user, _, _ = env
    assert client.post("/api/kite/strategies", json={"name": "x", "symbols": ["SBIN"],
                                                     "mode": "auto"}).status_code == 422
    assert client.post("/api/kite/strategies", json={"name": "x", "symbols": ["bad sym"]}
                       ).status_code == 422
    s = client.post("/api/kite/strategies", json={"name": "x", "symbols": ["sbin.ns"]}).json()
    assert s["symbols"] == ["SBIN"] and s["mode"] == "off"
    user["id"] = "user_b"
    assert client.get("/api/kite/strategies").json() == []
    assert client.put(f"/api/kite/strategies/{s['id']}", json={"name": "y", "symbols": ["SBIN"]}
                      ).status_code == 404
    assert client.delete(f"/api/kite/strategies/{s['id']}").status_code == 404


def test_paper_strategy_signals_into_paper_ledger_once(env):
    client, desk, _, _, clock = env
    bars_source.register_live_provider(_breakout_provider(_breakout_rows()))
    clock["now"] = ist(9, 36)
    s = client.post("/api/kite/strategies", json={"name": "orb", "symbols": ["SBIN"],
                                                  "mode": "paper", "or_minutes": 15}).json()
    out = client.post(f"/api/kite/strategies/{s['id']}/scan").json()
    assert len(out["new"]) == 1, out
    sig = out["new"][0]
    assert sig["status"] == "paper" and sig["source"] == "live:kite" and sig["paper_trade_id"]
    assert desk.get("/api/desk/paper").json()["trades"][0]["symbol"] == "SBIN"
    assert client.post(f"/api/kite/strategies/{s['id']}/scan").json()["new"] == []


def test_confirm_strategy_waits_then_expires_and_cancels_ticket(env):
    client, desk, _, fake, clock = env
    bars_source.register_live_provider(_breakout_provider(_breakout_rows()))
    clock["now"] = ist(9, 36)
    s = client.post("/api/kite/strategies", json={"name": "orb", "symbols": ["SBIN"],
                                                  "mode": "confirm", "confirm_ttl_s": 60}).json()
    sig = client.post(f"/api/kite/strategies/{s['id']}/scan").json()["new"][0]
    assert sig["status"] == "await_confirm" and sig["order"]["qty"] > 0
    # Confirming without arming is refused by the live gates (no orders).
    words = f"BUY {sig['order']['qty']} SBIN"
    r = client.post(f"/api/kite/strategies/{s['id']}/signals/{sig['id']}/confirm",
                    json={"confirm_text": words})
    assert r.status_code == 409 and fake.placed() == []
    clock["now"] = ist(9, 37, 1)
    r = client.post(f"/api/kite/strategies/{s['id']}/signals/{sig['id']}/confirm",
                    json={"confirm_text": words})
    assert r.status_code == 409 and "expired" in r.text
    assert get_desk_store().get_ticket(sig["ticket_id"], "user_a")["status"] == "cancelled"
