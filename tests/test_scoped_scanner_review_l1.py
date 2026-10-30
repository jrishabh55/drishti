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


def test_universe_skips_unknown_instruments_and_reports_names(env, tmp_path, monkeypatch):
    client, _, _, _, _, _ = prepared(env, tmp_path, monkeypatch)
    async def hold(*args, **kwargs):
        await asyncio.Event().wait()
    monkeypatch.setattr(live.ticker, 'run_ticker', hold)
    response = client.post('/api/kite/live/start', json={'universe': 'nifty500'})
    assert response.status_code == 200, response.text
    assert live.get_hub('user_a').symbols == ['SBIN']
    assert any('TCS' in n and 'skipped' in n.lower() for n in response.json()['selection_notes'])
    custom = client.post('/api/kite/live/start', json={'symbols': ['SBIN', 'TCS']})
    assert custom.status_code == 422 and 'TCS' in custom.json()['detail']


def test_universe_fallback_filters_before_cap_and_discloses_all_skips(env, tmp_path, monkeypatch):
    client, _, _, _, store, _ = prepared(env, tmp_path, monkeypatch)
    symbols = [f'S{i:03}' for i in range(510)]
    store.upsert_instruments([{'symbol': s, 'yf_symbol': s + '.NS', 'index_memberships': []} for s in symbols])
    store.upsert_instruments([{'symbol': s, 'yf_symbol': s + '.NS', 'index_memberships': []} for s in ['SBIN', 'TCS']])
    valid = symbols[5:]  # unknowns sort before the valid members; filter first
    monkeypatch.setattr(live.rest, 'nse_instruments', lambda *a: {s: {'token': i} for i, s in enumerate(valid)})
    async def hold(*args, **kwargs):
        await asyncio.Event().wait()
    monkeypatch.setattr(live.ticker, 'run_ticker', hold)
    response = client.post('/api/kite/live/start', json={'universe': 'fno'})
    assert response.status_code == 200, response.text
    assert live.get_hub('user_a').symbols == valid[:500]
    notes = ' '.join(response.json()['selection_notes'])
    for skipped in [*symbols[:5], *valid[500:], 'SBIN', 'TCS']:
        assert skipped in notes
    assert '500' in notes


def test_universe_with_no_kite_members_fails_without_replacing_hub(env, tmp_path, monkeypatch):
    client, _, _, _, _, hub = prepared(env, tmp_path, monkeypatch)
    monkeypatch.setattr(live.rest, 'nse_instruments', lambda *a: {})
    response = client.post('/api/kite/live/start', json={'universe': 'nifty500'})
    assert response.status_code == 422 and 'Kite' in response.json()['detail']
    assert live.get_hub('user_a') is hub
