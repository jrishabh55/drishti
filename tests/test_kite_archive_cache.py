from datetime import date, datetime, timezone
import gzip
import hashlib
import json

import pytest

from apps.research.archive import Archive, ArchiveError, IST
from apps.research.cli import Context, ReadOnlyKiteClient


def saved_parent(tmp_path):
    config = dict(owner='owner', broker_hash='broker', symbols={'RELIANCE': 1},
                  start='2024-01-01', daily_start='2023-11-01', end='2026-10-30',
                  engine_version='original-source')
    archive = Archive(tmp_path/'parent', config)
    task = archive.next_task()
    rows = [['2023-11-01T09:15:00+05:30', 100.125, 102.25, 99.5, 101.75, 1234.0],
            ['2026-10-30T09:15:00+05:30', 200.0, 202.0, 199.0, 201.0, 2345.0]]
    archive.complete(task, rows)
    return archive, config, task, rows


def at(day, end=False):
    return datetime.combine(date.fromisoformat(day), datetime.max.time() if end else datetime.min.time(), IST)


def context(client, fingerprint='session'):
    return Context(client, fingerprint, datetime.now(timezone.utc), 'broker')


def test_fully_cached_request_preserves_saved_values_and_parent_files(tmp_path):
    from apps.research.kite_cache import KiteDailyCache
    archive, config, task, rows = saved_parent(tmp_path)
    before = {p.relative_to(archive.root): p.read_bytes() for p in archive.root.rglob('*') if p.is_file()}
    client = ReadOnlyKiteClient('fake', 'fake', lambda *args: pytest.fail('cached history reached broker'))
    cache = KiteDailyCache(archive.root, config)
    wrapped = cache.wrap_context(lambda: context(client))()
    assert wrapped.fingerprint == 'session'
    assert wrapped.client.historical(1, 'day', at('2023-11-01'), at('2026-10-30', True)) == rows
    assert cache.last_provenance['cached_segments'][0]['sha256'] == task_sha(archive, task)
    assert cache.last_provenance['network_segments'] == []
    assert wrapped.client.historical_provenance == cache.last_provenance
    assert before == {p.relative_to(archive.root): p.read_bytes() for p in archive.root.rglob('*') if p.is_file()}
    with pytest.raises(ArchiveError, match='read-only'):
        wrapped.client.place_order()


def task_sha(archive, task):
    return hashlib.sha256(archive.chunk_path(task).read_bytes()).hexdigest()


def test_partial_cache_downloads_only_earlier_prefix(tmp_path):
    from apps.research.kite_cache import KiteDailyCache
    archive, config, task, saved = saved_parent(tmp_path)
    calls = []
    old = ['2023-10-31T09:15:00+05:30', 90., 92., 89., 91., 1000.]
    def transport(method, url, params, data, headers):
        calls.append((method, url, dict(params)))
        return 200, json.dumps(dict(status='success', data=dict(candles=[old])))
    cache = KiteDailyCache(archive.root, config)
    client = ReadOnlyKiteClient('fake', 'fake', transport)
    result = cache.wrap_context(lambda: context(client))().client.historical(
        1, 'day', at('2023-10-31'), at('2026-10-30', True))
    assert result == [old, *saved]
    assert len(calls) == 1
    assert calls[0][2] == {'from': '2023-10-31 00:00:00', 'to': '2023-10-31 23:59:59'}
    assert cache.last_provenance['cached_segments'][0]['source_version'] == 'original-source'
    assert cache.last_provenance['network_segments'][0]['end'] == '2023-10-31'


@pytest.mark.parametrize('field,value', [('owner', 'other'), ('broker_hash', 'other'),
                                        ('provider', 'yahoo'), ('symbols', {'RELIANCE': 2})])
def test_cross_scope_and_wrong_source_are_blocked(tmp_path, field, value):
    from apps.research.kite_cache import KiteDailyCache
    archive, config, _, _ = saved_parent(tmp_path)
    with pytest.raises(ArchiveError):
        KiteDailyCache(archive.root, config | {field: value})


def test_corrupt_parent_is_hard_failure_without_network_or_repair(tmp_path):
    from apps.research.kite_cache import KiteDailyCache
    archive, config, task, _ = saved_parent(tmp_path)
    archive.chunk_path(task).write_bytes(b'corrupted')
    cache = KiteDailyCache(archive.root, config)
    client = ReadOnlyKiteClient('fake', 'fake', lambda *args: pytest.fail('corrupt cache fell back to broker'))
    with pytest.raises(ArchiveError, match='integrity'):
        cache.wrap_context(lambda: context(client))().client.historical(1, 'day', at('2023-11-01'), at('2026-10-30', True))
    assert archive.chunk_path(task).read_bytes() == b'corrupted'
    assert archive.progress()['completed'] == 1


