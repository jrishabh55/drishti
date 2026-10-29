"""Regression coverage for a Desk deployed after market close with an old scanner DB."""
import asyncio
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from apps.api.desk import bars as bars_mod, bars_source, orb
from apps.api.desk.market.session import NSE
from apps.api.desk.store import reset_desk_store_for_tests
from apps.api.scanner import ingest
from tests.desk_utils import session_rows
from tests.scanner_utils import make_store

NOW = NSE.local(date(2026, 10, 29), 16*60)


def long(rows, symbol='RELIANCE'):
    return pd.DataFrame([dict(symbol=symbol,ts=r[0].isoformat(),open=r[1],high=r[2],low=r[3],close=r[4],volume=r[5]) for r in rows])


def test_deploy_after_close_refreshes_stale_intraday_even_when_daily_cache_exists(tmp_path, monkeypatch):
    store = make_store(tmp_path, {'RELIANCE':[100.0]})
    store.upsert_bars('5m', long(session_rows(date(2026,10,9), [100.0]*75)))
    monkeypatch.setattr(ingest,'get_scanner_store',lambda:store)
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return NOW.astimezone(tz or timezone.utc)
    monkeypatch.setattr(ingest,'datetime',Clock)
    refreshed = []
    def refresh(s, tf, **kw):
        refreshed.append(tf)
        s.upsert_bars(tf, long(session_rows(date(2026,10,29), [100.0]*75)))
        return 75
    monkeypatch.setattr(ingest,'refresh_timeframe',refresh)
    monkeypatch.setattr(ingest,'_publish_and_warm',lambda:None)
    async def stop(_):
        raise asyncio.CancelledError()
    monkeypatch.setattr(ingest.asyncio,'sleep',stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ingest.ingest_loop())
    assert store.latest_ts('5m').startswith('2026-10-29')
    assert '1d' in refreshed  # stale daily data must not keep liquidity filters weeks behind


def test_watched_symbol_refreshes_stale_cache_and_persists_new_bars(tmp_path, monkeypatch):
    reset_desk_store_for_tests(tmp_path/'desk.sqlite')
    store=make_store(tmp_path,{'RELIANCE':[100.0]})
    store.upsert_bars('5m',long(session_rows(date(2026,10,9),[100.0]*75)))
    new=session_rows(date(2026,10,29),[100.0]*75)
    monkeypatch.setattr(bars_mod,'fetch_history',lambda *a,**kw:new)
    series=bars_source.get_session_rows('u1','RELIANCE',now=NOW)
    assert series.data_as_of.startswith('2026-10-29')
    assert store.latest_ts('5m').startswith('2026-10-29')
    assert not series.notes


def test_failed_symbol_refresh_explains_stale_data_without_inventing_bars(tmp_path,monkeypatch):
    reset_desk_store_for_tests(tmp_path/'desk.sqlite')
    make_store(tmp_path,{'RELIANCE':[100.0]})
    def unavailable(*a,**kw):
        raise bars_mod.BarsError('Yahoo unavailable')
    monkeypatch.setattr(bars_mod,'fetch_history',unavailable)
    series=bars_source.get_session_rows('u1','RELIANCE',now=NOW)
    assert not series.rows
    assert series.data_as_of is None
    assert any('Yahoo unavailable' in note for note in series.notes)


def test_orb_data_timestamp_is_actual_bar_time_not_the_requested_cutoff():
    doc=orb.scan({},NOW,symbols=['RELIANCE'])
    assert doc['data_as_of'] is None
    doc=orb.scan({'RELIANCE':session_rows(date(2026,10,29),[100.0]*75)},NOW)
    assert doc['data_as_of'].startswith('2026-10-29T15:25')


def test_orb_cache_reads_only_the_requested_session_and_universe(tmp_path):
    store=make_store(tmp_path,{'RELIANCE':[100.0],'TCS':[100.0]})
    store.upsert_bars('5m',long(session_rows(date(2026,10,9),[100.0]*75)))
    store.upsert_bars('5m',long(session_rows(date(2026,10,29),[100.0]*75)))
    store.upsert_bars('5m',long(session_rows(date(2026,10,29),[100.0]*75),'TCS'))
    rows=bars_source.get_session_rows_universe('u1',day=date(2026,10,29),symbols=['RELIANCE'])
    assert set(rows)=={'RELIANCE'}
    assert len(rows['RELIANCE'])==75


