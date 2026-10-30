"""Controlled regressions for the independent Task 1 review; no provider I/O."""
import asyncio
from datetime import timedelta

import pandas as pd
import pytest

from apps.api.kite import live
from apps.api.scanner import engine
from apps.api.scanner.schema import parse_definition
from tests.scanner_utils import bars_long
from tests.test_kite_connection import env, ist
from tests.test_scoped_scanner_data import prepared


def definition(*timeframes):
    return parse_definition({'logic': 'AND', 'children': [
        {'timeframe': tf, 'left': {'field': 'close'}, 'op': '>', 'right': {'const': 1}}
        for tf in (timeframes or ('1d',))]})


@pytest.mark.parametrize('user_id', ['unconnected', 'user_a'])
def test_public_scans_reuse_warmed_panels_without_bar_reads(env, tmp_path, monkeypatch, user_id):
    _, _, _, clock, store, _ = prepared(env, tmp_path, monkeypatch)
    live._hubs.clear()
    shared = engine.get_engine()
    shared.warm()
    def cold_read(*args, **kwargs):
        pytest.fail('a warmed public scan must not load/pivot long bars again')
    monkeypatch.setattr(store, 'load_bars', cold_read)
    for _ in range(2):
        got = engine.run_for_user(user_id, definition('1d', '5m'), now=clock['now'])
        assert [m['symbol'] for m in got['matches']] == ['TCS']
        assert got['source'] == 'delayed:yahoo'


def test_public_scans_keep_published_version_until_ingest_bump(env, tmp_path, monkeypatch):
    _, _, _, clock, store, _ = prepared(env, tmp_path, monkeypatch)
    shared = engine.get_engine()
    shared.warm()
    store.upsert_bars('1d', bars_long('SBIN', [150.], start='2026-01-06'))
    got = engine.run_for_user('unconnected', definition(), now=clock['now'])
    assert next(m for m in got['matches'] if m['symbol'] == 'SBIN')['close'] == 90
    store.bump_bar_version()
    got = engine.run_for_user('unconnected', definition(), now=clock['now'])
    assert next(m for m in got['matches'] if m['symbol'] == 'SBIN')['close'] == 150


def test_cached_public_completion_view_does_not_mutate_legacy_engine(env, tmp_path, monkeypatch):
    _, _, _, clock, store, _ = prepared(env, tmp_path, monkeypatch)
    for tf in ('1d', '5m', '15m', '1h'):
        stamp = '2026-10-28T09:25:00' if tf == '1d' else ist(9, 25).isoformat()
        store.upsert_bars(tf, bars_long('SBIN', [999.], start=stamp))
    shared = engine.get_engine()
    shared.warm()
    for tf in ('1d', '5m', '15m', '1h', '1w', '1mo'):
        got = engine.run_for_user('unconnected', definition(tf), now=clock['now'])
        assert all(m['values'][f'close [{tf}]'] != 999 for m in got['matches'])
    assert next(m for m in shared.run(definition())['matches'] if m['symbol'] == 'SBIN')['close'] == 999


def test_scoped_timestamp_processing_is_vectorized(env, tmp_path, monkeypatch):
    _, _, _, clock, _, _ = prepared(env, tmp_path, monkeypatch)
    original = pd.Series.map
    def no_timestamp_map(series, *args, **kwargs):
        assert series.name != 'ts', 'bar timestamp parsing must not call Python per row'
        return original(series, *args, **kwargs)
    monkeypatch.setattr(pd.Series, 'map', no_timestamp_map)
    engine.run_for_user('user_a', definition('1d', '5m'), now=clock['now'])


def test_public_scan_pins_panels_through_mid_evaluation_publication(env, tmp_path, monkeypatch):
    _, _, _, clock, store, _ = prepared(env, tmp_path, monkeypatch)
    shared = engine.get_engine()
    shared.warm()
    original = engine.ScanEngine._condition
    changed = []
    def publish(self, condition, panels):
        if not changed:
            changed.append(True)
            store.upsert_bars('1d', bars_long('TCS', [777.], start='2026-01-06'))
            store.upsert_bars('5m', bars_long('TCS', [777.], start=ist(9, 15).isoformat()))
            store.bump_bar_version()
            shared.warm()
        return original(self, condition, panels)
    monkeypatch.setattr(engine.ScanEngine, '_condition', publish)
    got = engine.run_for_user('unconnected', definition('1d', '5m'), now=clock['now'])
    assert got['matches'][0]['close'] == 90
    assert got['matches'][0]['values']['close [5m]'] == 90
    got = engine.run_for_user('unconnected', definition('1d', '5m'), now=clock['now'])
    assert got['matches'][0]['close'] == 777
    assert got['matches'][0]['values']['close [5m]'] == 777


