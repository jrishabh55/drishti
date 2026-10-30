"""Private scanner candles must never escape the selected direct-grant owner."""
from datetime import timedelta

from apps.api.desk import bars_source
from apps.api.kite import auth, live
from apps.api.kite.store import get_kite_store
from apps.api.scanner import engine
from apps.api.scanner.schema import parse_definition
from tests.scanner_utils import make_store, bars_long
from tests.test_kite_connection import env, _connect, ist
from tests.test_kite_sharing import grant, select
import asyncio
import pytest


def prepared(env, tmp_path, monkeypatch):
    client, user, fake, clock = env
    _connect(client, fake)
    clock['now'] = ist(9, 25, 3)
    monkeypatch.setattr(live, '_now', lambda: clock['now'])
    store = make_store(tmp_path, {'SBIN': [80., 90.], 'TCS': [80., 90.]})
    store.upsert_bars('5m', bars_long('TCS', [90.], start=ist(9, 15).isoformat(), freq_minutes=5))
    hub = live.Hub('user_a', ['SBIN'], state='live', backfilled={'SBIN'})
    hub.builder.seed('SBIN', [(ist(9, 15), 100., 102., 99., 101., 1000.),
                              (ist(9, 20), 101., 104., 100., 103., 1200.),
                              (ist(9, 25), 103., 1000., 103., 999., 1.)])
    hub.builder.last_tick['SBIN'] = clock['now']
    live._hubs['user_a'] = hub
    live.install()
    return client, user, fake, clock, store, hub


def test_universe_overlays_selected_owner_and_excludes_forming_candles(env, tmp_path, monkeypatch):
    client, user, _, clock, store, hub = prepared(env, tmp_path, monkeypatch)
    grant(client)
    user['id'] = 'user_b'
    select(client)
    result = bars_source.get_session_universe('user_b', symbols=['SBIN', 'TCS'], now=clock['now'])
    assert [r[4] for r in result.rows['SBIN']] == [101., 103.]
    assert result.sources == {'SBIN': 'live:kite', 'TCS': 'delayed:yahoo'}
    assert result.coverage == {'requested': 2, 'live': 1, 'delayed': 1, 'missing': 0}
    assert store.load_symbol_bars('5m', 'SBIN').empty
    assert 'SBIN' not in bars_source.get_session_rows_universe('user_c', symbols=['SBIN'])
    get_kite_store().revoke_data_access('user_a', 'user_b')
    assert 'SBIN' not in bars_source.get_session_universe('user_b', symbols=['SBIN'], now=clock['now']).rows
    auth.mark_revoked('user_a')
    assert 'SBIN' not in bars_source.get_session_universe('user_a', symbols=['SBIN'], now=clock['now']).rows


def test_universe_discards_inflight_revoked_private_rows(env, tmp_path, monkeypatch):
    client, user, _, clock, _, hub = prepared(env, tmp_path, monkeypatch)
    grant(client)
    user['id'] = 'user_b'
    select(client)
    original = hub.builder.rows
    def revoked(*args):
        rows = original(*args)
        get_kite_store().revoke_data_access('user_a', 'user_b')
        return rows
    monkeypatch.setattr(hub.builder, 'rows', revoked)
    result = bars_source.get_session_universe('user_b', symbols=['SBIN'], now=clock['now'])
    assert result.rows == {} and result.coverage['missing'] == 1


def test_general_scanner_uses_private_closed_daily_and_completed_session(env, tmp_path, monkeypatch):
    _, _, _, clock, store, hub = prepared(env, tmp_path, monkeypatch)
    hub.history = {'1d': {'SBIN': [(ist(9, 15) - timedelta(days=1), 100., 110., 99., 105., 1000.),
                                    (ist(9, 15), 105., 999., 105., 999., 1000.)]}, '5m': {}}
    definition = parse_definition({'logic': 'AND', 'children': [
        {'timeframe': '5m', 'left': {'field': 'close'}, 'op': '>', 'right': {'const': 100}},
        {'timeframe': '1d', 'left': {'field': 'close'}, 'op': '<', 'right': {'const': 200}}]})
    got = engine.run_for_user('user_a', definition, now=clock['now'])
    assert [m['symbol'] for m in got['matches']] == ['SBIN']
    assert got['matches'][0]['close'] == 105.  # closed daily price, never forming candle
    assert got['matches'][0]['source'] == 'live:kite'
    assert got['matches'][0]['price_basis'] == 'closed_daily'
    assert got['source'] == 'mixed'
    assert got['coverage']['live'] == 1
    other = engine.run_for_user('user_b', definition, now=clock['now'])
    assert other['matches'] == [] and other['source'] == 'delayed:yahoo'
    assert store.load_symbol_bars('1d', 'SBIN').close.tolist() == [80., 90.]


