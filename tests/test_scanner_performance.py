"""Scanner requests must reuse published panels, even with a Kite connection."""
from datetime import timedelta
import asyncio

import pytest
from pandas.testing import assert_frame_equal

from apps.api.kite import live
from apps.api.scanner import engine
from tests.test_kite_connection import env, ist
from tests.test_scoped_scanner_data import prepared
from tests.test_scoped_scanner_review_m2 import definition
from tests.scanner_utils import bars_long
from apps.api.scanner.schema import parse_definition


@pytest.mark.parametrize('state,stale', [('stopped', False), ('starting', False), ('live', True)])
def test_inactive_feed_uses_published_panels_without_bar_reads(env, tmp_path, monkeypatch, state, stale):
    _, _, _, clock, store, hub = prepared(env, tmp_path, monkeypatch)
    hub.state = state
    if stale:
        hub.builder.last_tick['SBIN'] = clock['now'] - timedelta(seconds=91)
    engine.get_engine().warm()
    def cold_read(*args, **kwargs):
        pytest.fail('scanner reread the full candle table despite warmed public panels')
    monkeypatch.setattr(store, 'load_bars', cold_read)
    for _ in range(2):
        got = engine.run_for_user('user_a', definition('1d', '5m'), now=clock['now'])
        assert [m['symbol'] for m in got['matches']] == ['TCS']
        assert got['source'] == 'delayed:yahoo'


def test_private_overlay_uses_published_panels_and_bounded_access_checks(env, tmp_path, monkeypatch):
    _, _, _, clock, store, hub = prepared(env, tmp_path, monkeypatch)
    hub.history = {'1d': {'SBIN': [(ist(9, 15) - timedelta(days=1), 100., 110., 99., 105., 1000.)]}}
    shared = engine.get_engine()
    shared.warm()
    original = shared._panels['1d'].close.copy(deep=True)
    access = live.access_valid
    checks = []
    def check(*args):
        checks.append(args)
        return access(*args)
    def cold_read(*args, **kwargs):
        pytest.fail('private overlay reread the public candle table')
    monkeypatch.setattr(store, 'load_bars', cold_read)
    monkeypatch.setattr(live, 'access_valid', check)
    got = engine.run_for_user('user_a', definition('1d', '5m'), now=clock['now'])
    assert [m['symbol'] for m in got['matches']] == ['SBIN']
    match = next(m for m in got['matches'] if m['symbol'] == 'SBIN')
    assert match['close'] == 105.
    assert match['values']['close [5m]'] == 103.
    assert match['source'] == 'live:kite'
    assert len(checks) == 2  # authorize the snapshot; recheck before returning it
    assert_frame_equal(shared._panels['1d'].close, original)


@pytest.mark.parametrize('tf', ['1w', '1mo'])
def test_active_hub_without_private_history_still_supports_period_scans(env, tmp_path, monkeypatch, tf):
    _, _, _, clock, store, hub = prepared(env, tmp_path, monkeypatch)
    # The fixture has a live session but no bootstrapped daily history.
    engine.get_engine().warm()
    def cold_read(*args, **kwargs):
        pytest.fail('period scan reread the candle table')
    monkeypatch.setattr(store, 'load_bars', cold_read)
    got = engine.run_for_user('user_a', definition(tf), now=clock['now'])
    assert got['source'] == 'delayed:yahoo'
    assert got['universe'] == 2


def test_startup_serves_existing_panels_before_network_catchup(env, tmp_path, monkeypatch):
    from apps.api.scanner import ingest, universe
    _, _, _, clock, store, _ = prepared(env, tmp_path, monkeypatch)
    live._hubs.clear()
    monkeypatch.setattr(universe, 'apply_fo_membership', lambda *a: None)
    class StopProbe(Exception):
        pass
    def network_catchup(*args, **kwargs):
        def cold_read(*args, **kwargs):
            pytest.fail('the first user scan during startup must not rebuild existing panels')
        monkeypatch.setattr(store, 'load_bars', cold_read)
        got = engine.run_for_user('unconnected', definition('1d', '5m'), now=clock['now'])
        assert [m['symbol'] for m in got['matches']] == ['TCS']
        raise StopProbe
    monkeypatch.setattr(ingest, 'refresh_timeframe', network_catchup)
    with pytest.raises(StopProbe):
        asyncio.run(ingest.ingest_loop())


@pytest.mark.parametrize('completed', [0, 1])
@pytest.mark.parametrize('connected', [False, True])
def test_streak_needs_all_requested_completed_bars(env, tmp_path, monkeypatch, completed, connected):
    _, _, _, clock, store, hub = prepared(env, tmp_path, monkeypatch)
    if not connected:
        live._hubs.clear()
    store.upsert_bars('1h', bars_long('SBIN', [20000.], start=ist(9, 15).isoformat()))
    if completed:
        store.upsert_bars('1h', bars_long('SBIN', [20000.],
                                        start=(ist(14, 15) - timedelta(days=1)).isoformat()))
    scan = parse_definition({'logic': 'AND', 'children': [
        {'timeframe': '1h', 'left': {'field': 'close'}, 'op': '>',
         'right': {'const': 9999}, 'for_n_bars': 2}]})
    got = engine.run_for_user('user_a', scan, now=clock['now'])
    assert got['matches'] == []
