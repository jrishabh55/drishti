"""Time Machine: port-correctness against upstream, NSE behaviour, swing mode."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from apps.api.desk import bars as bars_mod, timemachine as tm
from apps.api.desk.market.session import NYSE
from tests.desk_utils import (
    daily_rows,
    random_walk_sessions,
    session_rows,
    upstream_tsla_result,
)

REEL_NOW = datetime(2026, 10, 19, 20, 30, tzinfo=timezone.utc)  # 4:30 PM ET


def reel_plan():
    return tm.normalize_intraday_plan(
        {"symbol": "TSLA", "date": "2026-10-19", "entry": 364.18, "stop": 358.32,
         "target": 370.04, "entry_time": "10:15 AM"}, NYSE)


def test_port_reproduces_upstream_reel_numbers_exactly():
    rows = bars_mod.rows_from_yahoo_chart(upstream_tsla_result())
    out = tm.run_intraday(rows, reel_plan(), spec=NYSE, now=REEL_NOW, rnd=tm.cents_round)
    mc, h = out["mc"], out["history"]
    # Numbers printed by upstream timemachine.py on the same fixture, seed 19.
    assert (mc["p_target"], mc["p_stop"], mc["p_neither"], mc["p_green"]) == \
        (0.2938, 0.2548, 0.4514, 0.5168)
    assert (mc["median_close"], mc["p5_close"], mc["p95_close"], mc["avg_R"]) == \
        (364.36, 355.54, 373.54, 0.034)
    assert mc["pool_sessions"] == 59 and mc["bars_left"] == 69
    assert (h["sessions"], h["triggered"], h["target"], h["stop"], h["close"], h["avg_R"]) == \
        (59, 33, 13, 15, 5, -0.062)
    assert h["rule"]["stop_rule"] == "or_mid"
    assert out["verdict"] == "Close to a coin flip: target first 29.4% vs stop first 25.5%."
    assert out["verdict_notes"][0].startswith("45.1% of the replays hit neither by 4:00 PM")


def test_same_seed_same_numbers_different_seed_different():
    rows = bars_mod.rows_from_yahoo_chart(upstream_tsla_result())
    a = tm.run_intraday(rows, reel_plan(), spec=NYSE, now=REEL_NOW, rnd=tm.cents_round,
                        replays=2000)
    b = tm.run_intraday(rows, reel_plan(), spec=NYSE, now=REEL_NOW, rnd=tm.cents_round,
                        replays=2000)
    c = tm.run_intraday(rows, reel_plan(), spec=NYSE, now=REEL_NOW, rnd=tm.cents_round,
                        replays=2000, seed=7)
    assert a["mc"] == b["mc"] and a["mc"] != c["mc"]


def nse_rows(n_days=30, plan_day=date(2026, 10, 28)):
    rows = random_walk_sessions(date(2026, 10, 27), n_days, start_price=800.0)
    # Plan day: a quiet opening range then a breakout close on the 09:35 bar.
    closes = [800.0, 801.0, 799.5, 803.0] + [803.0] * 71
    rows += session_rows(plan_day, closes,
                         highs=[801.0, 802.0, 800.5, 803.5] + [803.5] * 71,
                         lows=[799.0, 800.0, 798.5, 801.0] + [802.5] * 71)
    return rows


def test_nse_intraday_run_uses_75_bar_sessions_and_ticks():
    plan = tm.normalize_intraday_plan({"symbol": "SBIN", "date": "2026-10-28", "entry": 803.0,
                                       "stop": 800.25, "target": 808.5, "entry_time": "09:35"})
    assert plan["entry_bar"] == 3 and plan["target_r"] == 2.0
    out = tm.run_intraday(nse_rows(), plan, now=datetime(2026, 10, 28, 12, tzinfo=timezone.utc),
                          until="15:15", replays=3000)
    mc = out["mc"]
    assert mc["pool_sessions"] == 30
    assert mc["until"] == "15:15"
    assert mc["bars_left"] == 71 - 3        # bars 4..71 (last bar closes 15:15)
    assert abs(mc["p_target"] + mc["p_stop"] + mc["p_neither"] - 1) < 1e-3
    assert out["history"]["rule"]["stop_rule"] == "or_mid"   # (802+798.5)/2 -> 800.25
    assert out["history"]["trades"], "the breakout rule should fire on some past sessions"
    assert all(round(t["stop"] * 100) % 5 == 0 for t in out["history"]["trades"])  # ₹0.05 ticks
    assert "Close" in out["verdict"] or "toward" in out["verdict"]


def test_entry_bar_must_be_complete_and_present():
    plan = tm.normalize_intraday_plan({"symbol": "SBIN", "date": "2026-10-28", "entry": 803.0,
                                       "stop": 800.25, "target": 808.5, "entry_time": "09:35"})
    # 09:33 IST: the 09:30 bar is still forming.
    early = datetime(2026, 10, 28, 4, 3, tzinfo=timezone.utc)
    with pytest.raises(tm.TMError, match="entry is not in the data"):
        tm.run_intraday(nse_rows(), plan, now=early)


def test_refuses_with_too_few_sessions():
    plan = tm.normalize_intraday_plan({"symbol": "SBIN", "date": "2026-10-28", "entry": 803.0,
                                       "stop": 800.25, "target": 808.5, "entry_time": "09:35"})
    with pytest.raises(tm.TMError, match="at least 20"):
        tm.run_intraday(nse_rows(n_days=5), plan,
                        now=datetime(2026, 10, 28, 12, tzinfo=timezone.utc))


@pytest.mark.parametrize("raw,msg", [
    ({"entry": 100, "stop": 101, "target": 103}, "stop < entry < target"),
    ({"entry": 100, "stop": 99, "target": 103, "entry_time": "09:33"}, "bar close"),
    ({"entry": 100, "stop": 99, "target": 103, "entry_time": "15:30"}, "leave bars"),
    ({"entry": 100, "stop": 99, "target": 103, "entry_time": "09:35", "or_minutes": 7},
     "multiple of 5"),
    ({"entry": "x", "stop": 99, "target": 103, "entry_time": "09:35"}, "number for entry"),
])
def test_plan_validation(raw, msg):
    with pytest.raises(tm.TMError, match=msg):
        tm.normalize_intraday_plan(dict(raw, symbol="SBIN", date="2026-10-28"))


def test_circuit_band_clamps_and_reports():
    pool = [[(1.0, 1.10, 0.90, 1.08)]] * 10    # every bar +8% close, wild range
    mc = tm.simulate(pool, 100.0, 95.0, 200.0, 0, 5, replays=50, seed=1,
                     band=(80.0, 120.0))
    assert mc["p_circuit"] == 1.0
    assert mc["median_close"] <= 120.0
    free = tm.simulate(pool, 100.0, 95.0, 200.0, 0, 5, replays=50, seed=1)
    assert free["median_close"] > 120.0 and free["p_circuit"] is None


def test_gap_through_stop_is_counted():
    pool = [[(0.90, 0.92, 0.88, 0.90)]] * 4    # every bar opens 10% lower
    mc = tm.simulate(pool, 100.0, 95.0, 110.0, 0, 3, replays=10)
    assert mc["p_stop"] == 1.0 and mc["p_gap_through_stop"] == 1.0
    assert any("worse than −1R" in n for n in tm.verdict_notes(mc, "3:15 PM"))


def test_verdict_thresholds():
    base = {"p_neither": 0.1, "avg_R": 0.1}
    assert tm.verdict(dict(base, p_target=0.5, p_stop=0.4, r_at_target=1.0), "x") \
        .startswith("Leans toward the target")
    assert tm.verdict(dict(base, p_target=0.2, p_stop=0.7, r_at_target=1.0), "x") \
        .startswith("Strongly toward the stop")
    two_r = tm.verdict(dict(base, p_target=0.24, p_stop=0.5, r_at_target=2.0), "x")
    assert two_r.startswith("Close to break-even")      # 32% vs 33% needed
    assert tm.verdict(dict(base, p_target=0, p_stop=0), "3:15 PM").startswith("No replay")


def test_swing_mode_runs_on_daily_bars_with_levels_history():
    import random

    rng = random.Random(3)
    closes, p = [], 1000.0
    for _ in range(400):
        p *= 1 + rng.gauss(0.0005, 0.015)
        closes.append(round(p, 2))
    end = date(2026, 10, 28)
    rows = daily_rows(end, closes)
    last = closes[-1]
    out = tm.run_swing(rows, {"symbol": "RELIANCE.NS", "date": "2026-10-28", "entry": last,
                              "stop": round(last * 0.96, 2), "target": round(last * 1.08, 2)},
                       horizon=10, replays=2000)
    mc, h = out["mc"], out["history"]
    assert out["mode"] == "swing" and mc["bars_left"] == 10
    assert mc["r_at_target"] == 2.0
    assert h["rule"]["setup"] == "levels" and h["triggered"] > 100
    assert h["target"] + h["stop"] + h["close"] == h["triggered"]
    assert "gap_through_stop" in h


def test_swing_mode_uses_last_session_on_or_before_date():
    closes = [100 + i * 0.1 for i in range(200)]
    end = date(2026, 10, 30)  # Friday
    rows = daily_rows(end, closes)
    out = tm.run_swing(rows, {"symbol": "X", "date": "2026-11-01", "entry": 119.9,
                              "stop": 115, "target": 125}, horizon=5, replays=200)
    assert out["plan"]["date"] == "2026-10-30" and out["warnings"]
    with pytest.raises(tm.TMError, match="at least 120"):
        tm.run_swing(daily_rows(end, closes[:50]), {"symbol": "X", "date": "2026-10-30",
                                                    "entry": 104.9, "stop": 100,
                                                    "target": 110}, replays=10)
