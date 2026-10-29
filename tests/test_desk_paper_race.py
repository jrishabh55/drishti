"""Paper confirmation races (double confirm, paper vs live, cancel, shared
cash) and 1-minute paper trading end to end. Fake Kite only; no network."""
from __future__ import annotations

import sqlite3
import threading
from datetime import date

import pytest

from apps.api.desk import bars_source, paper
from apps.api.desk.bars import session_bars
from apps.api.desk.market.session import NSE
from apps.api.desk.store import get_desk_store
from apps.api.routes import desk as desk_routes
from tests.test_kite_execution import _armed, env, ist  # noqa: F401

DAY = date(2026, 10, 28)
SPEC1 = bars_source.spec_for("1m")


def _ticket(desk, interval="5m", entry=800.0, stop=796.0, entry_time="09:40"):
    plan = desk.post("/api/desk/plans", json={
        "symbol": "SBIN", "entry": entry, "entry_time": entry_time, "setup": "level",
        "stop_rule": "price", "stop_param": stop, "target_param": 2,
        "interval": interval}).json()
    assert plan.get("interval") == interval, plan
    tk = desk.post("/api/desk/tickets", json={"plan_id": plan["id"]}).json()
    assert tk["status"] == "ready", tk["verdict"]["reasons"]
    return tk


def _race(*calls):
    out = [None] * len(calls)
    gate = threading.Barrier(len(calls))

    def run(i, fn):
        gate.wait()
        out[i] = fn()
    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(calls)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    return out


def _confirm(desk, tk, mode="paper"):
    return lambda: desk.post(f"/api/desk/tickets/{tk['id']}/confirm", json={"mode": mode})


# ---------------------------------------------------------------- races

def test_concurrent_double_paper_confirm_creates_one_trade(env):
    _, desk, _, _, clock = env
    clock["now"] = ist(10, 0)
    tk = _ticket(desk)
    codes = sorted(r.status_code for r in _race(*[_confirm(desk, tk)] * 4))
    assert codes == [200, 409, 409, 409]
    trades = get_desk_store().list_paper("user_a")
    assert len(trades) == 1 and trades[0]["ticket_id"] == tk["id"]
    assert get_desk_store().get_ticket(tk["id"], "user_a")["paper_trade_id"] == trades[0]["id"]


def test_paper_and_live_confirm_of_one_ticket_cannot_both_win(env):
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    tk = _ticket(desk)
    words = f"BUY {tk['order']['qty']} SBIN"
    paper_r, live_r = _race(_confirm(desk, tk), lambda: client.post(
        "/api/kite/trades", json={"ticket_id": tk["id"], "confirm_text": words}))
    assert sorted([paper_r.status_code, live_r.status_code]) in ([200, 409], [201, 409])
    papers = len(get_desk_store().list_paper("user_a"))
    buys = sum(d.get("transaction_type") == "BUY" for d in fake.placed())
    assert papers + buys == 1
    status = get_desk_store().get_ticket(tk["id"], "user_a")["status"]
    assert status == ("paper" if papers else "sent")


def test_cancel_racing_confirm_never_leaves_a_cancelled_ticket_with_a_trade(env):
    _, desk, _, _, clock = env
    clock["now"] = ist(10, 0)
    desk.put("/api/desk/profile", json={"max_loss_per_trade_inr": 200, "flat_by": "15:05",
                                        "max_trades_per_day": 10})
    tickets = [_ticket(desk) for _ in range(3)]     # all ready before any confirm
    for tk in tickets:
        _race(_confirm(desk, tk), lambda: desk.post(f"/api/desk/tickets/{tk['id']}/cancel"))
        final = get_desk_store().get_ticket(tk["id"], "user_a")["status"]
        mine = [t for t in get_desk_store().list_paper("user_a") if t["ticket_id"] == tk["id"]]
        # (a later ticket can also be blocked on re-check: duplicate buy / position cap)
        assert (final, len(mine)) in (("cancelled", 0), ("paper", 1), ("blocked", 0))
        if final == "blocked":
            continue
        # a confirmed ticket can't be cancelled back to life, nor a cancelled one confirmed
        again = desk.post(f"/api/desk/tickets/{tk['id']}/"
                          + ("cancel" if final == "paper" else "confirm"), json={"mode": "paper"})
        assert again.status_code == 409
        assert get_desk_store().get_ticket(tk["id"], "user_a")["status"] == final


def test_distinct_tickets_cannot_overspend_paper_cash_concurrently(env):
    _, desk, _, _, clock = env
    clock["now"] = ist(10, 0)
    desk.put("/api/desk/profile", json={"max_loss_per_trade_inr": 600, "flat_by": "15:05"})
    tks = [_ticket(desk, stop=794.0) for _ in range(3)]      # each ≈₹50k of ₹1,00,000
    assert all(t["order"]["qty"] * 800 > 45_000 for t in tks)
    results = _race(*[_confirm(desk, t) for t in tks])
    ok = [r for r in results if r.status_code == 200]
    assert len(ok) == 1
    for r in results:
        if r.status_code != 200:   # the re-check saw the winner's cash already committed
            assert any(x.startswith("cash:") for x in r.json()["detail"]["reasons"]), r.text
    assert len(get_desk_store().list_paper("user_a")) == 1