def test_matching_digest_cannot_hide_wrong_chunk_metadata(tmp_path):
    from apps.research.kite_cache import KiteDailyCache
    archive, config, task, _ = saved_parent(tmp_path)
    doc = json.loads(gzip.decompress(archive.chunk_path(task).read_bytes()))
    doc['start'] = '2020-01-01'
    payload = gzip.compress(json.dumps(doc).encode())
    archive.chunk_path(task).write_bytes(payload)
    with archive.db() as c:
        c.execute('UPDATE tasks SET sha=?,bytes=? WHERE key=?', (hashlib.sha256(payload).hexdigest(), len(payload), task['key']))
    cache = KiteDailyCache(archive.root, config)
    client = ReadOnlyKiteClient('fake', 'fake', lambda *args: pytest.fail('bad metadata reached broker'))
    with pytest.raises(ArchiveError, match='integrity'):
        cache.wrap_context(lambda: context(client))().client.historical(1, 'day', at('2023-11-01'), at('2026-10-30', True))


def test_multiple_uncovered_segments_recheck_session_and_rate_limit(tmp_path):
    from apps.research.kite_cache import KiteDailyCache
    archive, config, _, saved = saved_parent(tmp_path)
    clock = [0.]
    starts = []
    def sleep(seconds): clock[0] += seconds
    def transport(method, url, params, data, headers):
        starts.append(clock[0])
        return 200, json.dumps(dict(status='success', data=dict(candles=[])))
    client = ReadOnlyKiteClient('fake', 'fake', transport)
    checks = []
    def current(): checks.append(clock[0]); return context(client)
    cache = KiteDailyCache(archive.root, config, clock=lambda: clock[0], sleep=sleep)
    result = cache.wrap_context(current)().client.historical(1, 'day', at('2023-10-31'), at('2026-10-31', True))
    assert result == saved
    assert len(starts) == 2 and starts[1] - starts[0] >= 2
    assert len(checks) >= 5  # initial binding, and before/after each actual HTTP


def test_changed_session_between_segments_discards_request(tmp_path):
    from apps.api.kite.rest import KiteError
    from apps.research.kite_cache import KiteDailyCache
    archive, config, _, _ = saved_parent(tmp_path)
    calls = []
    client = ReadOnlyKiteClient('fake', 'fake', lambda *args: (calls.append(args) or (200, json.dumps(dict(status='success', data=dict(candles=[]))))))
    checks = iter([context(client), context(client), context(client, 'new-session')])
    cache = KiteDailyCache(archive.root, config)
    wrapped = cache.wrap_context(lambda: next(checks))()
    with pytest.raises(KiteError) as error:
        wrapped.client.historical(1, 'day', at('2023-10-31'), at('2026-10-31', True))
    assert error.value.token_error and len(calls) == 1
    assert cache.last_provenance == {}
    assert wrapped.client.historical_provenance == {}


def test_session_is_rechecked_after_inter_segment_rate_wait(tmp_path):
    from apps.api.kite.rest import KiteError
    from apps.research.kite_cache import KiteDailyCache
    archive, config, _, _ = saved_parent(tmp_path)
    clock, fingerprint, calls = [0.], ['session'], []
    def sleep(seconds):
        clock[0] += seconds
        if seconds:
            fingerprint[0] = 'renewed'
    def transport(*args):
        calls.append(clock[0])
        return 200, json.dumps(dict(status='success', data=dict(candles=[])))
    client = ReadOnlyKiteClient('fake', 'fake', transport)
    cache = KiteDailyCache(archive.root, config, clock=lambda: clock[0], sleep=sleep)
    wrapped = cache.wrap_context(lambda: context(client, fingerprint[0]))()
    with pytest.raises(KiteError) as error:
        wrapped.client.historical(1, 'day', at('2023-10-31'), at('2026-10-31', True))
    assert error.value.token_error and len(calls) == 1
    assert cache.last_provenance == {}


def test_parent_source_identity_change_is_not_accepted(tmp_path):
    from apps.research.kite_cache import KiteDailyCache
    archive, config, _, _ = saved_parent(tmp_path)
    cache = KiteDailyCache(archive.root, config)
    with archive.db() as connection:
        connection.execute("UPDATE meta SET v=? WHERE k='config'",
                           (json.dumps(config | {'engine_version': 'changed'}),))
    client = ReadOnlyKiteClient('fake', 'fake', lambda *args: pytest.fail('changed source reached broker'))
    with pytest.raises(ArchiveError, match='identity'):
        cache.wrap_context(lambda: context(client))().client.historical(1, 'day', at('2023-11-01'), at('2026-10-30', True))


@pytest.mark.parametrize('retained_digest', [True, False])
def test_parent_saved_file_with_inconsistent_checkpoint_is_never_refetched(tmp_path, retained_digest):
    from apps.research.kite_cache import KiteDailyCache
    archive, config, _, _ = saved_parent(tmp_path)
    with archive.db() as connection:
        connection.execute("UPDATE tasks SET status='pending' WHERE interval='1d'")
        if not retained_digest:
            connection.execute("UPDATE tasks SET sha=NULL WHERE interval='1d'")
    cache = KiteDailyCache(archive.root, config)
    client = ReadOnlyKiteClient('fake', 'fake', lambda *args: pytest.fail('saved parent was refetched'))
    with pytest.raises(ArchiveError, match='checkpoint'):
        cache.wrap_context(lambda: context(client))().client.historical(1, 'day', at('2023-11-01'), at('2026-10-30', True))
