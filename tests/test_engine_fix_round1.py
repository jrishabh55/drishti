"""Engine fix round 1 (final-engine-review F1–F4): atomic daily quota across
concurrent scans, signal-time-only opening-range inputs, scale-out vs the
terminal target and backtest symbol overrides. Fake broker and controlled
bars only — nothing reaches a broker or a paid feed."""
from __future__ import annotations

from copy import deepcopy

import pytest

import tests.kite_fake as fk
from apps.api.desk import bars_source, paper
from apps.api.kite import strategies
from apps.api.kite.store import get_kite_store
from tests.test_kite_execution import _armed, _breakout_rows, env, ist  # noqa: F401 — fixture
from tests.test_strategy_platform import (DAILY_ABOVE_850, PROF, _bars, _full_session,  # noqa: F401
                                          _open_trade, _run, _scanner, _strategy, acct)

TCS = "2953217,11536,TCS,TATA CONSULTANCY,0,,0,0.05,1,EQ,NSE,NSE\n"


# ---------------------------------------------------------------- F1

def _session_provider(rows):
    bars_source.clear_live_providers_for_tests()

    def session(uid, sym, interval, kind):
        if kind == "session":
            return bars_source.BarSeries(sym, interval, rows, "live:kite",
                                         rows[-1][0].isoformat())
    bars_source.register_live_provider(session)


def _two_stale_scans(monkeypatch, s_id, now):
    """Both scans load the strategy before either acts (the sweep overlapping
    a manual Scan), and the scanner's matches differ between them."""
    store = get_kite_store()
    loaded = store.get_strategy(s_id, "user_a")
    stale = deepcopy(loaded)
    monkeypatch.setattr(strategies, "candidates", lambda uid, s, **kw: (["SBIN"], None))
    first = strategies.scan("user_a", loaded, now)
    monkeypatch.setattr(strategies, "candidates", lambda uid, s, **kw: (["TCS"], None))
    second = strategies.scan("user_a", stale, now)
    return first, second, store.get_strategy(s_id, "user_a")


def test_concurrent_auto_scans_send_at_most_the_daily_cap(env, monkeypatch):
    client, desk, user, fake, clock = env
    monkeypatch.setattr(fk, "INSTRUMENTS", fk.INSTRUMENTS + TCS)
    _armed(client, fake, clock)
    _session_provider(_breakout_rows())
    auto = _strategy(client, mode="auto", symbols=[], scanner_id=_scanner(DAILY_ABOVE_850),
                     max_signals_per_day=1)
    client.post(f"/api/kite/strategies/{auto['id']}/auto-opt-in", json={"ack": "AUTO orb"})
    clock["now"] = ist(9, 36)
    first, second, saved = _two_stale_scans(monkeypatch, auto["id"], clock["now"])
    assert [(g["symbol"], g["status"]) for g in first] == [("SBIN", "sent")]
    assert second == []
    assert [x["tradingsymbol"] for x in fake.placed()] == ["SBIN"]
    assert [(g["symbol"], g["status"]) for g in saved["signals"]] == [("SBIN", "sent")]
    assert saved["last_scan"]["outcomes"]["TCS"]["status"] == "limit_reached"


def test_concurrent_paper_scans_respect_the_cap_and_keep_each_others_signals(env, monkeypatch):
    client, desk, *_ , clock = env
    _session_provider(_breakout_rows())
    s = _strategy(client, mode="paper", symbols=["SBIN"], max_signals_per_day=2)
    clock["now"] = ist(9, 36)
    store = get_kite_store()
    loaded = store.get_strategy(s["id"], "user_a")
    stale = deepcopy(loaded)
    strategies.scan("user_a", loaded, clock["now"])
    # The stale copy still sees SBIN as unsignalled: the stored state must win.
    assert strategies.scan("user_a", stale, clock["now"]) == []
    saved = store.get_strategy(s["id"], "user_a")
    assert [(g["symbol"], g["status"]) for g in saved["signals"]] == [("SBIN", "paper")]
    assert len(get_desk_store_trades()) == 1


def get_desk_store_trades():
    from apps.api.desk.store import get_desk_store
    return get_desk_store().list_paper("user_a", portfolio_id=None)


