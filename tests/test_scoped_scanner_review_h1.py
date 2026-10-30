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


@pytest.mark.parametrize('interval', ['1d', '5m'])
def test_bootstrap_forming_candle_never_matures_by_clock_alone(env, tmp_path, monkeypatch, interval):
    _, _, _, clock, _, hub = prepared(env, tmp_path, monkeypatch)
    clock['now'] = ist(10, 2)
    previous = ist(9, 15) - timedelta(days=1) if interval == '1d' else ist(9, 55)
    forming = ist(9, 15) if interval == '1d' else ist(10, 0)

    class Client:
        def historical(self, token, tf, start, end):
            if tf != ('day' if interval == '1d' else '5minute'):
                return []
            # Simulate a response crossing the candle close. The request end
            # still bounds the data, even if the response arrives after close.
            clock['now'] = ist(15, 31) if interval == '1d' else ist(10, 6)
            return [[ts.isoformat(), 100, 130, 99, close, 1000]
                    for ts, close in [(previous, 105), (forming, 120)]]

    asyncio.run(live._bootstrap_history(hub, Client(), {'SBIN': {'token': 1}}, lambda: clock['now']))
    if interval == '1d':
        hub.builder.last_tick['SBIN'] = clock['now']
        got = engine.run_for_user('user_a', definition(), now=clock['now'])
        assert got['matches'][0]['close'] == 105
    assert [r[4] for r in hub.history[interval]['SBIN']] == [105]


