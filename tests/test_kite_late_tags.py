"""Late-visible orders, position checks, the kill switch, resize limits, the
single-process lease and the postback worker — against the fake Kite only
(no network, no real orders).

The invariant under test everywhere: absence from Kite's order book is not
proof an order doesn't exist, so an ambiguous order is never re-sent, never
replaced by a speculative market exit, never left behind by a closed trade;
and the engine never sells more than its trade holds in Kite's own position."""
from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
import time

from apps.api.desk.store import get_desk_store
from apps.api.kite import execution, live
from apps.api.kite.store import get_kite_store
from tests.test_kite_execution import (  # noqa: F401 — the shared fixture
    _armed, _send, _ticket, env, ist)
from tests.test_kite_safety import ORDERS, is_entry, never_short, protected, refresh


def is_market(d):
    return d.get("order_type") == "MARKET"


def is_stop(d):
    return d.get("order_type") == "SL-M"


def count(fake, order_type):
    return sum(1 for o in fake.orders.values() if o["order_type"] == order_type)


# ---------------------------------------------------------------- late tags

def test_accepted_stop_listed_after_100s_is_adopted_never_doubled_or_orphaned(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    t = _send(client, _ticket(desk)).json()
    fake.fill("E", 800.0)
    fake.hidden_types.add("SL-M")                     # Kite has it; its book doesn't show it
    fake.faults.append(("POST", ORDERS, is_stop, "timeout"))
    clock["now"] = ist(10, 0, 20)
    t = refresh(client, t)
    assert t["status"] == "needs_attention" and "UNPROTECTED" in t["attention"]
    for s in (40, 80, 125):                           # > 100 s of absence, several reads
        clock["now"] = ist(10, 0, 20) + __import__("datetime").timedelta(seconds=s)
        t = refresh(client, t)
    assert t["legs"]["stop"]["status"] == "ABSENT" and t["status"] == "needs_attention"
    assert count(fake, "MARKET") == 0 and count(fake, "SL-M") == 1   # no speculative exit
    fake.reveal()                                     # the order book catches up
    t = refresh(client, t)
    assert t["status"] == "protected" and t["attention"] is None
    assert any("adopted stop" in e["what"] for e in t["events"])
    assert count(fake, "SL-M") == 1 and int(fake.by_tag_suffix("T")["quantity"]) == 50
    fake.fill("S", 796.0)
    t = refresh(client, t)
    assert t["status"] == "closed" and t["exit_reason"] == "stop"
    assert fake.by_tag_suffix("T")["status"] == "CANCELLED"
    working = [o for o in fake.orders.values()
               if o["status"] not in ("COMPLETE", "CANCELLED", "REJECTED")]
    assert working == []                              # nothing left behind at Kite
    never_short(fake)


def test_entry_listed_late_is_adopted_and_protected_not_left_naked(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    fake.hidden_types.add("LIMIT")
    fake.faults.append(("POST", ORDERS, is_entry, "timeout"))
    t = _send(client, _ticket(desk)).json()
    assert t["status"] == "needs_attention" and t["attention_kind"] == "ambiguous"
    for sec in (30, 70, 110):
        clock["now"] = ist(10, 0, sec % 60) if sec < 60 else ist(10, 1, sec - 60)
        t = refresh(client, t)
    assert t["legs"]["entry"]["status"] == "ABSENT" and t["status"] == "needs_attention"
    fake.reveal()
    fake.fill("E", 800.0)
    clock["now"] = ist(10, 2, 0)
    t = refresh(client, t)
    assert t["status"] == "protected"
    assert sum(is_entry(d) for d in fake.placed()) == 1        # one BUY, ever
    assert count(fake, "SL-M") == 1 and count(fake, "LIMIT") == 2
    never_short(fake)


def test_exit_listed_late_never_gets_a_second_exit(env):
    client, desk, _, fake, clock = env
    t = protected(env)
    fake.hidden_types.add("MARKET")
    fake.faults.append(("POST", ORDERS, is_market, "timeout"))
    t = client.post(f"/api/kite/trades/{t['id']}/exit").json()
    assert t["status"] == "needs_attention" and t["legs"]["exit"]["status"] in ("UNKNOWN", "ABSENT")
    for m in (1, 2, 3):
        clock["now"] = ist(10, m, 0)
        t = refresh(client, t)
    assert t["legs"]["exit"]["status"] == "ABSENT" and "exit2" not in t["legs"]
    assert count(fake, "MARKET") == 1
    fake.reveal()
    fake.fill("X", 801.0)
    t = refresh(client, t)
    assert t["status"] == "closed" and count(fake, "MARKET") == 1
    never_short(fake)


def test_ambiguity_resolves_to_not_placed_only_after_the_orders_expire(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    t = _send(client, _ticket(desk)).json()
    fake.fill("E", 800.0)
    fake.faults.append(("POST", ORDERS, is_stop, "down"))
    clock["now"] = ist(10, 0, 20)
    refresh(client, t)
    clock["now"] = ist(15, 0)
    t = refresh(client, t)
    assert t["legs"]["stop"]["status"] == "ABSENT" and t["status"] == "needs_attention"
    clock["now"] = ist(15, 36)
    t = refresh(client, t)
    assert t["legs"]["stop"]["status"] == "NOT_PLACED"
    assert t["status"] == "needs_attention" and count(fake, "MARKET") == 0   # too late: a human


# ---------------------------------------------------------------- positions

def test_exit_never_sells_the_users_other_position(env):
    client, desk, _, fake, clock = env
    fake.manual["SBIN"] = 20                           # bought by hand in the Kite app
    t = protected(env)
    assert get_kite_store().get_trade(t["id"], "user_a")["baseline_qty"] == 20
    t = client.post(f"/api/kite/trades/{t['id']}/exit").json()
    assert int(fake.by_tag_suffix("X")["quantity"]) == 50
    fake.fill("X", 801.0)
    t = refresh(client, t)
    assert t["status"] == "closed"
    assert fake.bought() - fake.sold() + fake.manual["SBIN"] == 20    # the 20 are untouched


def test_exit_refused_when_kite_holds_less_than_the_trade(env):
    client, desk, _, fake, clock = env
    t = protected(env)
    fake.manual["SBIN"] = -30                          # 30 sold by hand outside the engine
    t = client.post(f"/api/kite/trades/{t['id']}/exit").json()
    assert "exit" not in t["legs"]                     # settling: re-checks, sells nothing
    clock["now"] = ist(10, 1, 0)
    t = refresh(client, t)
    assert t["status"] == "needs_attention" and "not selling" in t["attention"]
    assert count(fake, "MARKET") == 0


def test_close_waits_for_kite_position_to_agree(env):
    client, desk, _, fake, clock = env
    t = protected(env)
    fake.manual["SBIN"] = 10                           # Kite shows 10 more than the ledger
    get_kite_store().update_trade("user_a", dict(get_kite_store().get_trade(t["id"], "user_a"),
                                                 baseline_qty=0))
    fake.fill("S", 796.0)
    t = refresh(client, t)
    assert t["status"] == "exit_sent"                  # not closed on the ledger alone
    clock["now"] = ist(10, 1, 0)
    t = refresh(client, t)
    assert t["status"] == "needs_attention" and "still open" in t["attention"]


def test_entry_refused_when_positions_cannot_be_read(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    fake.positions_down = True
    r = _send(client, _ticket(desk))
    assert r.status_code == 409 and "positions" in r.text
    assert fake.placed() == []


def test_definitely_rejected_entry_marks_the_ticket_rejected(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    tk = _ticket(desk)
    fake.reject_next["LIMIT"] = "Insufficient funds"
    t = _send(client, tk).json()
    assert t["status"] == "entry_rejected"
    stored = get_desk_store().get_ticket(tk["id"], "user_a")
    assert stored["status"] == "rejected" and "Insufficient funds" in stored["rejected_reason"]


# ---------------------------------------------------------------- kill

def test_kill_records_itself_before_waiting_for_the_broker_lock(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    tk = _ticket(desk)
    lock = execution.user_lock("user_a")
    lock.acquire()                                     # a slow send / scan holds the lock
    done = threading.Event()
    th = threading.Thread(target=lambda: (client.post("/api/kite/kill"), done.set()))
    th.start()
    deadline = time.monotonic() + 2
    while not execution.control("user_a", clock["now"])["killed"]:
        assert time.monotonic() < deadline, "kill wasn't recorded while the lock was held"
        time.sleep(0.01)
    assert not done.is_set()                           # cancels still wait their turn
    lock.release()
    th.join(5)
    r = _send(client, tk)                              # the queued send now refuses
    assert r.status_code == 409 and "kill switch" in r.text and fake.placed() == []


def test_arming_cannot_undo_a_kill_that_lands_meanwhile(env, monkeypatch):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    real = execution.gate_reasons

    def kill_during_arm(*a, **k):                      # the kill lands between check and write
        out = real(*a, **k)
        execution._update_control("user_a", lambda d: d.update(killed_on="2026-10-28"))
        return out

    monkeypatch.setattr(execution, "gate_reasons", kill_during_arm)
    r = client.post("/api/kite/arm", json={"ack": "ARM 2026-10-28"})
    assert r.status_code == 409 and execution.control("user_a", clock["now"])["killed"]


def test_kill_flag_alone_makes_any_reconcile_exit(env):
    client, desk, _, fake, clock = env
    t = protected(env)
    execution._update_control("user_a", lambda d: d.update(killed_on="2026-10-28"))
    t = refresh(client, t)                             # e.g. another process wrote the flag
    assert t["exit_reason"] == "kill" and count(fake, "MARKET") == 1


# ---------------------------------------------------------------- resizes

def test_resize_is_not_repeated_and_hands_over_at_the_cap(env, monkeypatch):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    t = _send(client, _ticket(desk)).json()
    puts = lambda: sum(1 for c in fake.calls if c[0] == "PUT")  # noqa: E731
    fake.fill("E", 800.0, qty=10)
    clock["now"] = ist(10, 0, 10)
    t = refresh(client, t)
    t = refresh(client, t)
    assert puts() == 0 and int(fake.by_tag_suffix("S")["quantity"]) == 10
    fake.fill("E", 800.0, qty=10)
    t = refresh(client, t)
    t = refresh(client, t)                             # same size wanted: no second PUT
    assert puts() == 2                                 # stop + target once each
    monkeypatch.setattr(execution, "MAX_MODIFIES", 1)
    fake.fill("E", 800.0, qty=10)
    t = refresh(client, t)
    assert t["status"] == "needs_attention" and "resized" in t["attention"]
    assert puts() == 2


# ---------------------------------------------------------------- one process

def test_second_process_refuses_orders(env, tmp_path):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    path = get_kite_store().path + ".engine.lock"
    with execution._lease_guard:
        fh = execution._lease.pop(path, None)
    if fh:
        fh.close()                                     # this process lets go
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import fcntl,sys,time; f=open(sys.argv[1],'a+'); "
         "fcntl.flock(f.fileno(), fcntl.LOCK_EX); print('held', flush=True); time.sleep(30)",
         path], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        r = _send(client, _ticket(desk))
        assert r.status_code == 409 and "another server process" in r.text
        assert fake.placed() == [] and execution.reconcile_user(
            "user_a", clock["now"], execution.load_profile_safe("user_a")) == []
    finally:
        holder.kill()
        holder.wait()
        with execution._lease_guard:
            execution._lease.pop(path, None)


# ---------------------------------------------------------------- postbacks

def test_postbacks_run_off_the_event_loop_coalesced_per_hub():
    live.clear_for_tests()
    gate, calls = threading.Event(), []

    def blocking_broker(user_id, data):                # e.g. a reconcile stuck on Kite
        calls.append(data["n"])
        gate.wait(5)

    live.order_listeners.append(blocking_broker)
    hub = live.Hub("user_a", ["SBIN"])

    async def scenario():
        t0 = time.monotonic()
        for n in range(20):                            # a burst of postbacks
            hub.pump.submit({"tag": "Dabc", "n": n})
            await asyncio.sleep(0)
        await asyncio.sleep(0.05)                      # the loop is still responsive
        assert time.monotonic() - t0 < 1.0
        assert calls == [0]                            # one worker, the rest coalesced
        gate.set()
        for _ in range(200):
            if not hub.pump.busy:
                break
            await asyncio.sleep(0.01)

    asyncio.run(scenario())
    assert calls == [0, 19] and hub.pump.runs == 2     # latest postback, once
    live.clear_for_tests()
