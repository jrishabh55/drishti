"""NSE closing auction session (CAS) timing and broker tick checks for live
MIS orders, against the fake Kite. CAS: F&O stocks, 2026-08-03 … 2026-12-06,
continuous trading to 15:15, Zerodha MIS square-off 15:12."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from apps.api.desk.market.session import NSE
from apps.api.kite import rest, sessions
from apps.api.kite.store import get_kite_store
from tests import kite_fake
from tests.test_kite_execution import _armed, env, ist  # noqa: F401


def at(d: date, h: int, m: int) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, tzinfo=NSE.tz).astimezone(timezone.utc)


def _send(client, tk):
    return client.post("/api/kite/trades", json={
        "ticket_id": tk["id"], "confirm_text": f"BUY {tk['order']['qty']} {tk['order']['symbol']}"})


def refresh(client, t):
    return client.post(f"/api/kite/trades/{t['id']}/refresh").json()


@pytest.mark.parametrize("day,cas", [(date(2026, 8, 2), False), (date(2026, 8, 3), True),
                                      (date(2026, 10, 29), True), (date(2026, 12, 6), True),
                                      (date(2026, 12, 7), False)])
def test_cas_window_date_edges_for_an_fo_stock(day, cas):
    assert (sessions.cas_rule("RELIANCE", day) is not None) is cas
    reasons = sessions.timing_reasons("RELIANCE", at(day, 10, 0), "15:15", "15:20")
    assert bool(reasons) is cas
    cut = sessions.cutoffs("RELIANCE", day, "15:15", "15:20")
    assert (cut.flatten_by, cut.no_orders_after) == ((15 * 60 + 10, 15 * 60 + 12) if cas
                                                     else (15 * 60 + 15, 15 * 60 + 20))


def test_current_reliance_needs_an_earlier_flat_by_and_an_entry_cutoff():
    now = at(date(2026, 10, 29), 10, 0)
    (why,) = sessions.timing_reasons("RELIANCE", now, "15:15", "15:20")
    assert why.startswith("action required") and "set flat-by to 15:10 or earlier" in why
    assert "15:12" in why and "2026-12-06" in why
    assert sessions.timing_reasons("RELIANCE", now, "15:10", "15:20") == []
    late = sessions.timing_reasons("RELIANCE", at(date(2026, 10, 29), 15, 10), "15:05", "15:20")
    assert len(late) == 1 and late[0].startswith("too late for RELIANCE")


def test_non_fo_stock_keeps_the_profile_square_off():
    day = date(2026, 10, 29)
    assert "20MICRONS" not in sessions._fo()
    assert sessions.timing_reasons("20MICRONS", at(day, 10, 0), "15:15", "15:20") == []
    assert sessions.cutoffs("20MICRONS", day, "15:15", "15:20").no_orders_after == 15 * 60 + 20


def _plan(desk, entry=800.0, stop=796.0, symbol="SBIN"):
    plan = desk.post("/api/desk/plans", json={"symbol": symbol, "entry": entry,
                                              "entry_time": "09:40", "setup": "level",
                                              "stop_rule": "price", "stop_param": stop,
                                              "target_param": 2}).json()
    tk = desk.post("/api/desk/tickets", json={"plan_id": plan["id"]}).json()
    assert tk["status"] == "ready", tk
    return tk


def test_live_entry_refused_with_action_when_profile_flat_by_is_too_late(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    desk.put("/api/desk/profile", json={"max_loss_per_trade_inr": 200})   # default 15:15
    n = len(fake.calls)
    tk = _plan(desk)
    r = client.post("/api/kite/trades", json={"ticket_id": tk["id"],
                                              "confirm_text": f"BUY {tk['order']['qty']} SBIN"})
    assert r.status_code == 409
    assert any("set flat-by to 15:10" in x for x in r.json()["detail"]["reasons"])
    assert len(fake.calls) == n and fake.placed() == []          # refused before the network
    st = client.get("/api/kite/status").json()["cas"]
    assert st["active"] and st["profile_ok"] is False and st["latest_flat_by"] == "15:10"


def test_non_cas_stock_keeps_the_15_15_flat_by(env, monkeypatch):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    desk.put("/api/desk/profile", json={"max_loss_per_trade_inr": 200})
    monkeypatch.setattr(sessions, "_fo", lambda: frozenset())    # SBIN not in F&O
    tk = _plan(desk)
    assert _send(client, tk).status_code == 201


def test_old_trade_with_late_flat_by_is_flattened_before_the_broker(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    t = _send(client, _plan(desk)).json()
    fake.fill("E", 800.0)
    clock["now"] = ist(10, 0, 20)
    refresh(client, t)
    stored = get_kite_store().get_trade(t["id"], "user_a")
    stored["flat_by"] = "15:15"          # e.g. sent before the CAS schedule was known
    get_kite_store().update_trade("user_a", stored)
    clock["now"] = ist(15, 10)
    t = refresh(client, t)
    assert t["status"] == "exit_sent" and t["exit_reason"] == "flat_by"
    assert any("flattening at 15:10" in e["what"] and "closing auction" in e["what"]
               for e in t["events"])
    assert fake.by_tag_suffix("X")["order_type"] == "MARKET"


def test_no_order_is_sent_at_or_after_the_broker_square_off(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    t = _send(client, _plan(desk)).json()
    fake.fill("E", 800.0)
    clock["now"] = ist(10, 0, 20)
    refresh(client, t)
    stored = get_kite_store().get_trade(t["id"], "user_a")
    stored["flat_by"] = "15:15"
    get_kite_store().update_trade("user_a", stored)
    n = len(fake.placed())
    clock["now"] = ist(15, 13)           # the engine was down until after 15:12
    t = refresh(client, t)
    assert t["status"] == "needs_attention" and "no order is sent" in t["attention"]
    assert len(fake.placed()) == n       # nothing into the auction / after the square-off


def test_unfilled_partial_after_square_off_is_not_protected_with_sl(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    t = _send(client, _plan(desk)).json()
    stored = get_kite_store().get_trade(t["id"], "user_a")
    stored["flat_by"] = "15:15"
    get_kite_store().update_trade("user_a", stored)
    fake.fill("E", 800.0, qty=10)
    clock["now"] = ist(15, 14)
    t = refresh(client, t)
    assert not [o for o in fake.orders.values() if o["transaction_type"] == "SELL"]
    assert t["status"] == "needs_attention"


def test_prices_off_the_broker_tick_are_refused_before_sending(env, monkeypatch):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    monkeypatch.setattr(kite_fake, "INSTRUMENTS", kite_fake.INSTRUMENTS.replace(
        "SBIN,STATE BANK OF INDIA,0,,0,0.05", "SBIN,STATE BANK OF INDIA,0,,0,0.1"))
    rest.clear_instruments_for_tests()
    tk = _plan(desk, entry=800.05, stop=796.0)
    r = client.post("/api/kite/trades", json={"ticket_id": tk["id"],
                                              "confirm_text": f"BUY {tk['order']['qty']} SBIN"})
    assert r.status_code == 409
    reasons = r.json()["detail"]["reasons"]
    assert any("limit price ₹800.05" in x and "tick ₹0.1 on Kite" in x for x in reasons)
    assert fake.placed() == [] and get_kite_store().list_trades("user_a") == []
    # the ticket is still ready: nothing left the server
    assert desk.get("/api/desk/tickets").json()[0]["status"] == "ready"


def test_prices_on_the_broker_tick_go_out_and_the_tick_is_recorded(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    t = _send(client, _plan(desk, entry=800.05, stop=796.0)).json()
    assert t["status"] == "entry_sent" and t["tick_size"] == 0.05


def test_instrument_list_unavailable_refuses_without_sending(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    fake.faults.append(("GET", "/instruments/NSE", lambda d: True, "5xx"))
    rest.clear_instruments_for_tests()
    r = _send(client, _plan(desk))
    assert r.status_code == 409 and "instrument list" in r.text and fake.placed() == []
