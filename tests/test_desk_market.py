"""Trade Desk market primitives: NSE session clock, tick rounding, charges."""
from __future__ import annotations

from datetime import date

import pytest

from apps.api.desk.market import charges, ticks
from apps.api.desk.market.session import NSE, NYSE, clock12, clock24, parse_clock


def test_nse_session_has_75_five_minute_bars():
    assert NSE.n_bars == 75
    assert clock24(NSE.bar_start_min(0)) == "09:15"
    assert clock24(NSE.bar_close_min(74)) == "15:30"
    assert NYSE.n_bars == 78


def test_slot_and_entry_bar_math():
    d = date(2026, 10, 28)
    assert NSE.slot_of(NSE.local(d, 9 * 60 + 15)) == 0
    assert NSE.slot_of(NSE.local(d, 9 * 60 + 30)) == 3
    assert NSE.slot_of(NSE.local(d, 9 * 60 + 12)) is None      # pre-open
    assert NSE.slot_of(NSE.local(d, 15 * 60 + 30)) is None     # the close itself
    assert NSE.bar_for_close(parse_clock("09:35")) == 3        # bar 3 closes 09:35
    with pytest.raises(ValueError):
        NSE.bar_for_close(parse_clock("09:33"))


def test_parse_clock_forms():
    assert parse_clock("10:15 AM") == 615
    assert parse_clock("3:20 pm") == 15 * 60 + 20
    assert parse_clock("12:05 AM") == 5
    assert parse_clock("15:10") == 910
    assert clock12(615) == "10:15 AM"
    with pytest.raises(ValueError):
        parse_clock("25:00")


def test_session_uses_nse_holidays():
    assert not NSE.is_trading_day(date(2026, 10, 2))   # Gandhi Jayanti
    assert NSE.is_trading_day(date(2026, 10, 1))
    assert not NSE.is_trading_day(date(2026, 10, 3))   # Saturday


@pytest.mark.parametrize("price,tick", [(99.99, 0.01), (249.99, 0.01), (250, 0.05),
                                        (999, 0.05), (1500, 0.10), (7000, 0.5),
                                        (15000, 1.0), (30000, 5.0)])
def test_tick_table(price, tick):
    assert ticks.tick_size(price) == tick


def test_round_away_moves_stops_away_from_entry():
    assert ticks.round_away(358.324, long=True) == 358.30       # 0.05 tick, down
    assert ticks.round_away(358.30, long=True) == 358.30        # already on tick
    assert ticks.round_away(358.32, long=False) == 358.35       # short: up
    assert ticks.round_away(120.005, long=True) == 120.0        # 0.01 tick
    assert ticks.round_away(358.32000000001, long=True) == 358.30
    assert ticks.round_to_tick(1234.56) == 1234.6


def test_charge_estimate_matches_hand_worked_intraday_example():
    # 100 shares bought at 500, sold at 505, intraday, default broker plan.
    b = charges.estimate(50_000, 50_500, "MIS")
    assert b.brokerage == pytest.approx(15 + 15.15)
    assert b.stt == pytest.approx(12.625)
    assert b.exchange_txn == pytest.approx(2.98485)
    assert b.sebi_fee == pytest.approx(0.1005)
    assert b.stamp_duty == pytest.approx(1.5)
    assert b.gst == pytest.approx(0.18 * (30.15 + 2.98485 + 0.1005))
    assert b.total == pytest.approx(53.34, abs=0.01)


def test_brokerage_is_capped_at_flat_fee():
    b = charges.estimate(1_000_000, 1_000_000, "MIS")
    assert b.brokerage == 40.0


def test_delivery_charges_stt_both_sides_and_dp():
    plan = charges.Brokerage(dp_charge_per_sell=15.34)
    b = charges.estimate(100_000, 101_000, "CNC", plan)
    assert b.brokerage == 0
    assert b.stt == pytest.approx(0.001 * 201_000)
    assert b.stamp_duty == pytest.approx(15.0)
    assert b.dp_charge == 15.34


def test_round_trip_cost_short_side_swaps_turnover():
    long_cost = charges.round_trip_cost(500, 505, 100, side="buy")
    short_cost = charges.round_trip_cost(505, 500, 100, side="sell")
    assert long_cost == pytest.approx(short_cost)


def test_schedule_versioning_picks_latest_effective():
    assert charges.schedule_for(date(2026, 1, 1)).effective_from == "2024-10-01"
    with pytest.raises(ValueError):
        charges.estimate(-1, 0)