def test_scanner_discards_private_snapshot_if_owner_changes_during_evaluation(env, tmp_path, monkeypatch):
    client, user, _, clock, _, hub = prepared(env, tmp_path, monkeypatch)
    hub.history = {'1d': {'SBIN': [(ist(9, 15) - timedelta(days=1), 100., 110., 99., 105., 1000.)]}}
    grant(client)
    user['id'] = 'user_b'
    select(client)
    original = engine.ScanEngine.run
    def revoke(self, definition):
        result = original(self, definition)
        get_kite_store().revoke_data_access('user_a', 'user_b')
        return result
    monkeypatch.setattr(engine.ScanEngine, 'run', revoke)
    definition = parse_definition({'logic': 'AND', 'children': [
        {'timeframe': '1d', 'left': {'field': 'close'}, 'op': '>', 'right': {'const': 100}}]})
    assert engine.run_for_user('user_b', definition, now=clock['now'])['matches'] == []


@pytest.mark.parametrize('count', [217, 500])
def test_stream_supports_full_universes_with_background_history(env, monkeypatch, count):
    client, _, fake, clock = env
    _connect(client, fake)
    symbols = [f'S{i}' for i in range(count)]
    monkeypatch.setattr(live.rest, 'nse_instruments', lambda *a: {s: {'token': i} for i, s in enumerate(symbols)})
    async def hold(*args, **kwargs):
        await asyncio.Event().wait()
    monkeypatch.setattr(live.ticker, 'run_ticker', hold)
    async def scenario():
        hub = await live.start('user_a', symbols, now=clock['now'], transport=fake)
        assert len(hub.symbols) == count
        assert hub.history_task is not None and not hub.history_task.done()
        status = live.freshness('user_a', clock['now'])
        assert status['bootstrap']['total'] == count * 2
        assert status['bootstrap']['completed'] == 0
        await live.stop('user_a')
        assert hub.history_task.cancelled()
    asyncio.run(scenario())


def test_history_bootstrap_is_bounded_private_and_records_empty_data(env, tmp_path, monkeypatch):
    _, _, fake, clock, store, hub = prepared(env, tmp_path, monkeypatch)
    monkeypatch.setattr(live, 'BACKFILL_SPACING_S', 0)
    calls = []
    class Client:
        def historical(self, token, interval, start, end):
            calls.append((token, interval, start, end))
            return [] if interval == '5minute' else [[(ist(9, 15) - timedelta(days=1)).isoformat(), 100, 110, 99, 105, 1000]]
    asyncio.run(live._bootstrap_history(hub, Client(), {'SBIN': {'token': 1}}, lambda: clock['now']))
    assert len(calls) == 2
    assert hub.history['1d']['SBIN'][0][4] == 105
    assert 'SBIN:5m' in hub.history_errors
    assert store.load_symbol_bars('1d', 'SBIN').close.tolist() == [80., 90.]


def test_history_gate_spaces_failed_requests_and_shares_owner_budget():
    from apps.api.kite import history
    current = [0.]
    waits = []
    def sleep(seconds):
        waits.append(seconds)
        current[0] += seconds
    gate = history.HistoryGate(clock=lambda: current[0], sleep=sleep)
    class Client:
        def historical(self, *args):
            raise live.rest.KiteError('offline', 'NetworkException', 503)
    for _ in range(3):
        with pytest.raises(live.rest.KiteError):
            gate.fetch('owner', Client(), 1, 'minute', None, None)
    assert len(waits) == 2 and all(s >= 1/3 for s in waits)


def test_saved_and_preview_routes_use_selected_private_data(env, tmp_path, monkeypatch):
    client, _, _, clock, store, hub = prepared(env, tmp_path, monkeypatch)
    from apps.api.routes import scanners
    # Reuse the authenticated test app; no production root-wiring edits.
    client.app.include_router(scanners.router, prefix='/api')
    hub.history = {'1d': {'SBIN': [(ist(9, 15) - timedelta(days=1), 100., 110., 99., 105., 1000.)]}}
    definition = {'logic': 'AND', 'children': [
        {'timeframe': '1d', 'left': {'field': 'close'}, 'op': '>', 'right': {'const': 100}}]}
    sid = store.create_scanner('user_a', 'Private', '', definition)
    for response in (client.post('/api/scanners/preview', json={'definition': definition}),
                     client.post(f'/api/scanners/{sid}/run')):
        assert response.status_code == 200
        assert [m['symbol'] for m in response.json()['matches']] == ['SBIN']
        assert response.json()['coverage']['live'] == 1


