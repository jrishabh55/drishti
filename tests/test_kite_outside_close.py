"""Kill landing mid-send, and trades the user closes outside the engine —
against the fake Kite only (no network, no real orders)."""
from __future__ import annotations

from datetime import timedelta

import pytest

from apps.api.desk.store import get_desk_store
from apps.api.kite import execution
from apps.api.kite.store import get_kite_store
from tests.test_kite_execution import (  # noqa: F401 — the shared fixture
    DAY, _armed, _send, _ticket, env, ist)
from tests.test_kite_safety import ORDERS, never_short, protected, refresh


def is_stop(d):
    return d.get("order_type") == "SL-M"


# ---------------------------------------------------------------- R1: kill during the send's broker reads

@pytest.mark.parametrize("read", ["/instruments/NSE", "/portfolio/positions"])
def test_kill_recorded_during_send_reads_places_no_buy(env, read):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    tk = _ticket(desk)

    def kill_lands(method, path, data):
        if path == read and not execution.control("user_a", clock["now"])["killed"]:
            # what kill() records first, before it waits for the user's lock
            execution._update_control("user_a", lambda d: d.update(
                armed_on=None, armed_at=None, killed_on=DAY.isoformat()))
    fake.after.append(kill_lands)
    r = _send(client, tk)
    assert execution.control("user_a", clock["now"])["killed"], "the hook never fired"
    assert r.status_code == 409 and any("kill switch" in x for x in r.json()["detail"]["reasons"])
    assert fake.placed() == []                                   # no BUY left the server
    assert get_kite_store().list_trades("user_a") == []          # no intent persisted
    assert get_desk_store().get_ticket(tk["id"], "user_a")["status"] == "ready"   # not "sent"


# ---------------------------------------------------------------- R3: closed outside the engine

def _sold_in_kite_app(fake, qty=50):
    fake.manual["SBIN"] = fake.manual.get("SBIN", 0) - qty      # an untagged manual SELL


def _drive_for(client, t, clock, start, seconds):
    for s in seconds:
        clock["now"] = start + timedelta(seconds=s)
        t = refresh(client, t)
    return t


def test_flattened_in_kite_with_working_stop_and_target_is_never_auto_closed(env):
    client, desk, _, fake, clock = env
    t = protected(env)
    _sold_in_kite_app(fake)
    t = _drive_for(client, t, clock, ist(10, 1), (0, 40, 90))
    assert t["status"] == "protected"            # our stop/target still work at Kite
    tr = get_kite_store().get_trade(t["id"], "user_a")
    tr.update(status="needs_attention", attention="resize needs a human", attention_kind="manual")
    get_kite_store().update_trade("user_a", tr)  # held for a human, legs still working
    t = _drive_for(client, t, clock, ist(10, 3), (0, 40, 90))
    assert t["status"] == "needs_attention" and not t.get("closed_note")
    assert fake.by_tag_suffix("S")["status"] == "TRIGGER PENDING"
    assert fake.by_tag_suffix("T")["status"] == "OPEN"
    assert [o["order_type"] for o in fake.placed()] == ["LIMIT", "SL-M", "LIMIT"]   # nothing new


def test_flattened_in_kite_then_orders_finished_closes_outside_without_inventing_pnl(env):
    client, desk, _, fake, clock = env
    t = protected(env)
    _sold_in_kite_app(fake)
    clock["now"] = ist(10, 1)
    t = client.post(f"/api/kite/trades/{t['id']}/exit").json()        # cancels stop + target
    t = _drive_for(client, t, clock, ist(10, 1), (5, 20, 45))
    assert t["status"] == "needs_attention" and "not selling" in t["attention"]
    assert fake.by_tag_suffix("S")["status"] == "CANCELLED"
    t = _drive_for(client, t, clock, ist(10, 2), (0, 15, 40))
    assert t["status"] == "closed_outside", t.get("attention")
    assert t["closed_note"].startswith("Closed outside the engine")
    for k in ("pnl_net", "pnl_gross", "exit_avg", "exit_fill"):
        assert t.get(k) is None                    # unknown, never made up
    assert t["attention"] is None
    assert not any(o["order_type"] == "MARKET" for o in fake.placed())   # never sold by the engine
    never_short(fake)
    assert get_kite_store().list_trades("user_a", open_only=True) == []

    # Same day: the unknown loss blocks fresh live entries (not counted as 0).
    prof = execution.load_profile_safe("user_a")
    tk = get_desk_store().get_ticket(t["ticket_id"], "user_a")
    with pytest.raises(execution.ExecRefused) as refused:
        execution.send_entry("user_a", tk, ist(10, 5), prof, lambda: None)
    assert any("closed outside the engine" in x and "P&L is unknown" in x for x in refused.value.reasons)
    assert len([o for o in fake.placed() if o["transaction_type"] == "BUY"]) == 1

    # Next trading day: nothing in flight for SBIN and no carried block.
    nxt = ist(10, 0) + timedelta(days=1)
    assert execution.daily_loss_reasons("user_a", nxt, prof, 100) == []


def test_ambiguous_leg_is_not_dropped_because_kite_shows_flat(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    t = _send(client, _ticket(desk)).json()
    fake.fill("E", 800.0)
    fake.hidden_types.add("SL-M")                  # Kite has the stop; its book doesn't show it
    fake.faults.append(("POST", ORDERS, is_stop, "timeout"))
    clock["now"] = ist(10, 0, 20)
    t = refresh(client, t)
    assert t["status"] == "needs_attention" and t["attention_kind"] == "ambiguous"
    _sold_in_kite_app(fake)
    t = _drive_for(client, t, clock, ist(10, 1), (0, 40, 90, 150))
    assert t["status"] == "needs_attention" and t["legs"]["stop"]["status"] in ("UNKNOWN", "ABSENT")
    assert not t.get("closed_note")


def test_previous_day_trade_is_not_judged_by_todays_positions(env):
    client, desk, _, fake, clock = env
    t = protected(env)
    _sold_in_kite_app(fake)
    clock["now"] = ist(10, 1)
    client.post(f"/api/kite/trades/{t['id']}/exit")
    tr = get_kite_store().get_trade(t["id"], "user_a")
    tr["trade_date"] = (DAY - timedelta(days=1)).isoformat()
    get_kite_store().update_trade("user_a", tr)
    t = _drive_for(client, t, clock, ist(10, 1), (5, 20, 45, 60, 80, 120))
    assert t["status"] != "closed_outside"
