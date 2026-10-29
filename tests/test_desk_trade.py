"""Sizing, risk checks, paper fills and the ticket lifecycle."""
from __future__ import annotations

from datetime import date, timezone

import pandas as pd
import pytest

from apps.api.desk import feeds, paper, risk, sizing
from apps.api.desk.bars import session_bars
from apps.api.desk.market.session import NSE
from apps.api.desk.profile import DeskProfile
from apps.api.desk.store import reset_desk_store_for_tests
from apps.api.routes import desk as desk_routes
from tests.desk_utils import desk_client, session_rows
from tests.scanner_utils import make_store

DAY = date(2026, 10, 28)


def ist(h, m, d=DAY):
    return NSE.local(d, h * 60 + m).astimezone(timezone.utc)


PLAN = {"symbol": "SBIN", "side": "buy", "date": "2026-10-28", "setup": "orb",
        "entry": 800.0, "stop": 796.0, "target": 808.0, "entry_time": "09:40",
        "flat_by": "15:15"}


# ---------------------------------------------------------------- sizing

def test_size_binds_on_max_loss_and_reports_charges():
    s = sizing.size_trade(PLAN, DeskProfile())
    assert s["qty"] == 62 and s["bound_by"] == "position cap"     # 50% of 1L / 800 = 62
    prof = DeskProfile(max_loss_per_trade_inr=100)
    s = sizing.size_trade(PLAN, prof)
    assert s["qty"] == 25 and s["bound_by"] == "max loss" and s["max_loss"] == 100.0
    assert s["charges_at_stop"] > 0 and s["max_loss_net"] > s["max_loss"]
    assert s["reward_risk"] == 2.0 and s["reward_risk_net"] < 2.0
    o = sizing.order_from(s, plan_id="p1")
    assert o["qty"] == 25 and o["price"] == 800.0 and o["product"] == "MIS"


def test_size_refusals():
    s = sizing.size_trade(PLAN, DeskProfile(max_loss_per_trade_inr=3, max_daily_loss_inr=3))
    assert s["qty"] == 0 and "more than your ₹3 max loss" in s["refusal"]
    assert sizing.order_from(s) is None
    tight = dict(PLAN, target=800.6)   # 0.15R gross: charges push it lower
    s = sizing.size_trade(tight, DeskProfile())
    assert s["refusal"] and "after charges" in s["refusal"]
    with pytest.raises(sizing.SizeError, match="blocked"):
        sizing.size_trade(PLAN, DeskProfile(blocked=["sbin"]))
    with pytest.raises(sizing.SizeError, match="intraday plan is for its own day"):
        sizing.size_trade(PLAN, DeskProfile(), today="2026-10-29")


def test_charges_heavy_note_on_tiny_risk():
    p = dict(PLAN, entry=100.0, stop=99.9, target=100.5)
    s = sizing.size_trade(p, DeskProfile(max_loss_per_trade_inr=10, min_reward_risk=0))
    assert s["notes"] and "Charges" in s["notes"][0]


# ---------------------------------------------------------------- risk

def order(**kw):
    s = sizing.size_trade(PLAN, DeskProfile(max_loss_per_trade_inr=200))
    o = sizing.order_from(s, plan_id="p1")
    o.update(kw)
    return o


def test_risk_passes_a_good_order_in_the_window():
    res = risk.check([order()], DeskProfile(max_loss_per_trade_inr=200), now=ist(10, 0))
    r = res["results"][0]
    assert res["pass"] == 1 and r["verdict"] == "PASS" and r["kind"] == "entry"
    assert r["max_loss"] == 200.0 and r["reward_risk"] == 2.0 and r["reward_risk_net"] < 2
    assert "skipped" in res["warnings"][0]          # no NSE feeds -> said, not silent