def test_orb_route_reports_selected_sources_without_changing_legacy_coverage(env, tmp_path, monkeypatch):
    client, _, _, clock, _, hub = prepared(env, tmp_path, monkeypatch)
    from apps.api.routes import desk
    client.app.include_router(desk.router, prefix='/api')
    monkeypatch.setattr(desk, '_now', lambda: clock['now'])
    response = client.get('/api/desk/orb?or_minutes=5')
    assert response.status_code == 200, response.text
    doc = response.json()
    assert doc['source'] == 'mixed'
    assert doc['source_coverage'] == {'requested': 2, 'live': 1, 'delayed': 1, 'missing': 0}
    assert doc['sources'] == {'SBIN': 'live:kite', 'TCS': 'delayed:yahoo'}
    assert 'complete' in doc['coverage'] and 'incomplete' in doc['coverage']


@pytest.mark.parametrize('universe,want', [('fno', ['SBIN']), ('nifty500', ['SBIN', 'TCS'])])
def test_connection_can_subscribe_scanner_universe_without_typing_symbols(env, tmp_path, monkeypatch, universe, want):
    client, _, _, clock, store, _ = prepared(env, tmp_path, monkeypatch)
    store.upsert_instruments([{'symbol': 'SBIN', 'yf_symbol': 'SBIN.NS', 'name': 'SBIN',
                              'fno': True, 'index_memberships': ['NIFTY500']}])
    chosen = []
    async def start(user_id, symbols, **kwargs):
        chosen.extend(symbols)
    monkeypatch.setattr(live, 'start', start)
    response = client.post('/api/kite/live/start', json={'universe': universe})
    assert response.status_code == 200, response.text
    assert chosen == want


def test_latest_quote_is_visible_and_revoked_hub_is_never_labelled_live(env, tmp_path, monkeypatch):
    _, _, _, clock, _, hub = prepared(env, tmp_path, monkeypatch)
    status = live.freshness('user_a', clock['now'])
    assert status['symbols'][0]['last_price'] == 999.
    auth.mark_revoked('user_a')
    status = live.freshness('user_a', clock['now'])
    assert status['symbols'][0]['source'] == 'delayed:yahoo'


def test_weekly_scanner_does_not_use_current_unfinished_week(env, tmp_path, monkeypatch):
    _, _, _, clock, _, hub = prepared(env, tmp_path, monkeypatch)
    hub.history = {'1d': {'SBIN': [(ist(9, 15) - timedelta(days=1), 100., 110., 99., 105., 1000.)]}}
    definition = parse_definition({'logic': 'AND', 'children': [
        {'timeframe': '1w', 'left': {'field': 'close'}, 'op': '>', 'right': {'const': 100}}]})
    assert engine.run_for_user('user_a', definition, now=clock['now'])['matches'] == []


@pytest.mark.parametrize('count', [217, 500])
def test_entire_universe_uses_private_feed_without_shared_cache_writes(env, tmp_path, monkeypatch, count):
    _, _, _, clock, _, _ = prepared(env, tmp_path, monkeypatch)
    symbols = [f'S{i}' for i in range(count)]
    store = make_store(tmp_path / 'full', {s: [80., 90.] for s in symbols})
    hub = live.Hub('user_a', symbols, state='live', backfilled=set(symbols))
    row = (ist(9, 15), 100., 102., 99., 101., 1000.)
    hub.history['1d'] = {s: [(row[0] - timedelta(days=1), *row[1:])] for s in symbols}
    for sym in symbols:
        hub.builder.seed(sym, [row])
        hub.builder.last_tick[sym] = clock['now']
    live._hubs['user_a'] = hub
    result = bars_source.get_session_universe('user_a', symbols=symbols, now=clock['now'])
    assert result.coverage == {'requested': count, 'live': count, 'delayed': 0, 'missing': 0}
    definition = parse_definition({'logic': 'AND', 'children': [
        {'timeframe': '5m', 'left': {'field': 'close'}, 'op': '>', 'right': {'const': 100}}]})
    result = engine.run_for_user('user_a', definition, now=clock['now'])
    assert len(result['matches']) == count and result['coverage']['live'] == count
    assert store.load_bars('5m').empty
    assert set(store.load_bars('1d').close) == {80., 90.}


def test_stale_feed_falls_back_to_public_without_using_private_candles(env, tmp_path, monkeypatch):
    _, _, _, clock, _, hub = prepared(env, tmp_path, monkeypatch)
    hub.builder.last_tick['SBIN'] = clock['now'] - timedelta(seconds=91)
    definition = parse_definition({'logic': 'AND', 'children': [
        {'timeframe': '5m', 'left': {'field': 'close'}, 'op': '>', 'right': {'const': 100}}]})
    result = engine.run_for_user('user_a', definition, now=clock['now'])
    assert result['matches'] == []
    assert result['coverage']['by_timeframe']['5m'] == {'kite': 0, 'delayed': 1, 'missing': 1}


