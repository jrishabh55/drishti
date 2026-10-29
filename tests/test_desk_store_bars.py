"""Trade Desk store, bar conversions, history cache and the bar seam."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from apps.api.desk import bars as bars_mod, bars_source
from apps.api.desk.market.session import NSE, NYSE
from apps.api.desk.store import SCHEMA_VERSION, reset_desk_store_for_tests
from tests.desk_utils import random_walk_sessions, session_rows, upstream_tsla_result
from tests.scanner_utils import make_store


@pytest.fixture()
def store(tmp_path):
    s = reset_desk_store_for_tests(tmp_path / "desk.sqlite")
    yield s
    bars_source.clear_live_providers_for_tests()


def test_store_schema_and_per_user_isolation(store):
    assert store.schema_version() == SCHEMA_VERSION
    plan = store.create_plan("a", {"symbol": "RELIANCE", "entry": 1.0})
    assert store.get_plan(plan["id"], "a")["symbol"] == "RELIANCE"
    assert store.get_plan(plan["id"], "b") is None
    assert not store.update_plan(plan["id"], "b", {"symbol": "X"})
    assert not store.delete_plan(plan["id"], "b")
    assert store.list_plans("b") == []
    store.put_profile("a", {"capital": 1})
    assert store.get_profile("a") == {"capital": 1} and store.get_profile("b") is None
    store.add_watch("a", "SBIN")
    store.add_watch("a", "SBIN")
    assert store.watchlist("a") == ["SBIN"] and store.watchlist("b") == []


def test_symbol_normalisation():
    assert bars_mod.nse_symbol("reliance.ns") == "RELIANCE"
    assert bars_mod.nse_symbol("NSE:M&M") == "M&M"
    assert bars_mod.yf_symbol("BAJAJ-AUTO") == "BAJAJ-AUTO.NS"
    with pytest.raises(bars_mod.BarsError):
        bars_mod.nse_symbol("DROP TABLE")


def test_session_bars_drop_rules_on_nse():
    d = date(2026, 10, 28)
    rows = session_rows(d, [100.0] * 75)
    pre = (NSE.local(d, 9 * 60 + 10), 1, 1, 1, 1, 1)                       # pre-open
    tick = (NSE.local(d, 10 * 60) + timedelta(seconds=40), 1, 1, 1, 1, 1)   # live tick
    hole = (NSE.local(d, 11 * 60), None, 1, 1, 1, 1)
    as_of = NSE.local(d, 10 * 60 + 2)   # the 09:55 bar closed at 10:00; 10:00 bar forming
    bars, dropped = bars_mod.session_bars(rows + [pre, tick, hole], NSE, as_of)
    assert len(bars) == 9 and bars[-1]["start"] == "09:55"
    assert dropped["outside_hours"] == 1 and dropped["off_grid"] == 1
    assert dropped["no_price"] == 1 and dropped["not_complete"] == 75 - 9


def test_full_sessions_leave_out_short_days():
    rows = random_walk_sessions(date(2026, 10, 27), 3)
    d_short = date(2026, 10, 28)
    rows += session_rows(d_short, [100.0] * 40)  # a short day
    rows += session_rows(date(2026, 10, 29), [100.0] * 75)  # the plan day itself
    bars, _ = bars_mod.session_bars(rows, NSE)
    full, short = bars_mod.full_sessions(bars_mod.split_sessions(bars), "2026-10-29", NSE)
    assert len(full) == 3 and short == [{"date": "2026-10-28", "bars": 40}]


def test_upstream_fixture_parses_with_nyse_spec():
    rows = bars_mod.rows_from_yahoo_chart(upstream_tsla_result())
    bars, _ = bars_mod.session_bars(rows, NYSE, datetime(2026, 10, 19, 20, 30, tzinfo=timezone.utc))
    full, _ = bars_mod.full_sessions(bars_mod.split_sessions(bars), "2026-10-19", NYSE)
    assert len(full) == 59


def _yf_frame(n=3):
    idx = pd.date_range("2026-10-28 09:15", periods=n, freq="5min", tz=NSE.tz)
    return pd.DataFrame({"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5,
                         "Volume": 10.0}, index=idx)


def test_fetch_history_caches_and_serves_stale_on_error(store, monkeypatch):
    calls = []

    def fake_download(sym, interval, start):
        calls.append((sym, interval))
        return _yf_frame()

    monkeypatch.setattr(bars_mod, "_download", fake_download)
    # Sunday: market closed, so a fresh fetch stays fresh.
    now = datetime(2026, 10, 25, 6, 0, tzinfo=timezone.utc)
    rows = bars_mod.fetch_history("SBIN", "5m", now=now, store=store)
    assert len(rows) == 3 and calls == [("SBIN.NS", "5m")]
    bars_mod.fetch_history("SBIN", "5m", now=now + timedelta(minutes=5), store=store)
    assert len(calls) == 1  # cache hit

    def boom(*a):
        raise RuntimeError("yahoo down")

    monkeypatch.setattr(bars_mod, "_download", boom)
    later = now + timedelta(days=2)  # stale -> refetch fails -> cache served
    assert len(bars_mod.fetch_history("SBIN", "5m", now=later, store=store)) == 3
    with pytest.raises(bars_mod.BarsError):
        bars_mod.fetch_history("INFY", "5m", now=later, store=store)


def test_bar_seam_prefers_live_provider_then_falls_back(store, tmp_path):
    scan = make_store(tmp_path, None)
    d = date(2026, 10, 28)
    long = pd.DataFrame([{"symbol": "SBIN", "ts": r[0].isoformat(), "open": r[1], "high": r[2],
                          "low": r[3], "close": r[4], "volume": r[5]}
                         for r in session_rows(d, [800.0, 801.0])])
    scan.upsert_bars("5m", long)
    s = bars_source.get_session_rows("u1", "SBIN")
    assert s.source == "delayed:yahoo" and len(s.rows) == 2

    def live(user_id, symbol, interval, kind):
        if user_id != "u1":
            return None
        return bars_source.BarSeries(symbol, interval, [], "live:test", None)

    bars_source.register_live_provider(live)
    assert bars_source.get_session_rows("u1", "SBIN").source == "live:test"
    assert bars_source.get_session_rows("u2", "SBIN").source == "delayed:yahoo"

    def broken(*a):
        raise RuntimeError("socket gone")

    bars_source.clear_live_providers_for_tests()
    bars_source.register_live_provider(broken)
    assert bars_source.get_session_rows("u1", "SBIN").source == "delayed:yahoo"
    assert "no 5m bars" in bars_source.get_session_rows("u1", "INFY").notes[0]