def test_a_failed_action_keeps_its_reservation_and_fails_closed(env, monkeypatch):
    client, desk, *_ , clock = env
    _session_provider(_breakout_rows())
    s = _strategy(client, mode="paper", symbols=["SBIN"], max_signals_per_day=1)
    clock["now"] = ist(9, 36)

    def boom(*a, **k):
        raise RuntimeError("crashed mid-action")
    real = strategies._act
    monkeypatch.setattr(strategies, "_act", boom)
    with pytest.raises(RuntimeError):
        strategies.scan("user_a", get_kite_store().get_strategy(s["id"], "user_a"), clock["now"])
    monkeypatch.setattr(strategies, "_act", real)
    saved = get_kite_store().get_strategy(s["id"], "user_a")
    assert [g["status"] for g in saved["signals"]] == ["reserved"]
    assert strategies.scan("user_a", saved, clock["now"]) == []
    assert get_desk_store_trades() == []


# ---------------------------------------------------------------- F2

def _level_run(acct, monkeypatch, future_volume, client_kw):
    bc, desk, s, day, _ = acct
    client = client_kw
    rows = [tuple([*r[:5], future_volume if 3 <= i < 12 else 1000])
            for i, r in enumerate(_full_session(day))]
    bars_source.clear_live_providers_for_tests()

    def history(uid, sym, interval, kind):
        return bars_source.BarSeries(sym, interval, rows, "live:kite", rows[-1][0].isoformat())
    bars_source.register_live_provider(history)
    level = _strategy(client, setup="level", level=800, or_minutes=60, stop_rule="or_low", target_rule="price", target_param=815,
                      min_volume_x=1, name=f"lvl{future_volume}")
    return _run(bc, level, day)


def test_level_backtest_signal_never_depends_on_later_opening_range_bars(acct, env, monkeypatch):
    client = env[0]
    a = _level_run(acct, monkeypatch, 1000, client)
    b = _level_run(acct, monkeypatch, 100_000, client)
    pick = lambda r: [(t["signal_time"], t["qty"], t["stop"]) for t in r["trades"]]  # noqa: E731
    assert a["trades"], (a["skipped"], a["coverage"])
    assert pick(a) == pick(b) and pick(a)[0][0] == "09:30"
    # The or_low stop comes from the three bars closed by 09:30 only.
    assert a["trades"][0]["stop"] == 799.0


def test_entry_opening_range_at_a_level_trigger_is_the_signal_time_prefix():
    from apps.api.desk import entry
    from tests.desk_utils import session_rows

    closes = [800.0, 801.0, 800.5, 802.5] + [803.0] * 71
    lows = [799.0, 800.0, 800.0, 801.0, 790.0] + [802.0] * 70     # 09:35 low only later
    rows = session_rows(ist(9, 0).date(), closes, lows=lows)
    ev = entry.evaluate(rows, ist(15, 31), symbol="X", setup="level", level=801.5,
                        or_minutes=60)
    assert ev["trigger"]["time"] == "09:35"
    assert ev["opening_range"]["low"] == 799.0 and ev["opening_range"]["complete"] is False


# ---------------------------------------------------------------- F3

def test_scale_out_above_the_target_closes_everything_at_the_target():
    t = paper.advance(_open_trade({"scale_out_at_r": 6, "scale_out_pct": 50}),
                      _bars([(100, 100.5, 99.5, 100), (100, 107, 99.5, 104)]), ist(9, 45), PROF)
    assert [(x["price"], x["qty"], x["reason"]) for x in t["exit_legs"]] == [(104.0, 10, "target")]
    assert t["pnl_gross"] == 40.0


def test_scale_out_below_the_target_still_books_both_legs():
    t = paper.advance(_open_trade({"scale_out_at_r": 2, "scale_out_pct": 50}),
                      _bars([(100, 100.5, 99.5, 100), (100, 105, 99.5, 104)]), ist(9, 45), PROF)
    assert [(x["price"], x["qty"]) for x in t["exit_legs"]] == [(102.0, 5), (104.0, 5)]


def test_gap_open_above_scale_out_and_target_exits_everything_at_the_open():
    t = paper.advance(_open_trade({"scale_out_at_r": 6, "scale_out_pct": 50}),
                      _bars([(100, 100.5, 99.5, 100), (108, 109, 107, 108)]), ist(9, 45), PROF)
    assert [(x["price"], x["qty"], x["reason"]) for x in t["exit_legs"]] == [(108.0, 10, "target")]


# ---------------------------------------------------------------- F4

def test_non_scanner_symbols_override_is_the_daily_candidate_list(acct):
    bc, desk, s, day, _ = acct
    store = get_kite_store()
    store.put_strategy("user_a", dict(store.get_strategy(s["id"], "user_a"), symbols=["SBIN"]))
    res = _run(bc, s, day, symbols=["TCS"])
    assert res["config"]["symbols"] == ["TCS"] and res["coverage"]["TCS"]["status"] == "ok"
    assert [t["symbol"] for t in res["trades"]] == ["TCS"]