def test_or_condition_still_matches_when_another_timeframe_has_no_candles(env, tmp_path, monkeypatch):
    _, _, _, clock, _, hub = prepared(env, tmp_path, monkeypatch)
    hub.history = {'1d': {'SBIN': [(ist(9, 15) - timedelta(days=1), 100., 110., 99., 105., 1000.)]}}
    definition = parse_definition({'logic': 'OR', 'children': [
        {'timeframe': '1h', 'left': {'field': 'close'}, 'op': '>', 'right': {'const': 100}},
        {'timeframe': '1d', 'left': {'field': 'close'}, 'op': '>', 'right': {'const': 100}}]})
    assert [m['symbol'] for m in engine.run_for_user('user_a', definition, now=clock['now'])['matches']] == ['SBIN']


def test_final_provenance_cannot_leak_quotes_after_revocation(env, tmp_path, monkeypatch):
    client, user, _, clock, _, hub = prepared(env, tmp_path, monkeypatch)
    from apps.api.scanner import scoped
    hub.history = {'1d': {'SBIN': [(ist(9, 15) - timedelta(days=1), 100., 110., 99., 105., 1000.)]}}
    grant(client)
    user['id'] = 'user_b'
    select(client)
    original = scoped.annotate
    def revoke(*args):
        result = original(*args)
        get_kite_store().revoke_data_access('user_a', 'user_b')
        return result
    monkeypatch.setattr(scoped, 'annotate', revoke)
    definition = parse_definition({'logic': 'AND', 'children': [
        {'timeframe': '1d', 'left': {'field': 'close'}, 'op': '>', 'right': {'const': 100}}]})
    assert engine.run_for_user('user_b', definition, now=clock['now'])['matches'] == []


def test_universe_explicit_clock_controls_private_provider_reads(env, tmp_path, monkeypatch):
    _, _, _, clock, _, _ = prepared(env, tmp_path, monkeypatch)
    monkeypatch.setattr(live, '_now', lambda: clock['now'] + timedelta(days=1))
    result = bars_source.get_session_universe('user_a', symbols=['SBIN'], now=clock['now'])
    assert result.sources == {'SBIN': 'live:kite'}


def test_own_history_is_discarded_when_disconnected_during_fetch(env, tmp_path, monkeypatch):
    _, _, fake, clock, _, _ = prepared(env, tmp_path, monkeypatch)
    def revoked(*args):
        auth.mark_revoked('user_a')
        return [[ist(9, 15).isoformat(), 100, 110, 99, 105, 1000]]
    monkeypatch.setattr(live.rest.KiteClient, 'historical', revoked)
    assert live.provider('user_a', 'SBIN', '5m', 'history', now=clock['now'], transport=fake) is None


def test_universe_never_blends_two_selected_owners_during_source_switches(env, tmp_path, monkeypatch):
    client, user, _, clock, _, a = prepared(env, tmp_path, monkeypatch)
    grant(client)
    user['id'] = 'user_b'
    select(client)
    # Both connections are legitimately available, but one scan must remain
    # bound to its original selection even if the user changes it twice.
    other = live.Hub('user_b', ['SBIN', 'TCS', 'WIPRO'], state='live', backfilled={'SBIN', 'TCS', 'WIPRO'})
    for sym in other.symbols:
        other.builder.seed(sym, [(ist(9, 15), 9999., 9999., 9999., 9999., 1.)])
        other.builder.last_tick[sym] = clock['now']
    live._hubs['user_b'] = other
    # Give user_b a real controlled session as well.
    _connect(client, env[2])
    select(client)
    original_a, original_b = a.builder.rows, other.builder.rows
    def switch_to_own(*args):
        rows = original_a(*args)
        get_kite_store().select_data_source('user_b', None)
        return rows
    def switch_back_on_third(sym, interval):
        rows = original_b(sym, interval)
        if sym == 'WIPRO':
            get_kite_store().select_data_source('user_b', 'user_a')
        return rows
    monkeypatch.setattr(a.builder, 'rows', switch_to_own)
    monkeypatch.setattr(other.builder, 'rows', switch_back_on_third)
    result = bars_source.get_session_universe('user_b', symbols=['SBIN', 'TCS', 'WIPRO'], now=clock['now'])
    assert all(r[4] != 9999. for rows in result.rows.values() for r in rows)
