"""Broker-safety regressions for Kite execution and live data, against the
fake Kite only (no network, no real orders). Each test is a race, timeout or
partial fill that once could leave a position unprotected or oversold; the
invariant everywhere: never more shares sold than bought, never a blind
re-send, never "rejected" without proof."""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from apps.api.desk import bars_source, entry as entry_mod
from apps.api.desk.profile import load_profile
from apps.api.kite import execution, live, strategies, ticker
from apps.api.kite.store import get_kite_store
from apps.api.routes import kite as kite_routes
from tests.kite_fake import FakeSocket, frame, quote_packet
from tests.test_kite_execution import (  # noqa: F401 — the shared fixture
    _armed, _breakout_provider, _breakout_rows, _send, _ticket, env, ist)

ORDERS = "/orders/regular"


def is_entry(d):
    return d.get("tag", "").endswith("E")


def is_stop(d):
    return d.get("order_type") == "SL-M"


def refresh(client, t):
    return client.post(f"/api/kite/trades/{t['id']}/refresh").json()


def protected(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    t = _send(client, _ticket(desk)).json()
    fake.fill("E", 800.0)
    clock["now"] = ist(10, 0, 20)
    t = refresh(client, t)
    assert t["status"] == "protected"
    return t


def never_short(fake):
    assert fake.sold() <= fake.bought()


# ---------------------------------------------------------------- B1 ambiguous entry

@pytest.mark.parametrize("kind", ["timeout", "5xx"])
def test_entry_kite_received_but_answer_lost_is_adopted_and_protected(env, kind):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    fake.faults.append(("POST", ORDERS, is_entry, kind))
    t = _send(client, _ticket(desk)).json()
    # adopted from the order book right away; never "rejected" on a lost answer
    assert t["status"] == "entry_sent" and t["legs"]["entry"]["order_id"]
    assert any("adopted entry" in e["what"] for e in t["events"])
    fake.fill("E", 800.0)
    clock["now"] = ist(10, 0, 30)
    t = refresh(client, t)
    assert t["status"] == "protected" and len(fake.placed()) == 3
    assert sum(is_entry(d) for d in fake.placed()) == 1          # one BUY, ever


def test_entry_never_received_is_tracked_all_session_and_never_resent(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    fake.faults.append(("POST", ORDERS, is_entry, "down"))
    t = _send(client, _ticket(desk)).json()
    # flagged at once, never "rejected" on a lost answer
    assert t["status"] == "needs_attention" and t["attention_kind"] == "ambiguous"
    assert t["legs"]["entry"]["status"] == "UNKNOWN"
    clock["now"] = ist(10, 1, 40)
    t = refresh(client, t)
    t = refresh(client, t)
    # absent for 100 s is NOT proof: still tracked, still flagged, nothing re-sent
    assert t["status"] == "needs_attention" and t["legs"]["entry"]["status"] == "ABSENT"
    clock["now"] = ist(15, 36)            # every DAY order has expired: now it is proof
    t = refresh(client, t)
    assert t["status"] == "entry_rejected"
    assert any("not placed" in e["what"] for e in t["events"])
    assert fake.placed() == [] and not fake.orders           # nothing was ever re-sent


# ---------------------------------------------------------------- B2 ambiguous protection

def test_stop_answer_lost_is_adopted_not_doubled_by_a_market_exit(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    t = _send(client, _ticket(desk)).json()
    fake.fill("E", 800.0)
    fake.faults.append(("POST", ORDERS, is_stop, "timeout"))
    clock["now"] = ist(10, 0, 20)
    t = refresh(client, t)
    assert t["status"] == "protected"
    assert not [o for o in fake.orders.values() if o["order_type"] == "MARKET"]
    assert sorted(o["order_type"] for o in fake.orders.values()) == ["LIMIT", "LIMIT", "SL-M"]
    assert all(int(o["quantity"]) == 50 for o in fake.orders.values())
    fake.fill("S", 796.0)
    t = refresh(client, t)
    assert t["status"] == "closed" and t["exit_reason"] == "stop"
    never_short(fake)


def test_stop_never_acknowledged_flags_unprotected_at_once_and_never_market_exits(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    t = _send(client, _ticket(desk)).json()
    fake.fill("E", 800.0)
    fake.faults.append(("POST", ORDERS, is_stop, "down"))
    clock["now"] = ist(10, 0, 20)
    t = refresh(client, t)
    # immediately actionable: the user is told the position may be unprotected
    assert t["status"] == "needs_attention" and "UNPROTECTED" in t["attention"]
    assert t["legs"]["stop"]["status"] == "UNKNOWN" and "target" not in t["legs"]
    clock["now"] = ist(10, 2, 0)
    t = refresh(client, t)
    t = refresh(client, t)
    # 100 s of absence: no speculative market exit, no second stop
    assert t["legs"]["stop"]["status"] == "ABSENT" and fake.sell_exposure() == 0
    assert t["status"] == "needs_attention"
    never_short(fake)


# ---------------------------------------------------------------- B3 serialisation

def test_concurrent_reconciles_protect_once(env, monkeypatch):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    t = _send(client, _ticket(desk)).json()
    fake.fill("E", 800.0)
    clock["now"] = ist(10, 0, 20)

    def slow(method, url, params, data, headers):        # widen every race window
        if method == "GET" and url.endswith("/orders"):
            time.sleep(0.05)
        return fake(method, url, params, data, headers)

    prof = load_profile("user_a")
    threads = [threading.Thread(target=execution.reconcile_user,
                                args=("user_a", clock["now"], prof, slow)) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sum(o["order_type"] == "SL-M" for o in fake.orders.values()) == 1
    assert sum(o["order_type"] == "LIMIT" and o["transaction_type"] == "SELL"
               for o in fake.orders.values()) == 1


def test_double_submit_of_one_ticket_sends_one_buy(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    tk = _ticket(desk)

    def slow(method, url, params, data, headers):
        if method == "POST" and url.endswith(ORDERS):
            time.sleep(0.1)
        return fake(method, url, params, data, headers)

    kite_routes._transport = slow
    try:
        codes = []
        threads = [threading.Thread(target=lambda: codes.append(_send(client, tk).status_code))
                   for _ in range(3)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
    finally:
        kite_routes._transport = fake
    assert sorted(codes) == [201, 409, 409]
    assert sum(is_entry(d) for d in fake.placed()) == 1


# ---------------------------------------------------------------- B4 exit vs filled legs

def test_flat_by_after_the_stop_already_filled_sends_no_exit(env):
    client, _, _, fake, clock = env
    t = protected(env)
    stop_id = fake.by_tag_suffix("S")["order_id"]

    def racing(method, url, params, data, headers):      # the stop fills as we cancel it
        if method == "DELETE" and url.endswith(stop_id):
            fake.fill("S", 796.0)
        return fake(method, url, params, data, headers)

    kite_routes._transport = racing
    try:
        clock["now"] = ist(15, 6)
        t = refresh(client, t)
    finally:
        kite_routes._transport = fake
    # the stop's fill flattened the position: no market exit on top of it
    assert t["status"] == "closed" and t["exit_avg"] == 796.0 and t["exit_reason"] == "flat_by"
    assert not [o for o in fake.orders.values() if o["order_type"] == "MARKET"]
    assert fake.by_tag_suffix("T")["status"] == "CANCELLED"
    never_short(fake)


def test_exit_waits_for_cancel_acknowledgements(env):
    client, _, _, fake, clock = env
    t = protected(env)
    fake.fail_cancel = True
    clock["now"] = ist(15, 6)
    t = refresh(client, t)
    assert t["status"] == "exit_sent" and "waiting for Kite to confirm" in t["events"][-1]["what"]
    assert not [o for o in fake.orders.values() if o["order_type"] == "MARKET"]
    fake.fail_cancel = False
    clock["now"] = ist(15, 6, 30)
    t = refresh(client, t)
    assert fake.by_tag_suffix("X")["quantity"] == 50 and fake.sell_exposure() == 50
    fake.fill("X", 801.0)
    assert refresh(client, t)["status"] == "closed"
    never_short(fake)


def test_kill_with_kite_down_keeps_exits_pending_then_finishes(env):
    client, _, _, fake, clock = env
    t = protected(env)

    def down(*a, **k):
        raise ConnectionError("no route to host")

    kite_routes._transport = down
    try:
        out = client.post("/api/kite/kill").json()
    finally:
        kite_routes._transport = fake
    assert out["killed"] and out["summary"]["error"] and out["summary"]["exits_sent"] == 0
    stored = get_kite_store().get_trade(t["id"], "user_a")
    assert stored["exit_reason"] == "kill" and stored["status"] != "needs_attention"
    t = refresh(client, t)                             # Kite is back: the loop finishes it
    assert t["status"] == "exit_sent" and fake.by_tag_suffix("X")["quantity"] == 50
    never_short(fake)


# ---------------------------------------------------------------- B5 partial fills

def test_partial_target_fill_resizes_the_stop(env):
    client, _, _, fake, clock = env
    t = protected(env)
    fake.fill("T", 808.0, qty=20)
    t = refresh(client, t)
    assert t["held_qty"] == 30 and fake.by_tag_suffix("S")["quantity"] == 30
    fake.fill("S", 796.0)
    t = refresh(client, t)
    assert t["status"] == "closed" and fake.sold() == 50
    assert t["exit_avg"] == round((20 * 808.0 + 30 * 796.0) / 50, 2)
    assert t["pnl_gross"] == round(20 * 8.0 + 30 * -4.0, 2)
    never_short(fake)


def test_partial_entry_is_protected_at_once_and_grows_with_fills(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    t = _send(client, _ticket(desk)).json()
    fake.fill("E", 800.0, qty=20)
    clock["now"] = ist(10, 0, 10)
    t = refresh(client, t)
    assert t["held_qty"] == 20 and fake.by_tag_suffix("S")["quantity"] == 20
    assert fake.by_tag_suffix("T")["quantity"] == 20
    fake.fill("E", 800.0)
    t = refresh(client, t)
    assert t["status"] == "protected" and fake.by_tag_suffix("S")["quantity"] == 50


def test_partial_entry_cancelled_at_timeout_keeps_protecting_the_fill(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    t = _send(client, _ticket(desk)).json()
    fake.fill("E", 800.0, qty=10)
    clock["now"] = ist(10, 1, 30)
    t = refresh(client, t)
    assert fake.by_tag_suffix("E")["status"] == "CANCELLED"
    assert t["status"] == "protected" and fake.by_tag_suffix("S")["quantity"] == 10


def test_simultaneous_fills_beyond_the_position_are_flagged_not_traded(env):
    client, _, _, fake, clock = env
    t = protected(env)
    fake.fill("T", 808.0, qty=20)
    fake.fill("S", 796.0)                    # both filled before the engine could resize
    n = len(fake.placed())
    t = refresh(client, t)
    assert t["status"] == "needs_attention" and "short 20" in t["attention"]
    assert len(fake.placed()) == n           # no further orders on uncertain ground
    assert client.post(f"/api/kite/trades/{t['id']}/exit").status_code == 409


# ---------------------------------------------------------------- gates

def test_owner_without_bound_client_id_is_refused_before_any_order(env, monkeypatch):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    monkeypatch.setenv("AUTOTRADE_USER_IDS", "user_a")
    n = len(fake.calls)
    r = _send(client, _ticket(desk))
    assert r.status_code == 409 and any("isn't bound" in x for x in r.json()["detail"]["reasons"])
    monkeypatch.setenv("AUTOTRADE_USER_IDS", "user_a:ZZ9999")
    r = _send(client, _ticket(desk))
    assert any("bound to ZZ9999" in x for x in r.json()["detail"]["reasons"])
    assert len(fake.calls) == n and fake.placed() == []      # not one Kite request


def test_kill_during_a_running_strategy_scan_wins(env):
    client, desk, _, _, clock = env
    bars_source.register_live_provider(_breakout_provider(_breakout_rows()))
    clock["now"] = ist(9, 36)
    s = client.post("/api/kite/strategies", json={"name": "orb", "symbols": ["SBIN"],
                                                  "mode": "paper"}).json()
    stale = get_kite_store().get_strategy(s["id"], "user_a")   # the sweep loaded this…
    client.post("/api/kite/kill")                                # …then the kill landed
    assert strategies.scan("user_a", stale, clock["now"]) == []
    assert get_kite_store().get_strategy(s["id"], "user_a")["mode"] == "off"
    assert desk.get("/api/desk/paper").json()["trades"] == []
    r = client.put(f"/api/kite/strategies/{s['id']}", json=dict(
        {k: s[k] for k in ("name", "symbols")}, mode="paper"))
    assert r.status_code == 409


# ---------------------------------------------------------------- live data

def _hub_env(env):
    client, _, _, fake, clock = env
    clock["now"] = ist(9, 5)
    from tests.test_kite_execution import _login
    _login(client, fake)
    return fake


def test_mid_session_hub_backfills_the_session_before_serving_live(env, monkeypatch):
    fake = _hub_env(env)
    fake.candles = [[f"2026-10-28T09:{15 + i}:00+0530", 800 + i, 801 + i, 799 + i, 800.5 + i,
                     1000] for i in range(45)]          # 09:15 … 09:59 from Kite history
    now = ist(10, 0, 1)
    monkeypatch.setattr(live, "_now", lambda: now)

    async def scenario():
        sock = FakeSocket([frame(quote_packet(779521, 84600, 50000))])
        hub = await live.start("user_a", ["SBIN"], transport=fake, now=now,
                               connect=lambda url: _ret(sock))
        hub.builder.on_tick("SBIN", 846.0, 50000, now)
        hub.state = "live"
        # ticking, but the session before 10:00 isn't filled yet: not served as live
        assert "SBIN" not in hub.backfilled
        assert live.provider("user_a", "SBIN", "5m", "session", now=now) is None
        assert live.freshness("user_a", now)["symbols"][0]["source"] == "delayed:yahoo"
        await hub.backfill_task
        hub.state = "live"    # the ticker's own clock is wall time; pin the state for the check
        got = live.provider("user_a", "SBIN", "5m", "session", now=now)
        assert got.source == "live:kite" and got.rows[0][0] == ist(9, 15)
        ev = entry_mod.evaluate(got.rows, ist(10, 0, 5), symbol="SBIN", setup="orb",
                                or_minutes=15)
        assert ev["opening_range"]["complete"] and ev["opening_range"]["bars"] == 3
        hist = [c for c in fake.calls if c[1].startswith("/instruments/historical/")]
        assert hist[0][2]["from"].endswith("09:15:00") and hist[0][1].endswith("/minute")
        assert live.provider("user_b", "SBIN", "5m", "session", now=now) is None
        await live.stop("user_a")

    asyncio.run(scenario())


def test_backfill_failure_keeps_the_symbol_on_delayed_and_says_why(env, monkeypatch):
    fake = _hub_env(env)
    now = ist(10, 0, 1)
    monkeypatch.setattr(live, "_now", lambda: now)
    monkeypatch.setattr(live, "BACKFILL_RETRY_S", (0, 0))   # bounded: 3 tries in all
    real = fake.__call__
    tries = []

    def no_history(method, url, params, data, headers):
        if "/instruments/historical/" in url:
            tries.append(url)
            return 429, '{"status":"error","message":"Too many requests","error_type":"NetworkException"}'
        return real(method, url, params, data, headers)

    async def scenario():
        hub = await live.start("user_a", ["SBIN"], transport=no_history, now=now,
                               connect=lambda url: _ret(FakeSocket([])))
        await hub.backfill_task
        hub.builder.on_tick("SBIN", 846.0, 50000, now)
        hub.state = "live"
        assert live.provider("user_a", "SBIN", "1m", "session", now=now) is None
        f = live.freshness("user_a", now)["symbols"][0]
        assert f["source"] == "delayed:yahoo" and "Too many requests" in f["backfill_error"]
        await live.stop("user_a")

    asyncio.run(scenario())
    assert len(tries) == 3                                   # retried, then gave up


def test_backfill_retries_a_transient_failure_then_serves_live(env, monkeypatch):
    fake = _hub_env(env)
    now = ist(10, 0, 1)
    monkeypatch.setattr(live, "_now", lambda: now)
    monkeypatch.setattr(live, "BACKFILL_RETRY_S", (0, 0, 0))
    real = fake.__call__
    tries = []

    def flaky_history(method, url, params, data, headers):
        if "/instruments/historical/" in url:
            tries.append(url)
            if len(tries) == 1:
                return 429, '{"status":"error","message":"Too many requests","error_type":"NetworkException"}'
        return real(method, url, params, data, headers)

    async def scenario():
        hub = await live.start("user_a", ["SBIN"], transport=flaky_history, now=now,
                               connect=lambda url: _ret(FakeSocket([])))
        await hub.backfill_task
        assert "SBIN" in hub.backfilled and not hub.backfill_errors
        await live.stop("user_a")

    asyncio.run(scenario())
    assert len(tries) == 2


async def _ret(x):
    return x


def test_half_open_socket_times_out_and_reconnects():
    states, connects = [], []

    async def go():
        async def connect(url):
            connects.append(url)
            return FakeSocket([], hold=True)     # no frames, no heartbeats, never closes
        task = asyncio.create_task(ticker.run_ticker(
            api_key="k", access_token="t", tokens={1: "SBIN"}, on_tick=lambda *a: None,
            on_text=lambda m: None, on_state=lambda s, d: states.append((s, d)),
            stop_at=ist(15, 30), connect=connect, clock=lambda: ist(10, 0), backoff=(0,),
            read_timeout=0.05))
        await asyncio.sleep(0.3)
        task.cancel()
    asyncio.run(go())
    assert len(connects) >= 2
    assert any(s == "reconnecting" and "heartbeat" in (d or "") for s, d in states)


def test_reconnect_refills_the_gap_before_serving_live_again(env, monkeypatch):
    fake = _hub_env(env)
    now = ist(10, 0, 1)
    monkeypatch.setattr(live, "_now", lambda: now)

    async def scenario():
        hub = await live.start("user_a", ["SBIN"], transport=fake, now=now,
                               connect=lambda url: _ret(FakeSocket([], hold=True)))
        await hub.backfill_task
        assert hub.backfilled == {"SBIN"}
        # the ticker reports a drop and a reconnect: the gap must be refilled first
        hub.on_state("reconnecting", "stream dropped")
        hub.on_state("live", None)
        assert hub.backfilled == set()
        await hub.backfill_task
        assert hub.backfilled == {"SBIN"}
        await live.stop("user_a")

    asyncio.run(scenario())
