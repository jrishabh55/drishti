"""ORB liquidity reuses public scanner panels without rescanning candle SQL."""
from datetime import UTC

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from apps.api.desk import orb
from apps.api.scanner import engine
from apps.api.scanner.store import get_scanner_store
from tests import test_desk_intraday
from tests.scanner_utils import bars_long, make_store

orb_api = test_desk_intraday.api


@pytest.mark.parametrize('query', ['', '?at=09:55', '?date=2026-10-28&at=09:55'])
def test_warm_orb_keeps_response_without_full_candle_reads(orb_api, monkeypatch, query):
    client, _ = orb_api
    store = get_scanner_store()
    # BRK's average is 109.5 * 1e6 / 1e7 = 10.95 crore. The outlier
    # precedes the last 20. WICK's own history is older than every BRK date.
    store.upsert_bars('1d', bars_long('BRK', [999.] + list(range(100, 120)),
                                    start='2026-10-01', volumes=[1e6] * 21))
    store.upsert_bars('1d', bars_long('WICK', [200.] * 25,
                                    start='2026-01-01', volumes=[1e6] * 25))
    client.put('/api/desk/profile', json={'min_avg_value_cr': 10, 'min_price': 0})
    baseline = client.get('/api/desk/orb' + query)
    assert baseline.status_code == 200, baseline.text
    expected = baseline.json()
    assert expected['counts']['illiquid'] == 0
    assert expected['counts']['scanned'] == 2
    assert expected['hits'][0]['avg_value_cr'] == 10.95
    assert expected['source'] == 'delayed:yahoo'
    engine.get_engine().warm()

    def cold_read(*args, **kwargs):
        pytest.fail('ORB reread the full candle table despite warmed public panels')

    monkeypatch.setattr(store, 'load_bars', cold_read)
    for _ in range(2):
        response = client.get('/api/desk/orb' + query)
        assert response.status_code == 200, response.text
        assert response.json() == expected


def test_public_daily_snapshot_retains_each_symbols_last_21_rows(tmp_path, monkeypatch):
    from apps.api.desk.scanner_snapshot import public_daily_bars

    store = make_store(tmp_path)
    stale = bars_long('STALE', list(range(25)), start='2026-01-01', volumes=[1e7] * 25)
    fresh = bars_long('FRESH', list(range(100, 125)), start='2026-10-01')
    zero = bars_long('ZERO', [100.], start='2026-10-01', volumes=[0.])
    # A row missing close/volume still occupies one of the last 21 sessions.
    stale.loc[24, ['close', 'volume']] = float('nan')
    store.upsert_bars('1d', pd.concat([stale, fresh, zero]))
    shared = engine.get_engine()
    shared.warm()
    original = shared._panels['1d'].close.copy(deep=True)

    def cold_read(*args, **kwargs):
        pytest.fail('public daily adapter reread candle SQL')

    monkeypatch.setattr(store, 'load_bars', cold_read)
    daily = public_daily_bars(['STALE', 'ZERO', 'MISSING', 'STALE'])
    assert list(daily.columns) == ['symbol', 'ts', 'open', 'high', 'low', 'close', 'volume']
    assert daily.groupby('symbol').size().to_dict() == {'STALE': 21, 'ZERO': 1}
    stale_rows = daily[daily.symbol == 'STALE']
    assert stale_rows.close.iloc[:-1].tolist() == list(range(4, 24))
    assert pd.isna(stale_rows.close.iloc[-1])
    assert orb._avg_value_cr(daily, ['STALE', 'ZERO']) == {'STALE': 14., 'ZERO': 0.}
    assert_frame_equal(shared._panels['1d'].close, original)
    daily.loc[:, 'close'] = -999.
    assert_frame_equal(shared._panels['1d'].close, original)


def test_all_null_stored_session_still_counts_in_liquidity_window(tmp_path, monkeypatch):
    from apps.api.desk.scanner_snapshot import public_daily_bars

    store = make_store(tmp_path)
    rows = bars_long('NULL', [1000., 1000.] + [10.] * 20,
                     start='2026-01-01', volumes=[1e7] * 22)
    rows.loc[21, ['open', 'high', 'low', 'close', 'volume']] = float('nan')
    store.upsert_bars('1d', rows)
    assert orb._avg_value_cr(store.load_bars('1d', limit=21), ['NULL']) == {'NULL': 10.}
    engine.get_engine().warm()
    def cold_read(*args, **kwargs):
        pytest.fail('null-session preservation must not reread the candle table')
    monkeypatch.setattr(store, 'load_bars', cold_read)
    daily = public_daily_bars(['NULL'])
    assert len(daily) == 21
    assert daily.ts.iloc[-1] == '2026-01-22T00:00:00+05:30'
    assert pd.isna(daily.iloc[-1][['open', 'high', 'low', 'close', 'volume']]).all()
    assert orb._avg_value_cr(daily, ['NULL']) == {'NULL': 10.}


def test_public_daily_snapshot_uses_exchange_timezone_for_mixed_labels(tmp_path):
    from apps.api.desk.scanner_snapshot import public_daily_bars

    store = make_store(tmp_path)
    local = bars_long('LOCAL', [100.], start='2026-10-01T00:00:00')
    offset = bars_long('OFFSET', [200.], start='2026-09-30T18:30:00+00:00')
    store.upsert_bars('1d', pd.concat([local, offset]))
    daily = public_daily_bars(['LOCAL', 'OFFSET'])
    assert daily.ts.tolist() == ['2026-10-01T00:00:00+05:30'] * 2
    assert daily.close.tolist() == [100., 200.]
    assert pd.Timestamp(daily.ts.iloc[0]).tz_convert(UTC).isoformat() == '2026-09-30T18:30:00+00:00'


def test_public_daily_snapshot_refreshes_after_published_version_bump(tmp_path):
    from apps.api.desk.scanner_snapshot import public_daily_bars

    store = make_store(tmp_path, {'BRK': [100.]})
    assert public_daily_bars(['BRK']).close.tolist() == [100.]
    store.upsert_bars('1d', bars_long('BRK', [300.], start='2026-01-06'))
    assert public_daily_bars(['BRK']).close.tolist() == [100.]
    store.bump_bar_version()
    assert public_daily_bars(['BRK']).close.tolist() == [100., 300.]


def test_empty_public_daily_snapshot_is_a_valid_long_frame(tmp_path):
    from apps.api.desk.scanner_snapshot import public_daily_bars

    make_store(tmp_path)
    for symbols in ([], ['MISSING']):
        daily = public_daily_bars(symbols)
        assert daily.empty
        assert list(daily.columns) == ['symbol', 'ts', 'open', 'high', 'low', 'close', 'volume']
        assert orb._avg_value_cr(daily, symbols) == {}