def test_confirm_paper_is_atomic(env):
    _, desk, _, _, clock = env
    clock["now"] = ist(10, 0)
    tk = _ticket(desk)
    store = get_desk_store()
    bad = paper.new_trade(tk["order"], ticket_id=tk["id"], placed_at=clock["now"],
                          profile=desk_routes.load_profile("user_a"))
    with pytest.raises(sqlite3.IntegrityError):           # crash mid-transaction
        store.confirm_paper("user_a", tk["id"], tk, dict(bad, trade_date=None))
    assert store.get_ticket(tk["id"], "user_a")["status"] == "ready"
    assert store.list_paper("user_a") == []
    store.transition_ticket(tk["id"], "user_a", ("ready",), dict(tk, status="cancelled"))
    assert store.confirm_paper("user_a", tk["id"], tk, bad) is None
    assert store.list_paper("user_a") == []


# ---------------------------------------------------------------- 1-minute paper

def _minute_rows():
    """A real-shaped 1m session: 801 until 09:30, the 09:31 bar opens at the
    800 limit, 09:33 trades through the 796 stop."""
    n = 30
    closes = [801.0] * 16 + [800.5, 800.2, 797.0, 798.0] + [798.0] * (n - 20)
    opens = [801.0] * 16 + [800.0, 800.5, 800.2, 797.0] + [798.0] * (n - 20)
    lows = [800.6] * 16 + [799.9, 800.0, 795.5, 796.5] + [797.5] * (n - 20)
    highs = [801.2] * 16 + [800.6, 800.6, 800.3, 798.2] + [798.5] * (n - 20)
    from tests.desk_utils import session_rows

    return session_rows(DAY, closes, spec=SPEC1, highs=highs, lows=lows, opens=opens)


def _five_from(rows1):
    out = {}
    for ts, o, h, lo, c, v in rows1:
        k = (ts.hour * 60 + ts.minute - NSE.open_min) // 5
        start = NSE.local(DAY, NSE.open_min + 5 * k)
        b = out.get(start)
        out[start] = [o, h, lo, c, v] if b is None else [b[0], max(b[1], h), min(b[2], lo), c,
                                                          b[4] + v]
    return [(ts, *v) for ts, v in sorted(out.items())]


def _provider(rows1):
    def provider(user_id, symbol, interval, kind):
        if kind == "session" and user_id == "user_a" and symbol == "SBIN":
            rows = rows1 if interval == "1m" else _five_from(rows1)
            return bars_source.BarSeries(symbol, interval, rows, "live:kite",
                                         rows[-1][0].isoformat())
        return None
    return provider


def test_one_minute_plan_fills_and_stops_on_actual_minute_bars(env):
    _, desk, _, _, clock = env
    rows1 = _minute_rows()
    bars_source.register_live_provider(_provider(rows1))
    clock["now"] = ist(9, 30, 30)
    tk1 = _ticket(desk, interval="1m", entry_time="09:31")
    assert tk1["order"]["plan"]["interval"] == "1m"
    assert desk.post(f"/api/desk/tickets/{tk1['id']}/confirm", json={"mode": "paper"}).status_code == 200
    clock["now"] = ist(9, 45)
    t = desk.get("/api/desk/paper").json()["trades"][0]
    assert t["interval"] == "1m"
    assert t["entry_fill"] == {"time": "09:31", "price": 800.0}
    assert t["status"] == "closed" and t["exit_fill"]["reason"] == "stop"
    assert t["exit_fill"]["time"] == "09:33" and t["exit_fill"]["price"] == 796.0


def test_five_minute_and_legacy_trades_keep_five_minute_fills(env):
    _, desk, _, _, clock = env
    rows1 = _minute_rows()
    bars_source.register_live_provider(_provider(rows1))
    clock["now"] = ist(9, 30, 30)
    tk = _ticket(desk, interval="5m", entry_time="09:35")
    desk.post(f"/api/desk/tickets/{tk['id']}/confirm", json={"mode": "paper"})
    # a ledger row written before trades carried an interval
    store = get_desk_store()
    legacy = store.list_paper("user_a")[0]
    legacy.pop("interval")
    store.update_paper(legacy["id"], "user_a", legacy)
    clock["now"] = ist(9, 45)
    t = desk.get("/api/desk/paper").json()["trades"][0]
    assert "interval" not in t or t["interval"] == "5m"
    assert t["entry_fill"]["time"] == "09:35"        # the bar starting after 09:30:30
    # the same minutes seen as 5m bars: no fill at 09:31, no exit at 09:33
    assert t["exit_fill"] is None or t["exit_fill"]["time"] != "09:33"