@pytest.mark.parametrize("now,kw,prof,needle", [
    (ist(9, 20), {}, {}, "window: no new entries before 09:30"),
    (ist(14, 50), {}, {}, "window: no new entries after 14:45"),
    (ist(10, 0, date(2026, 10, 2)), {}, {}, "NSE is closed"),
    (ist(10, 0), {"qty": 1.5}, {}, "whole number"),
    (ist(10, 0), {"stop": None, "plan": {}}, {}, "stop: no stop"),
    (ist(10, 0), {}, {"max_loss_per_trade_inr": 100}, "loss: 50 shares"),
    (ist(10, 0), {}, {"max_daily_loss_inr": 250, "max_loss_per_trade_inr": 200,
                      "min_reward_risk": 1}, None),
    (ist(10, 0), {"symbol": "NIFTY26OCTFUT"}, {}, "asset"),
    (ist(10, 0), {"series": "BE"}, {}, "trade-for-trade"),
    (ist(10, 0), {"target": 800.5}, {}, "reward:risk"),
    (ist(10, 0), {}, {"blocked": ["SBIN"]}, "blocked"),
    (ist(10, 0), {}, {"max_order_inr": 10000, "min_order_inr": 0}, "size: ₹40,000.00 is over"),
])
def test_risk_blocks(now, kw, prof, needle):
    base = {"max_loss_per_trade_inr": 200}
    base.update(prof)
    res = risk.check([order(**kw)], DeskProfile(**base), now=now)
    r = res["results"][0]
    if needle is None:
        assert r["verdict"] == "PASS"
    else:
        assert r["verdict"] == "BLOCK" and any(needle in x for x in r["reasons"]), r["reasons"]


def test_risk_batch_daily_loss_trades_and_duplicates():
    prof = DeskProfile(max_loss_per_trade_inr=200, max_daily_loss_inr=300, max_trades_per_day=5,
                       max_position_pct=100, max_order_inr=100000)
    a = order()
    b = order(symbol="INFY")
    res = risk.check([a, b], prof, now=ist(10, 0))
    assert [r["verdict"] for r in res["results"]] == ["PASS", "BLOCK"]
    assert "daily loss" in res["results"][1]["reasons"][0]
    res = risk.check([order()], prof, now=ist(10, 0),
                     today_entries=[{"symbol": "SBIN", "side": "buy", "max_loss": 50}])
    assert any("already placed today" in x for x in res["results"][0]["reasons"])


def test_risk_never_blocks_an_exit():
    sell = {"symbol": "SBIN", "side": "sell", "qty": 10, "order_type": "LIMIT", "price": 790,
            "product": "MIS", "exchange": "NSE"}
    res = risk.check([sell], DeskProfile(blocked=["SBIN"]), now=ist(15, 0),
                     holdings={"SBIN": 10}, holding_values={"SBIN": 8000})
    r = res["results"][0]
    assert r["kind"] == "exit" and r["verdict"] == "PASS"
    assert any("exit, not blocked" in w for w in r["warnings"])
    short = dict(sell, qty=20)
    assert risk.check([short], DeskProfile(), now=ist(10, 0),
                      holdings={"SBIN": 10})["results"][0]["verdict"] == "BLOCK"


def test_risk_nse_facts_circuit_and_surveillance():
    prof = DeskProfile(max_loss_per_trade_inr=200)
    m = {"SBIN": {"band_upper": 805.0, "band_pct": 5}}
    r = risk.check([order()], prof, now=ist(10, 0), market=m)["results"][0]
    assert r["verdict"] == "BLOCK" and any("upper circuit" in x for x in r["reasons"])
    assert any("5% price band" in w for w in r["warnings"])
    r = risk.check([order()], prof, now=ist(10, 0),
                   market={"SBIN": {"asm": "LT-ASM Stage 1"}})["results"][0]
    assert any("surveillance" in x for x in r["reasons"])
    r = risk.check([order()], prof, now=ist(10, 0),
                   market={"SBIN": {"fo_ban": True, "results_date": "2026-10-30"}})["results"][0]
    assert r["verdict"] == "PASS" and len(r["warnings"]) == 2


def test_fingerprint_changes_with_the_order():
    assert risk.fingerprint(order()) == risk.fingerprint(order())
    assert risk.fingerprint(order()) != risk.fingerprint(order(qty=49))


