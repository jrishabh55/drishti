"""Runtime scanner strategies must use the same selected-owner view as the UI."""
from apps.api.kite import strategies
from apps.api.scanner import engine
from apps.api.scanner.store import get_scanner_store
from tests.test_kite_execution import env, ist  # noqa: F401


def test_runtime_candidates_pin_user_and_scan_clock(env, monkeypatch):
    client, _, _, _, clock = env
    sid = get_scanner_store().create_scanner('user_a', 'current', '', {
        'logic': 'AND', 'children': [
            {'timeframe': '5m', 'left': {'field': 'close'}, 'op': '>', 'right': {'const': 100}}]})
    calls = []
    def scoped(uid, definition, *, now=None):
        calls.append((uid, now))
        return {'matches': [{'symbol': 'TCS'}], 'source': 'mixed',
                'coverage': {'live': 1, 'delayed': 1}, 'data_as_of': 'controlled'}
    monkeypatch.setattr(engine, 'run_for_user', scoped)
    clock['now'] = ist(9, 36)
    symbols, info = strategies.candidates('user_a', {'symbols': ['SBIN'], 'scanner_id': sid}, now=clock['now'])
    assert symbols == ['SBIN', 'TCS']
    assert calls == [('user_a', clock['now'])]
    assert info['source'] == 'mixed' and info['coverage']['live'] == 1
    assert info['data_as_of'] == 'controlled'


def test_foreign_scanner_never_reaches_scoped_source(env, monkeypatch):
    sid = get_scanner_store().create_scanner('user_b', 'private', '', {'logic': 'AND', 'children': []})
    def forbidden(*args, **kwargs):
        raise AssertionError('must not evaluate a foreign scanner')
    monkeypatch.setattr(engine, 'run_for_user', forbidden)
    symbols, info = strategies.candidates('user_a', {'symbols': ['SBIN'], 'scanner_id': sid})
    assert symbols == ['SBIN'] and "isn't yours" in info['error']
