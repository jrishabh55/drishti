import json
import pytest
from apps.research.archive import Archive,ArchiveError


def config():
    return dict(owner='owner',symbols={'TCS':1},start='2024-01-01',daily_start='2024-01-01',
                end='2024-01-31',intervals=['1d'],provider='kite')


def row(close=101):return ['2024-01-02T00:00:00+05:30',100,102,99,close,1000]


def test_append_only_rejects_replacement_and_keeps_bytes_and_timestamp(tmp_path):
    a=Archive(tmp_path,config(),append_only=True);task=a.next_task()
    a.complete(task,[row()]);path=a.chunk_path(task)
    before=(path.read_bytes(),path.stat().st_mtime_ns)
    a.complete(task,[row()])
    assert (path.read_bytes(),path.stat().st_mtime_ns)==before
    with pytest.raises(ArchiveError,match='overwrite'):
        a.complete(task,[row(100)])
    assert (path.read_bytes(),path.stat().st_mtime_ns)==before
    assert a.progress()['completed']==1


@pytest.mark.parametrize('missing',[False,True])
def test_append_only_integrity_failure_never_queues_a_redownload(tmp_path,missing):
    a=Archive(tmp_path,config(),append_only=True);task=a.next_task();a.complete(task,[row()])
    path=a.chunk_path(task)
    if missing:path.unlink()
    else:path.write_bytes(b'corrupt-preserve-for-review')
    with pytest.raises(ArchiveError,match='integrity'):
        a.verify()
    assert a.next_task() is None
    assert a.progress()['completed']==1
    if not missing:assert path.read_bytes()==b'corrupt-preserve-for-review'


def test_orphan_checkpoint_recovery_reuses_existing_file(tmp_path):
    a=Archive(tmp_path,config(),append_only=True);task=a.next_task();a.complete(task,[row()])
    path=a.chunk_path(task);before=(path.read_bytes(),path.stat().st_mtime_ns)
    with a.db() as c:
        c.execute("UPDATE tasks SET status='pending',rows=0,sha=NULL,bytes=0")
    a.recover_existing()
    assert a.next_task() is None and a.progress()['rows']==1
    assert (path.read_bytes(),path.stat().st_mtime_ns)==before


def test_invalid_orphan_is_preserved_and_not_overwritten(tmp_path):
    a=Archive(tmp_path,config(),append_only=True);task=a.next_task()
    path=a.chunk_path(task);path.write_bytes(b'not-a-candle-file')
    with pytest.raises(ArchiveError,match='integrity'):a.recover_existing()
    assert path.read_bytes()==b'not-a-candle-file'
    with pytest.raises(ArchiveError,match='overwrite'):a.complete(task,[row()])


def test_append_only_yahoo_metadata_never_triggers_refetch(tmp_path):
    from apps.research.long_daily import YahooWorker,verify_metadata
    a=Archive(tmp_path,config()|dict(provider='yahoo'),append_only=True);task=a.next_task()
    YahooWorker(a,fetch=lambda _ : ([row()],dict(events=[])),sleep=lambda _:None).step(task)
    path=a.root/(task['key']+'.metadata.json');path.write_text('{}')
    with pytest.raises(ArchiveError,match='metadata integrity'):verify_metadata(a)
    assert path.read_text()=='{}'
    assert a.next_task() is None


def test_pending_task_with_saved_digest_and_missing_file_never_fetches(tmp_path):
    from apps.research.cli import DownloadWorker
    a=Archive(tmp_path,config(),append_only=True);task=a.next_task();a.complete(task,[row()])
    a.chunk_path(task).unlink()
    with a.db() as c:c.execute("UPDATE tasks SET status='pending'")
    def forbidden():pytest.fail('checkpointed history was fetched again')
    with pytest.raises(ArchiveError,match='integrity'):
        DownloadWorker(a,forbidden).step(a.next_task())


def test_saved_file_recovers_before_credentials_or_network(tmp_path):
    from apps.research.cli import DownloadWorker
    a=Archive(tmp_path,config(),append_only=True);task=a.next_task();a.complete(task,[row()])
    before=a.chunk_path(task).read_bytes()
    with a.db() as c:c.execute("UPDATE tasks SET status='pending',sha=NULL,rows=0,bytes=0")
    def forbidden():pytest.fail('orphan recovery requested credentials or network')
    assert DownloadWorker(a,forbidden).step(a.next_task()) is True
    assert a.chunk_path(task).read_bytes()==before


def test_cached_worker_completes_missing_batch_locally_with_atomic_provenance(tmp_path):
    import gzip
    from datetime import datetime,timezone
    from apps.research.cli import Context,DownloadWorker,ReadOnlyKiteClient
    from apps.research.kite_cache import KiteDailyCache
    parent_config=config()|dict(broker_hash='broker',engine_version='parent-source')
    parent=Archive(tmp_path/'parent',parent_config);old=parent.next_task();parent.complete(old,[row()])
    original={p.relative_to(parent.root):(p.read_bytes(),p.stat().st_mtime_ns) for p in parent.root.rglob('*') if p.is_file()}
    target=Archive(tmp_path/'target',parent_config,append_only=True);task=target.next_task()
    client=ReadOnlyKiteClient('fake','fake',lambda *args:pytest.fail('fully cached history was downloaded again'))
    context=lambda:Context(client,'session',datetime.now(timezone.utc),'broker')
    cache=KiteDailyCache(parent.root,target.config)
    assert DownloadWorker(target,cache.wrap_context(context)).step(task) is True
    assert target.load('owner','TCS','1d')==[row()]
    doc=json.loads(gzip.decompress(target.chunk_path(task).read_bytes()))
    assert doc['provenance']['network_segments']==[]
    assert doc['provenance']['cached_segments'][0]['source_version']=='parent-source'
    assert original=={p.relative_to(parent.root):(p.read_bytes(),p.stat().st_mtime_ns) for p in parent.root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('recorded_hash',[False,True])
def test_metadata_only_interruption_pauses_without_refetch_or_overwrite(tmp_path,recorded_hash):
    import hashlib
    from apps.research.long_daily import YahooWorker,verify_metadata
    a=Archive(tmp_path,config()|dict(provider='yahoo'),append_only=True);task=a.next_task()
    path=a.root/(task['key']+'.metadata.json');path.write_text('{"saved":"preserve"}')
    if recorded_hash:a.set_meta('metadata:'+task['key'],hashlib.sha256(path.read_bytes()).hexdigest())
    before=(path.read_bytes(),path.stat().st_mtime_ns)
    def forbidden(_):pytest.fail('saved metadata triggered historical refetch')
    with pytest.raises(ArchiveError,match='metadata'):
        YahooWorker(a,fetch=forbidden,sleep=lambda _:None).step(task)
    with pytest.raises(ArchiveError,match='metadata'):verify_metadata(a)
    assert (path.read_bytes(),path.stat().st_mtime_ns)==before
    assert a.next_task()['attempts']==0


def test_create_only_json_refuses_replacing_saved_metadata(tmp_path):
    from apps.research.replay import write_json
    path=tmp_path/'metadata.json';write_json(path,{'saved':1})
    before=(path.read_bytes(),path.stat().st_mtime_ns)
    with pytest.raises(ArchiveError,match='overwrite'):
        write_json(path,{'saved':2},create_only=True)
    assert (path.read_bytes(),path.stat().st_mtime_ns)==before
