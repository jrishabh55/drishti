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


@pytest.mark.parametrize('state,stale,backfilled', [('stopped', False, True), ('live', True, True),
                                                 ('live', False, False)])
def test_daily_requires_fresh_backfilled_stream(env, tmp_path, monkeypatch, state, stale, backfilled):
    _, _, _, clock, store, hub = prepared(env, tmp_path, monkeypatch)
    clock['now'] = ist(16, 30)
    hub.state = state
    hub.builder.last_tick['SBIN'] = clock['now'] - timedelta(seconds=91 if stale else 0)
    hub.backfilled = {'SBIN'} if backfilled else set()
    hub.history = {'1d': {'SBIN': [(ist(9, 15) - timedelta(days=1), 100, 110, 99, 105, 1000)]}}
    store.upsert_bars('1d', bars_long('SBIN', [150.], start=ist(9, 15).isoformat()))
    got = engine.run_for_user('user_a', definition(), now=clock['now'])
    match = next(m for m in got['matches'] if m['symbol'] == 'SBIN')
    assert match['close'] == 150
    assert match['source'] == 'delayed:yahoo'
    if state != 'live' or stale:
        assert 'quote' not in match
    assert got['coverage']['by_timeframe']['1d']['kite'] == 0


def test_newer_public_daily_close_wins_even_with_fresh_private_stream(env, tmp_path, monkeypatch):
    _, _, _, clock, store, hub = prepared(env, tmp_path, monkeypatch)
    clock['now'] = ist(15, 31)
    hub.builder.last_tick['SBIN'] = clock['now']
    hub.history = {'1d': {'SBIN': [(ist(9, 15) - timedelta(days=1), 100, 110, 99, 105, 1000)]}}
    store.upsert_bars('1d', bars_long('SBIN', [150.], start=ist(9, 15).isoformat()))
    got = engine.run_for_user('user_a', definition(), now=clock['now'])
    match = next(m for m in got['matches'] if m['symbol'] == 'SBIN')
    assert match['close'] == 150 and match['source'] == 'delayed:yahoo'
    assert match['quote_as_of'] == clock['now'].isoformat()  # distinct fresh quote