def test_advance_refuses_bars_of_another_interval():
    trade = {"interval": "1m", "status": "pending"}
    with pytest.raises(ValueError):
        paper.advance(trade, [], ist(10, 0), None, spec=NSE)
    bars, _ = session_bars(_minute_rows(), SPEC1, ist(9, 45))
    assert bars and bars[16]["start"] == "09:31"


def test_manual_close_cannot_be_overwritten_by_background_refresh(env, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
    _, desk, _, _, clock = env
    clock['now'] = ist(10, 0)
    tk = _ticket(desk)
    store = get_desk_store()
    profile = desk_routes.load_profile('user_a')
    trade = paper.new_trade(tk['order'], ticket_id=tk['id'], placed_at=clock['now'], profile=profile)
    trade.update(status='open', entry_fill={'time':'09:45', 'price':800.0})
    trade = store.create_paper('user_a', trade)
    entered, release, closing = threading.Event(), threading.Event(), threading.Event()
    def advance(t, *args, **kwargs):
        entered.set()
        assert release.wait(3)
        return dict(t, refreshed=True)
    monkeypatch.setattr(paper, 'advance', advance)
    monkeypatch.setattr(bars_source, 'get_session_rows', lambda *a, **k:
                        bars_source.BarSeries('SBIN', '5m', [], 'delayed:yahoo', None))
    def close():
        closing.set()
        return desk_routes.close_paper(trade['id'], desk_routes.CloseBody(price=802), 'user_a')
    with ThreadPoolExecutor(max_workers=2) as pool:
        refresh = pool.submit(desk_routes.refresh_paper, 'user_a')
        assert entered.wait(1)
        manual = pool.submit(close)
        assert closing.wait(1)
        try:
            manual.result(timeout=.15)
        except FutureTimeout:
            pass  # correctly waits for refresh's write lock
        finally:
            release.set()
        refresh.result(timeout=2)
        assert manual.result(timeout=2)['status'] == 'closed'
    final = store.get_paper(trade['id'], 'user_a')
    assert final['status'] == 'closed' and final['exit_fill']['price'] == 802


def test_missed_one_minute_eod_recovers_the_trade_day_from_deep_history(env, monkeypatch):
    from datetime import timedelta
    from tests.desk_utils import session_rows
    _, desk, _, _, clock = env
    clock['now'] = ist(9, 30, 30)
    tk = _ticket(desk, interval='1m', entry_time='09:31')
    desk.post(f"/api/desk/tickets/{tk['id']}/confirm", json={'mode':'paper'})
    rows = _minute_rows() + session_rows(DAY, [798.0] * 375, spec=SPEC1)[30:]
    calls = []
    def history(uid, sym, interval, **kw):
        calls.append((sym, interval))
        return bars_source.BarSeries(sym, interval, rows, 'delayed:yahoo', rows[-1][0].isoformat())
    monkeypatch.setattr(bars_source, 'get_history_rows', history)
    monkeypatch.setattr(bars_source, 'get_session_rows', lambda uid,sym,interval:
                        bars_source.BarSeries(sym,interval,[], 'delayed:yahoo',None))
    clock['now'] = ist(10, 0) + timedelta(days=1)
    out = desk_routes.refresh_paper('user_a')[0]
    assert calls == [('SBIN','1m')]
    assert out['status'] == 'closed' and out['exit_fill']['time'] == '09:33'
    assert out['exit_fill']['price'] == 796


def test_background_sweep_includes_pending_confirmed_paper_trades(env):
    _, desk, _, _, clock = env
    clock['now'] = ist(10, 0)
    tk = _ticket(desk)
    desk.post(f"/api/desk/tickets/{tk['id']}/confirm", json={'mode':'paper'})
    active = get_desk_store().open_paper_all_users()
    assert len(active) == 1 and active[0]['status'] == 'pending'


def test_missed_eod_without_history_stays_unresolved_and_explains_why(env, monkeypatch):
    from datetime import timedelta
    from apps.api.desk.bars import BarsError
    _, desk, _, _, clock = env
    clock['now'] = ist(10, 0)
    tk = _ticket(desk)
    desk.post(f"/api/desk/tickets/{tk['id']}/confirm", json={'mode':'paper'})
    clock['now'] += timedelta(days=1)
    def unavailable(*a, **kw):
        raise BarsError('source unavailable')
    monkeypatch.setattr(bars_source, 'get_history_rows', unavailable)
    out = desk_routes.refresh_paper('user_a')[0]
    assert out['status'] == 'pending' and out['entry_fill'] is None and out['exit_fill'] is None
    assert 'unavailable' in out['data_warning'] and 'manual close' in out['data_warning']
