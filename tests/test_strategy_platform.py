"""Portfolio strategies: versions, attribution, freshness diagnostics, advanced
exits, the opt-in auto path and saved backtests. Controlled providers only —
the fake Kite never reaches a broker and no real order is placed."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from apps.api.desk import bars_source, paper
from apps.api.desk.bars import session_bars
from apps.api.desk.market.session import NSE
from apps.api.desk.profile import DeskProfile
from apps.api.desk.store import get_desk_store
from apps.api.kite import strategies
from apps.api.kite.store import get_kite_store
from apps.api.routes import strategy_backtests as bt_routes
from tests.desk_utils import desk_client, session_rows, trading_days
from tests.test_kite_execution import (DAY, _armed, _breakout_provider, _breakout_rows,
                                       env, ist)  # noqa: F401 — fixture

ATTR = ("strategy_id", "strategy_name", "strategy_version")


def _strategy(client, **kw):
    body = {"name": "orb", "symbols": ["SBIN"], **kw}
    r = client.post("/api/kite/strategies", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------- contract

def test_new_fields_default_and_validate(env):
    client, desk, _, _, _ = env
    s = _strategy(client)
    assert (s["mode"], s["portfolio_id"], s["interval"], s["version"]) == ("off", "default", "5m", 1)
    assert s["scanner_id"] is None and s["min_volume_x"] is None and s["exit_policy"] is None
    bad = [
        {"exit_policy": {"scale_out_at_r": 1, "scale_out_pct": 100}},
        {"exit_policy": {"trail_after_r": 1}},                       # distance missing
        {"exit_policy": {"breakeven_after_r": -1}},
        {"interval": "15m"},
        {"min_volume_x": 0},
        {"portfolio_id": "nope"},                                    # not this user's
        {"mode": "auto", "auto_opt_in": True},                       # opt-in is not a field
    ]
    for extra in bad:
        r = client.post("/api/kite/strategies", json={"name": "x", "symbols": ["SBIN"], **extra})
        assert r.status_code == 422, (extra, r.text)
    pf = desk.post("/api/desk/portfolios", json={"name": "Lab", "capital": 50000}).json()
    for mode in ("confirm", "auto"):
        r = client.post("/api/kite/strategies", json={"name": "x", "symbols": ["SBIN"],
                                                      "mode": mode, "portfolio_id": pf["id"]})
        assert r.status_code == 422 and "Default" in r.text
    r = client.post("/api/kite/strategies", json={"name": "x", "symbols": ["SBIN"], "mode": "confirm",
                                                  "exit_policy": {"breakeven_after_r": 1}})
    assert r.status_code == 422 and "paper" in r.text
    assert _strategy(client, portfolio_id=pf["id"], mode="paper", interval="1m",
                     exit_policy={"breakeven_after_r": 1})["portfolio_id"] == pf["id"]


def test_legacy_strategy_without_new_fields_still_scans(env):
    client, desk, _, _, clock = env
    bars_source.register_live_provider(_breakout_provider(_breakout_rows()))
    clock["now"] = ist(9, 36)
    legacy = {"name": "old", "mode": "paper", "symbols": ["SBIN"], "setup": "orb", "level": None,
              "or_minutes": 15, "stop_rule": "or_mid", "stop_param": None, "target_r": 2.0,
              "order_type": "LIMIT", "max_signals_per_day": 1, "confirm_ttl_s": 120, "signals": []}
    s = get_kite_store().put_strategy("user_a", legacy)
    out = client.post(f"/api/kite/strategies/{s['id']}/scan").json()
    assert out["new"][0]["status"] == "paper"
    trade = desk.get("/api/desk/paper").json()["trades"][0]
    assert trade["strategy_version"] == 1 and trade["portfolio_id"] == "default"


def test_scanner_must_be_prebuilt_or_owned(env):
    client, _, user, _, _ = env
    from apps.api.scanner.store import get_scanner_store
    store = get_scanner_store()
    other = store.create_scanner("user_b", "theirs", "", {"op": "and", "conditions": []})
    r = client.post("/api/kite/strategies", json={"name": "x", "symbols": ["SBIN"],
                                                  "scanner_id": other})
    assert r.status_code == 422 and "scanner" in r.text
    mine = store.create_scanner("user_a", "mine", "", {"op": "and", "conditions": []})
    assert _strategy(client, scanner_id=mine)["scanner_id"] == mine


# ---------------------------------------------------------------- runtime

def test_paper_strategy_in_named_portfolio_is_attributed_and_bound_to_its_version(env):
    client, desk, _, _, clock = env
    bars_source.register_live_provider(_breakout_provider(_breakout_rows()))
    clock["now"] = ist(9, 36)
    pf = desk.post("/api/desk/portfolios", json={"name": "Lab", "capital": 100000}).json()
    s = _strategy(client, mode="paper", portfolio_id=pf["id"])
    sig = client.post(f"/api/kite/strategies/{s['id']}/scan").json()["new"][0]
    assert sig["status"] == "paper" and sig["strategy_version"] == 1
    trade = desk.get("/api/desk/paper", params={"portfolio_id": pf["id"]}).json()["trades"][0]
    tk = get_desk_store().get_ticket(sig["ticket_id"], "user_a")
    plan = get_desk_store().get_plan(tk["plan_id"], "user_a")
    for doc in (trade, tk, tk["order"], plan):
        assert [doc[k] for k in ATTR] == [s["id"], "orb", 1], doc
    assert trade["portfolio_id"] == pf["id"] and tk["portfolio_id"] == pf["id"]
    assert desk.get("/api/desk/paper").json()["trades"] == []          # Default untouched
    edited = client.put(f"/api/kite/strategies/{s['id']}", json={
        "name": "orb v2", "symbols": ["SBIN"], "portfolio_id": pf["id"]}).json()
    assert edited["version"] == 2
    again = desk.get("/api/desk/paper", params={"portfolio_id": pf["id"]}).json()["trades"][0]
    assert again["strategy_version"] == 1 and again["strategy_name"] == "orb"


def test_scan_loaded_before_an_edit_cannot_act_on_the_old_version(env):
    client, desk, _, _, clock = env
    bars_source.register_live_provider(_breakout_provider(_breakout_rows()))
    clock["now"] = ist(9, 36)
    s = _strategy(client, mode="paper")
    stale = get_kite_store().get_strategy(s["id"], "user_a")
    client.put(f"/api/kite/strategies/{s['id']}", json={"name": "orb", "symbols": ["SBIN"],
                                                       "mode": "paper", "target_r": 3})
    assert strategies.scan("user_a", stale, clock["now"]) == []
    assert desk.get("/api/desk/paper").json()["trades"] == []


def _with_empty_infy(provider):
    def wrapped(user_id, symbol, interval, kind):
        if symbol == "INFY":
            return bars_source.BarSeries(symbol, interval, [], "live:kite", None)
        return provider(user_id, symbol, interval, kind)
    return wrapped


def test_diagnostics_record_each_symbol_outcome(env):
    client, desk, _, _, clock = env
    bars_source.register_live_provider(_with_empty_infy(_breakout_provider(_breakout_rows())))
    s = _strategy(client, mode="paper", symbols=["SBIN", "INFY"])
    clock["now"] = ist(10, 30)                                       # trigger is 55 min old
    client.post(f"/api/kite/strategies/{s['id']}/scan")
    diag = get_kite_store().get_strategy(s["id"], "user_a")["last_scan"]
    assert diag["at"] == ist(10, 30).isoformat() and diag["version"] == 1
    assert diag["outcomes"]["SBIN"]["status"] == "stale"
    assert diag["outcomes"]["INFY"]["status"] == "no_data"
    clock["now"] = ist(9, 36)
    client.post(f"/api/kite/strategies/{s['id']}/scan")
    diag = get_kite_store().get_strategy(s["id"], "user_a")["last_scan"]
    assert diag["outcomes"]["SBIN"]["status"] == "paper" and diag["source"] == "live:kite"
    client.post(f"/api/kite/strategies/{s['id']}/scan")
    diag = get_kite_store().get_strategy(s["id"], "user_a")["last_scan"]
    assert diag["outcomes"]["SBIN"]["status"] == "limit_reached"
    assert len(get_kite_store().get_strategy(s["id"], "user_a")["signals"]) == 1


def test_delayed_data_never_becomes_a_live_confirm_signal(env):
    client, _, _, _, clock = env

    def delayed(user_id, symbol, interval, kind):
        if kind == "session" and symbol == "SBIN":
            rows = _breakout_rows()
            return bars_source.BarSeries(symbol, interval, rows, "delayed:yahoo",
                                         rows[-1][0].isoformat())
    bars_source.register_live_provider(delayed)
    clock["now"] = ist(9, 36)
    s = _strategy(client, mode="confirm")
    assert client.post(f"/api/kite/strategies/{s['id']}/scan").json()["new"] == []
    out = get_kite_store().get_strategy(s["id"], "user_a")["last_scan"]["outcomes"]["SBIN"]
    assert out["status"] == "stale" and "delayed" in out["reason"]


def test_pause_stops_scans_and_resume_never_overrides_kill(env):
    client, desk, _, _, clock = env
    bars_source.register_live_provider(_breakout_provider(_breakout_rows()))
    clock["now"] = ist(9, 36)
    s = _strategy(client, mode="paper")
    assert client.post(f"/api/kite/strategies/{s['id']}/pause").json()["paused"] is True
    assert client.post(f"/api/kite/strategies/{s['id']}/scan").json()["new"] == []
    assert desk.get("/api/desk/paper").json()["trades"] == []
    assert client.post(f"/api/kite/strategies/{s['id']}/resume").json()["paused"] is False
    client.post("/api/kite/kill")
    assert client.post(f"/api/kite/strategies/{s['id']}/resume").status_code == 409


# ---------------------------------------------------------------- auto mode

def test_auto_mode_needs_separate_opt_in_and_every_live_gate(env):
    client, _, _, fake, clock = env
    bars_source.register_live_provider(_breakout_provider(_breakout_rows()))
    s = _strategy(client, mode="auto")
    clock["now"] = ist(9, 36)
    client.post(f"/api/kite/strategies/{s['id']}/scan")
    out = get_kite_store().get_strategy(s["id"], "user_a")["last_scan"]["outcomes"]["SBIN"]
    assert out["status"] == "blocked" and "opt-in" in out["reason"] and fake.placed() == []
    r = client.post(f"/api/kite/strategies/{s['id']}/auto-opt-in", json={"ack": "yes"})
    assert r.status_code == 422
    r = client.post(f"/api/kite/strategies/{s['id']}/auto-opt-in", json={"ack": "AUTO orb"})
    assert r.status_code == 200 and r.json()["auto_opt_in"]["version"] == 1
    n = len(fake.calls)
    sig = client.post(f"/api/kite/strategies/{s['id']}/scan").json()["new"]
    assert sig == [] or sig[0]["status"] == "blocked"                # not armed
    assert len(fake.calls) == n and fake.placed() == []
    # An edit drops the opt-in: a new version must be opted in again.
    client.put(f"/api/kite/strategies/{s['id']}", json={"name": "orb", "symbols": ["SBIN"],
                                                       "mode": "auto"})
    assert get_kite_store().get_strategy(s["id"], "user_a").get("auto_opt_in") is None


def test_auto_mode_sends_through_the_existing_gates_when_everything_allows(env):
    client, _, _, fake, clock = env
    _armed(client, fake, clock)
    bars_source.register_live_provider(_breakout_provider(_breakout_rows()))
    s = _strategy(client, mode="auto")
    client.post(f"/api/kite/strategies/{s['id']}/auto-opt-in", json={"ack": "AUTO orb"})
    clock["now"] = ist(9, 36)
    new = client.post(f"/api/kite/strategies/{s['id']}/scan").json()["new"]
    assert new[0]["status"] == "sent", new
    trade = get_kite_store().get_trade(new[0]["kite_trade_id"], "user_a")
    assert [trade[k] for k in ATTR] == [s["id"], "orb", 1] and trade["source"] == f"strategy:{s['id']}"
    assert len(fake.placed()) == 1                                   # fake broker only


def test_kill_drops_auto_opt_in(env):
    client, _, _, _, _ = env
    s = _strategy(client, mode="auto")
    client.post(f"/api/kite/strategies/{s['id']}/auto-opt-in", json={"ack": "AUTO orb"})
    client.post("/api/kite/kill")
    doc = get_kite_store().get_strategy(s["id"], "user_a")
    assert doc["mode"] == "off" and doc.get("auto_opt_in") is None


# ---------------------------------------------------------------- exits

PROF = DeskProfile(slippage_ticks=0)


def _open_trade(policy=None, stop=99.0, target=104.0):
    order = {"symbol": "TEST", "qty": 10, "order_type": "MARKET", "stop": stop, "target": target,
             "plan": {"entry": 100.0, "stop": stop, "target": target, "risk_per_share": 1.0,
                      "flat_by": "15:05", "interval": "5m"}, "exit_policy": policy}
    return paper.new_trade(order, ticket_id="t", placed_at=ist(9, 30), profile=PROF)


def _bars(ohlc):
    rows = []
    for k, (o, h, lo, c) in enumerate(ohlc):
        rows.append((NSE.local(DAY, NSE.bar_start_min(3 + k)), o, h, lo, c, 1000.0))
    return session_bars(rows, NSE, ist(15, 30))[0]


def test_no_policy_keeps_fixed_stop_target():
    t = paper.advance(_open_trade(), _bars([(100, 100.5, 99.5, 100), (100, 101.6, 99.8, 101.5),
                                            (101.5, 101.6, 98.9, 99)]), ist(15, 30), PROF)
    assert t["exit_fill"]["reason"] == "stop" and t["exit_fill"]["price"] == 99.0


def test_breakeven_moves_stop_after_the_completed_bar():
    t = paper.advance(_open_trade({"breakeven_after_r": 1}),
                      _bars([(100, 100.5, 99.5, 100), (100, 101.2, 99.8, 101.0),
                             (101, 101.1, 99.9, 100.0)]), ist(15, 30), PROF)
    assert t["exit_fill"]["reason"] == "breakeven" and t["exit_fill"]["price"] == 100.0
    assert t["initial_stop"] == 99.0 and t["r_gross"] == 0.0


def test_breakeven_bar_itself_is_not_protected():
    # The bar that reaches +1R and dips back to the old stop: the old stop applies.
    t = paper.advance(_open_trade({"breakeven_after_r": 1}),
                      _bars([(100, 100.5, 99.5, 100), (100, 101.2, 98.9, 101.0)]),
                      ist(15, 30), PROF)
    assert t["exit_fill"]["reason"] == "stop" and t["exit_fill"]["price"] == 99.0


def test_trailing_stop_follows_the_high():
    t = paper.advance(_open_trade({"trail_after_r": 1, "trail_distance_r": 1}),
                      _bars([(100, 100.5, 99.5, 100), (100, 102.0, 99.8, 101.8),
                             (101.8, 103.0, 101.5, 102.8), (102.8, 102.9, 101.5, 101.7)]),
                      ist(15, 30), PROF)
    assert t["exit_fill"]["reason"] == "trail" and t["exit_fill"]["price"] == 102.0


def test_scale_out_settles_both_legs():
    t = paper.advance(_open_trade({"scale_out_at_r": 1, "scale_out_pct": 50}),
                      _bars([(100, 100.5, 99.5, 100), (100, 101.2, 99.8, 101.0),
                             (101, 104.5, 100.5, 104)]), ist(15, 30), PROF)
    legs = t["exit_legs"]
    assert [(x["qty"], x["price"], x["reason"]) for x in legs] == [
        (5, 101.0, "scale_out"), (5, 104.0, "target")]
    assert t["pnl_gross"] == 5 * 1.0 + 5 * 4.0 and t["status"] == "closed"


def test_bar_hitting_stop_and_scale_out_is_a_full_stop():
    t = paper.advance(_open_trade({"scale_out_at_r": 1, "scale_out_pct": 50}),
                      _bars([(100, 100.5, 99.5, 100), (100, 101.5, 98.5, 99)]), ist(15, 30), PROF)
    assert t.get("exit_legs") in (None, []) or len(t["exit_legs"]) == 1
    assert t["exit_fill"]["reason"] == "stop" and t["pnl_gross"] == -10.0


def test_refresh_never_replays_processed_bars_with_a_moved_stop():
    bars = _bars([(100, 100.5, 99.5, 100), (100, 101.2, 99.8, 101.0)])
    t = paper.advance(_open_trade({"breakeven_after_r": 1}), bars, ist(9, 45), PROF)
    assert t["status"] == "open" and t["stop"] == 100.0
    again = paper.advance(t, bars, ist(9, 46), PROF)
    assert again == t


# ---------------------------------------------------------------- backtests

def _full_session(day, breakout=True):
    closes = [800.0, 801.0, 800.5] + ([802.5] if breakout else [800.8]) + \
        [803 + 0.1 * k for k in range(NSE.n_bars - 4)]
    highs = [801.0, 801.5, 801.0, 803.0] + [c + 0.3 for c in closes[4:]]
    lows = [799.0, 800.0, 800.0, 801.0] + [c - 0.3 for c in closes[4:]]
    if not breakout:
        highs = [801.0, 801.5, 801.0, 801.0] + [801.2] * (NSE.n_bars - 4)
        closes = [800.0, 801.0, 800.5] + [800.8] * (NSE.n_bars - 3)
        lows = [799.0, 800.0, 800.0] + [800.5] * (NSE.n_bars - 3)
    return session_rows(day, closes, highs=highs, lows=lows)


@pytest.fixture()
def bt(env, monkeypatch):
    client, desk, user, fake, clock = env
    days = trading_days(DAY - timedelta(days=1), 6)
    rows = []
    for i, d in enumerate(days):
        if i == 2:
            rows += _full_session(d)[:20]                         # incomplete session
        else:
            rows += _full_session(d, breakout=i % 2 == 0)

    def history(user_id, symbol, interval, kind):
        if kind == "history" and user_id == "user_a":
            got = rows if symbol == "SBIN" else []
            return bars_source.BarSeries(symbol, interval, got, "live:kite",
                                         got[-1][0].isoformat() if got else None)
    bars_source.register_live_provider(history)
    clock["now"] = ist(16, 0)
    monkeypatch.setattr(bt_routes, "_now", lambda: clock["now"])
    bclient, _ = desk_client(bt_routes.router, user)
    s = _strategy(client, symbols=["SBIN", "INFY"])
    return bclient, s, days, user


def test_backtest_replays_strategy_semantics_and_saves_immutable_result(bt):
    client, s, days, user = bt
    body = {"strategy_id": s["id"], "start_date": days[0].isoformat(),
            "end_date": days[-1].isoformat(), "split_date": days[3].isoformat()}
    r = client.post("/api/kite/strategy-backtests", json=body)
    assert r.status_code == 201, r.text
    res = r.json()
    assert res["strategy_snapshot"]["version"] == 1 and res["strategy_snapshot"]["id"] == s["id"]
    assert "signals" not in res["strategy_snapshot"]
    assert res["sources"]["SBIN"] == "live:kite"
    cov = res["coverage"]
    assert cov["SBIN"]["days"][days[2].isoformat()] == "incomplete"
    assert cov["SBIN"]["days"][days[0].isoformat()] == "complete"
    assert cov["INFY"]["status"] == "insufficient_history"
    trades = res["trades"]
    assert {t["trade_date"] for t in trades} == {days[0].isoformat(), days[4].isoformat()}
    for t in trades:
        assert t["entry_fill"]["time"] >= t["signal_time"]               # no past-bar fill
        assert [t[k] for k in ATTR] == [s["id"], "orb", 1]
        assert t["split"] == ("train" if t["trade_date"] < days[3].isoformat() else "test")
        assert t["charges"] > 0
    assert res["summary"]["all"]["trades"] == 2
    assert res["summary"]["train"]["trades"] == 1 and res["summary"]["test"]["trades"] == 1
    assert "not a prediction" in res["disclaimer"]
    again = client.post("/api/kite/strategy-backtests", json=body).json()
    assert again["trades"] == trades and again["id"] != res["id"]       # deterministic
    assert client.get(f"/api/kite/strategy-backtests/{res['id']}").json() == res
    listed = client.get("/api/kite/strategy-backtests", params={"strategy_id": s["id"]}).json()
    assert {x["id"] for x in listed} == {res["id"], again["id"]} and "trades" not in listed[0]
    assert client.put(f"/api/kite/strategy-backtests/{res['id']}", json={}).status_code == 405
    assert client.delete(f"/api/kite/strategy-backtests/{res['id']}").status_code == 405
    user["id"] = "user_b"
    assert client.get(f"/api/kite/strategy-backtests/{res['id']}").status_code == 404
    assert client.get("/api/kite/strategy-backtests").json() == []
    assert client.post("/api/kite/strategy-backtests", json=body).status_code == 404


def test_backtest_bounds(bt):
    client, s, days, _ = bt
    base = {"strategy_id": s["id"]}
    today = DAY
    cases = [
        {"start_date": "2026-10-20", "end_date": "2026-10-10"},                  # inverted
        {"start_date": "2026-10-20", "end_date": (today + timedelta(days=1)).isoformat()},
        {"start_date": "2026-08-01", "end_date": "2026-10-20"},                  # > 60 days
        {"start_date": "2026-10-20", "end_date": "2026-10-27", "split_date": "2026-10-28"},
        {"start_date": "2026-10-20", "end_date": "2026-10-27",
         "symbols": [f"S{i}" for i in range(21)]},
    ]
    for c in cases:
        assert client.post("/api/kite/strategy-backtests", json={**base, **c}).status_code == 422, c


# ---------------------------------------------------------------- research price levels

def test_price_stop_and_fixed_target_validate(env):
    client, _, _, _, _ = env
    bad = [
        {"stop_rule": "price"},                                              # no stop price
        {"target_rule": "price"},                                            # no target price
        {"stop_rule": "price", "stop_param": 810, "target_rule": "price", "target_param": 805},
        {"setup": "level", "level": 800, "stop_rule": "price", "stop_param": 801},
        {"setup": "level", "level": 800, "target_rule": "price", "target_param": 799},
        {"target_rule": "price", "target_param": float("inf")},
    ]
    for extra in bad:
        r = client.post("/api/kite/strategies", content=__import__("json").dumps(
            {"name": "x", "symbols": ["SBIN"], **extra}, allow_nan=True),
            headers={"content-type": "application/json"})
        assert r.status_code == 422, (extra, r.text)
    s = _strategy(client, setup="level", level=802, stop_rule="price", stop_param=797.5,
                  target_rule="price", target_param=812.0)
    assert (s["target_rule"], s["target_param"], s["target_r"]) == ("price", 812.0, 2.0)
    assert _strategy(client)["target_rule"] == "r"


def test_runtime_keeps_research_absolute_levels(env):
    client, desk, _, _, clock = env
    bars_source.register_live_provider(_breakout_provider(_breakout_rows()))
    clock["now"] = ist(9, 36)
    s = _strategy(client, mode="paper", stop_rule="price", stop_param=797.5,
                  target_rule="price", target_param=812.0)
    client.post(f"/api/kite/strategies/{s['id']}/scan")
    t = desk.get("/api/desk/paper").json()["trades"][0]
    assert (t["stop"], t["target"]) == (797.5, 812.0)


def test_backtest_keeps_research_absolute_levels(bt):
    client, s, days, user = bt
    store = get_kite_store()
    doc = dict(store.get_strategy(s["id"], "user_a"), stop_rule="price", stop_param=801.0,
               target_rule="price", target_param=808.0)
    store.put_strategy("user_a", doc)
    res = client.post("/api/kite/strategy-backtests", json={
        "strategy_id": s["id"], "start_date": days[0].isoformat(), "end_date": days[0].isoformat(),
        "symbols": ["SBIN"]}).json()
    assert res["trades"], res["skipped"]
    t = res["trades"][0]
    assert (t["stop"], t["target"]) == (801.0, 808.0) and t["exit_fill"]["reason"] == "target"


def test_backtest_refuses_to_replay_scanner_matches(bt):
    client, s, days, _ = bt
    from apps.api.scanner.store import get_scanner_store
    sid = get_scanner_store().create_scanner("user_a", "mine", "", {"op": "and", "conditions": []})
    store = get_kite_store()
    store.put_strategy("user_a", dict(store.get_strategy(s["id"], "user_a"), scanner_id=sid))
    body = {"strategy_id": s["id"], "start_date": days[0].isoformat(),
            "end_date": days[-1].isoformat()}
    r = client.post("/api/kite/strategy-backtests", json={**body, "symbols": ["TCS"]})
    assert r.status_code == 422 and "scanner" in r.text
    ok = client.post("/api/kite/strategy-backtests", json={**body, "symbols": ["SBIN"]})
    assert ok.status_code == 201 and ok.json()["scanner_candidates"] == "not_replayed"


# ---------------------------------------------------------------- broker funds

def _funds_send(env, funds):
    from tests.test_kite_execution import _send, _ticket
    client, desk, _, fake, clock = env
    _armed(client, fake, clock)
    fake.funds = funds
    return _send(client, _ticket(desk)), fake


def test_live_send_checks_real_broker_funds_before_placing(env):
    r, fake = _funds_send(env, {"enabled": True, "net": 30000.0,
                                "available": {"live_balance": 30000.0, "cash": 30000.0}})
    assert r.status_code == 409 and "funds" in r.text and fake.placed() == []


def test_live_send_refuses_when_funds_are_unavailable(env):
    for funds in (None, {"enabled": False, "net": 1e7, "available": {"live_balance": 1e7}},
                  {"enabled": True, "net": "NaN", "available": {"live_balance": 1e7}}):
        from tests.test_kite_execution import _send, _ticket
        client, desk, _, fake, clock = env
        if not fake.placed():
            _armed(client, fake, clock)
        fake.funds = funds
        r = _send(client, _ticket(desk))
        assert r.status_code == 409 and "funds" in r.text, (funds, r.text)
        assert fake.placed() == []


def test_live_send_with_enough_funds_places_exact_quantity(env):
    r, fake = _funds_send(env, {"enabled": True, "net": 1e6,
                                "available": {"live_balance": 1e6, "cash": 1e6}})
    assert r.status_code == 201, r.text
    assert [o["quantity"] for o in fake.placed()] == [50]