@pytest.mark.parametrize(('now', 'latest', 'stale'), [
    (NSE.local(date(2026,10,29), 8*60), '2026-10-28T15:25:00+05:30', False),
    (NSE.local(date(2026,10,29), 12*60), '2026-10-29T11:35:00+05:30', False),
    (NOW, '2026-10-29T15:00:00+05:30', True),
    (NOW, '2026-10-29T15:25:00+05:30', False),
    (NSE.local(date(2026,11,1), 12*60), '2026-10-30T15:25:00+05:30', False),
])
def test_intraday_freshness_respects_session_and_vendor_delay(now, latest, stale):
    assert ingest.intraday_stale(latest, now) is stale


def test_fresh_watched_symbol_does_not_download_again(tmp_path, monkeypatch):
    store=make_store(tmp_path, {'RELIANCE':[100.0]})
    store.upsert_bars('5m',long(session_rows(date(2026,10,29),[100.0]*75)))
    def unexpected_download(*a, **kw):
        pytest.fail('fresh scanner cache should be used')
    monkeypatch.setattr(bars_mod, 'fetch_history', unexpected_download)
    assert len(bars_source.get_session_rows('u1','RELIANCE',now=NOW).rows) == 75


def test_history_cache_does_not_freeze_partial_after_close_snapshot():
    fetched = NSE.local(date(2026,10,29), 15*60+35)
    assert not bars_mod._fresh(fetched.isoformat(), '5m', NOW)
    fetched = NSE.local(date(2026,10,29), 16*60)
    assert bars_mod._fresh(fetched.isoformat(), '5m', NSE.local(date(2026,10,29), 17*60))


def test_one_fresh_watched_symbol_cannot_suppress_universe_catchup(tmp_path, monkeypatch):
    store = make_store(tmp_path, {'RELIANCE': [100.0], 'TCS': [100.0]})
    store.upsert_bars('5m', long(session_rows(NOW.date(), [100.0]*75)))
    for tf in ('15m', '1h'):
        store.upsert_bars(tf, long(session_rows(date(2026,10,9), [100.0])))
    monkeypatch.setattr(ingest, 'get_scanner_store', lambda: store)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz or timezone.utc)
    monkeypatch.setattr(ingest, 'datetime', Clock)
    calls = []
    def refresh(s, tf, **kw):
        calls.append(tf)
        return 1
    monkeypatch.setattr(ingest, 'refresh_timeframe', refresh)
    monkeypatch.setattr(ingest, '_publish_and_warm', lambda: None)
    async def stop(_):
        raise asyncio.CancelledError()
    monkeypatch.setattr(ingest.asyncio, 'sleep', stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ingest.ingest_loop())
    assert {'5m', '15m', '1h'} <= set(calls)


def test_final_after_close_sweep_runs_even_when_one_bar_is_current(tmp_path, monkeypatch):
    store = make_store(tmp_path, {'RELIANCE': [100.0]})
    store.upsert_bars('5m', long(session_rows(NOW.date(), [100.0]*75)))
    clock = {'now': NSE.local(NOW.date(), 15*60+50), 'sleeps': 0}
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock['now'].astimezone(tz or timezone.utc)
    monkeypatch.setattr(ingest, 'datetime', Clock)
    monkeypatch.setattr(ingest, 'get_scanner_store', lambda: store)
    monkeypatch.setattr(ingest, 'INTRADAY_REFRESH_SECONDS', 0)
    monkeypatch.setattr(ingest, '_publish_and_warm', lambda: None)
    calls = []
    def refresh(s, tf, **kw):
        calls.append(tf)
        return 1
    monkeypatch.setattr(ingest, 'refresh_timeframe', refresh)
    async def tick(_):
        clock['sleeps'] += 1
        clock['now'] = NSE.local(NOW.date(), 16*60+1)
        if clock['sleeps'] > 1:
            raise asyncio.CancelledError()
    monkeypatch.setattr(ingest.asyncio, 'sleep', tick)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ingest.ingest_loop())
    assert calls.count('5m') == 2  # startup + final delayed-data sweep