# ---------------------------------------------------------------- paper

def bars_of(closes, highs=None, lows=None, opens=None):
    rows = session_rows(DAY, closes, highs=highs, lows=lows, opens=opens)
    return session_bars(rows, NSE, ist(15, 35))[0]


def trade(order_type="LIMIT", placed=(9, 41)):
    o = order(order_type=order_type)
    if order_type == "MARKET":
        o.pop("price")
    return paper.new_trade(o, ticket_id="t1", placed_at=ist(*placed), profile=DeskProfile())


def test_paper_limit_fill_then_target():
    closes = [800.0] * 75
    highs = [800.5] * 75
    lows = [799.5] * 75
    highs[12] = 808.5            # 10:15 bar tags the target
    t = paper.advance(trade(), bars_of(closes, highs, lows), ist(15, 35), DeskProfile())
    assert t["status"] == "closed" and t["entry_fill"]["time"] == "09:45"   # first bar after 09:41
    assert t["exit_fill"]["reason"] == "target" and t["exit_fill"]["price"] == 808.0
    assert t["r_gross"] == 2.0 and t["r_net"] < 2.0 and t["pnl_net"] < t["pnl_gross"]


def test_paper_stop_first_when_bar_touches_both_and_gap_fills_worse():
    closes = [800.0] * 75
    highs = [800.5] * 75
    lows = [799.5] * 75
    highs[8], lows[8] = 809.0, 795.0     # both in one bar -> stop
    t = paper.advance(trade(), bars_of(closes, highs, lows), ist(15, 35), DeskProfile())
    assert t["exit_fill"]["reason"] == "stop" and t["exit_fill"]["price"] == 796.0
    opens = [800.0] * 75
    opens[10], lows[10], highs[10] = 790.0, 789.0, 791.0   # gap through the stop
    lows[8], highs[8] = 799.5, 800.5
    t = paper.advance(trade(), bars_of(closes, highs, lows, opens), ist(15, 35), DeskProfile())
    assert t["exit_fill"]["price"] == 790.0 and t["r_gross"] < -1


def test_paper_flat_by_market_slippage_and_cancel():
    t = paper.advance(trade("MARKET"), bars_of([800.0] * 75), ist(15, 35), DeskProfile())
    assert t["entry_fill"]["price"] == 800.1           # open + 2 ticks of ₹0.05
    assert t["exit_fill"]["reason"] == "flat_by" and t["exit_fill"]["time"] == "15:15"
    never = [810.0] * 75
    t = paper.advance(trade(), bars_of(never, [811.0] * 75, [809.0] * 75), ist(15, 35),
                      DeskProfile())
    assert t["status"] == "cancelled"
    # Mid-session: still pending, nothing invented.
    mid = paper.advance(trade(), bars_of([810.0] * 10, [811.0] * 10, [809.0] * 10),
                        ist(10, 5), DeskProfile())
    assert mid["status"] == "pending"
    assert paper.advance(mid, bars_of([810.0] * 10), ist(10, 5), DeskProfile()) == mid


def test_paper_stats():
    ts = [{"status": "closed", "r_net": 1.5, "pnl_net": 300, "charges": 20,
           "trade_date": "2026-10-27", "exit_fill": {"time": "10:00"}},
          {"status": "closed", "r_net": -1.1, "pnl_net": -220, "charges": 20,
           "trade_date": "2026-10-28", "exit_fill": {"time": "10:00"}}]
    s = paper.stats(ts)
    assert s["trades"] == 2 and s["win_rate"] == 0.5 and s["profit_factor"] == 1.36
    assert s["max_drawdown"] == 220


# ---------------------------------------------------------------- API lifecycle

@pytest.fixture()
def api(tmp_path, monkeypatch):
    reset_desk_store_for_tests(tmp_path / "desk.sqlite")
    scan = make_store(tmp_path, None)
    scan.upsert_instruments([{"symbol": "SBIN", "yf_symbol": "SBIN.NS", "name": "SBI",
                              "index_memberships": ["NIFTY500"], "fno": True}])
    closes = [800.0] * 9
    rows = session_rows(DAY, closes, highs=[800.5] * 9, lows=[799.5] * 9)
    long = pd.DataFrame([{"symbol": "SBIN", "ts": r[0].isoformat(), "open": r[1],
                          "high": r[2], "low": r[3], "close": r[4], "volume": r[5]}
                         for r in rows])
    scan.upsert_bars("5m", long)
    clock = {"now": ist(10, 0)}
    monkeypatch.setattr(desk_routes, "_now", lambda: clock["now"])
    client, user = desk_client(desk_routes.router)
    client.put("/api/desk/profile", json={"max_loss_per_trade_inr": 200})
    feeds.clear_facts_providers_for_tests()
    return client, user, clock, scan


def test_ticket_confirm_paper_and_ledger(api):
    client, user, clock, scan = api
    plan = client.post("/api/desk/plans", json={"symbol": "SBIN", "entry": 800.0,
                                                "entry_time": "09:40", "setup": "level",
                                                "stop_rule": "price", "stop_param": 796,
                                                "target_param": 2}).json()
    tk = client.post("/api/desk/tickets", json={"plan_id": plan["id"]}).json()
    assert tk["status"] == "ready" and tk["order"]["qty"] == 50
    assert tk["words"].startswith("Buy 50 SBIN on NSE at ₹800.00 limit")
    assert tk["kite_basket"][0]["transaction_type"] == "BUY"
    user["id"] = "user_b"
    assert client.post(f"/api/desk/tickets/{tk['id']}/confirm",
                       json={"mode": "paper"}).status_code == 404
    user["id"] = "user_a"
    r = client.post(f"/api/desk/tickets/{tk['id']}/confirm", json={"mode": "paper"})
    assert r.status_code == 200 and r.json()["status"] == "paper"
    assert client.post(f"/api/desk/tickets/{tk['id']}/confirm",
                       json={"mode": "paper"}).status_code == 409
    led = client.get("/api/desk/paper").json()
    assert led["trades"][0]["status"] == "pending"
    # A second ticket for the same symbol today is a duplicate -> blocked.
    tk2 = client.post("/api/desk/tickets", json={"plan_id": plan["id"]}).json()
    assert tk2["status"] == "blocked" and any("duplicate" in x for x in tk2["verdict"]["reasons"])
    # More bars arrive: the 10:00 bar fills the limit.
    more = session_rows(DAY, [800.0] * 12, highs=[800.5] * 12, lows=[799.5] * 12)[9:]
    scan.upsert_bars("5m", pd.DataFrame([{"symbol": "SBIN", "ts": r[0].isoformat(),
                                          "open": r[1], "high": r[2], "low": r[3],
                                          "close": r[4], "volume": r[5]} for r in more]))
    clock["now"] = ist(10, 16)
    led = client.get("/api/desk/paper").json()
    t = led["trades"][0]
    assert t["status"] == "open" and t["entry_fill"]["time"] == "10:00"
    closed = client.post(f"/api/desk/paper/{t['id']}/close", json={"price": 801.0}).json()
    assert closed["status"] == "closed" and closed["exit_fill"]["reason"] == "manual"
    assert client.get("/api/desk/paper").json()["stats"]["trades"] == 1


def test_ticket_reblocks_on_confirm_when_window_closes(api):
    client, _, clock, _ = api
    plan = client.post("/api/desk/plans", json={"symbol": "SBIN", "entry": 800.0,
                                                "entry_time": "09:40", "setup": "level",
                                                "stop_rule": "price", "stop_param": 796}).json()
    tk = client.post("/api/desk/tickets", json={"plan_id": plan["id"]}).json()
    clock["now"] = ist(14, 50)
    r = client.post(f"/api/desk/tickets/{tk['id']}/confirm", json={"mode": "broker"})
    assert r.status_code == 409 and "window" in str(r.json()["detail"])
    assert client.get("/api/desk/tickets").json()[0]["status"] == "blocked"
