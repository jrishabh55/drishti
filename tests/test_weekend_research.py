from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import gzip
import json
import math

import pytest
from pydantic import ValidationError

from apps.research.archive import Archive, ArchiveError, validate_rows
from apps.research.replay import ResearchBody, choose_symbols, variants
from apps.api.kite.backtest import BacktestBody

OWNER = 'test-owner'
START, END = date(2024, 10, 31), date(2026, 10, 30)


def config(owner=OWNER):
    return dict(owner=owner, start=START.isoformat(), end=END.isoformat(),
                daily_start='2023-10-31', symbols={'RELIANCE': 1, 'TCS': 2},
                profile={'capital': 100000}, engine_version='test')


def candle(stamp='2024-11-01T09:15:00+05:30', close=101):
    return [stamp, 100, 102, 99, close, 1000]


def test_archive_private_atomic_checkpoint_and_resume(tmp_path):
    a = Archive(tmp_path/'owned', config())
    task = a.next_task()
    rows = [candle('2023-11-01T09:15:00+05:30')]
    a.complete(task, rows)
    assert a.progress()['completed'] == 1
    assert not list((tmp_path/'owned'/'chunks').glob('*.tmp*'))
    assert (a.root.stat().st_mode & 0o077) == 0
    assert (a.root/'manifest.sqlite').stat().st_mode & 0o077 == 0
    b = Archive(tmp_path/'owned', config())
    assert b.progress()['completed'] == 1
    assert b.next_task()['key'] != task['key']
    with pytest.raises(ArchiveError, match='configuration'):
        Archive(tmp_path/'owned', config('another-owner'))


def test_archive_corruption_not_silently_used(tmp_path):
    a = Archive(tmp_path, config())
    task = a.next_task()
    a.complete(task, [candle('2023-11-01T09:15:00+05:30')])
    path = a.chunk_path(task)
    path.write_bytes(b'bad gzip')
    with pytest.raises(ArchiveError, match='integrity'):
        a.load(OWNER, task['symbol'], task['interval'])
    with pytest.raises(ArchiveError, match='owner'):
        a.load('another-owner', task['symbol'], task['interval'])


def test_interrupted_claim_is_retried_and_empty_is_explicit(tmp_path):
    a = Archive(tmp_path, config())
    task = a.next_task()
    a.claim(task)
    b = Archive(tmp_path, config(), recover=True)
    assert b.next_task()['key'] == task['key']
    b.complete(task, [])
    assert b.progress()['empty'] == 1
    assert b.load(OWNER, task['symbol'], task['interval']) == []


@pytest.mark.parametrize('rows', [
    [candle(close=float('nan'))],
    [candle('2027-01-01T09:15:00+05:30')],
    [candle('2024-11-01T09:16:00+05:30')],
    [candle('2024-11-01T09:15:00')],
    [candle(), candle(close=100)],
    [['2024-11-01T09:15:00+05:30', 100, 90, 99, 101, 1000]],
])
def test_candle_validation_rejects_bad_and_out_of_range(rows):
    with pytest.raises(ArchiveError):
        validate_rows(rows, '5m', START, END)


def test_identical_duplicate_is_deduplicated_and_original_values_preserved():
    assert validate_rows([candle(), candle()], '5m', START, END) == [candle()]


def test_research_span_explicitly_bounded_without_widening_public_api():
    body = dict(strategy_id='research', start_date=START, end_date=END,
                split_date=END-timedelta(days=183), symbols=['TCS'])
    with pytest.raises(ValidationError, match='60'):
        BacktestBody(**body)
    assert ResearchBody(**body).start_date == START
    with pytest.raises(ValidationError):
        ResearchBody(**(body | {'start_date': date(2020, 1, 1)}))


def test_symbol_selection_uses_only_preperiod_data():
    rows = {'TCS': [candle('2024-10-01T09:15:00+05:30', 101)],
            'RELIANCE': [candle('2024-10-01T09:15:00+05:30', 100),
                         candle('2026-10-01T09:15:00+05:30', 1000000)]}
    assert choose_symbols(rows, START, limit=1, min_sessions=1) == ['TCS']
    assert len(variants(['TCS'])) == 4
    assert all(v['mode'] == 'off' for v in variants(['TCS']))
    assert len({v['id'] for v in variants(['TCS'])}) == 4


def test_readonly_client_refuses_orders_before_network():
    from apps.research.cli import ReadOnlyKiteClient
    calls = []
    c = ReadOnlyKiteClient('not-real', 'not-real', lambda *args: calls.append(args))
    with pytest.raises(ArchiveError, match='read-only'):
        c._call('POST', '/orders/regular')
    with pytest.raises(ArchiveError, match='read-only'):
        c._call('GET', '/user/margins')
    assert calls == []


def test_expired_or_changed_session_never_commits_download(tmp_path):
    from types import SimpleNamespace
    from apps.research.cli import DownloadWorker
    a = Archive(tmp_path, config())
    task = a.next_task()
    calls = []
    client = SimpleNamespace(historical=lambda *args: calls.append(args) or [candle('2023-11-01T09:15:00+05:30')])
    ctx = SimpleNamespace(client=client, fingerprint='original')
    contexts = iter([ctx, ctx, None])
    w = DownloadWorker(a, lambda: next(contexts), sleep=lambda _: None)
    assert w.step(task) is False
    assert len(calls) == 1
    assert a.progress()['completed'] == 0
    assert a.next_task()['attempts'] == 0
    assert a.get_meta('phase') == 'awaiting_login'


def test_retry_429_remains_checkpointed_and_bounded(tmp_path):
    from types import SimpleNamespace
    from apps.research.cli import DownloadWorker
    from apps.api.kite.rest import KiteError
    a = Archive(tmp_path, config())
    def limited(*args):
        raise KiteError('do not log sensitive transport text', status=429)
    ctx = SimpleNamespace(client=SimpleNamespace(historical=limited), fingerprint='same')
    delays = []
    w = DownloadWorker(a, lambda: ctx, sleep=delays.append)
    assert w.step(a.next_task()) is True
    assert a.progress()['completed'] == 0
    assert a.next_task()['attempts'] == 1
    assert max(delays) >= 5
    with a.db() as c:
        assert 'sensitive' not in c.execute('SELECT error FROM tasks WHERE attempts=1').fetchone()[0]


def test_disk_reserve_checked_before_network(tmp_path, monkeypatch):
    from apps.research.cli import DownloadWorker
    a = Archive(tmp_path, config())
    monkeypatch.setattr(a, 'check_space', lambda: (_ for _ in ()).throw(ArchiveError('disk budget')))
    def forbidden():
        pytest.fail('credentials/network must not be used when disk budget is exhausted')
    with pytest.raises(ArchiveError, match='disk'):
        DownloadWorker(a, forbidden).step(a.next_task())


def test_credentials_readonly_expiry_and_corruption(tmp_path,monkeypatch):
    import sqlite3
    from cryptography.fernet import Fernet
    from apps.research.cli import Credentials
    key = Fernet.generate_key(); f=Fernet(key)
    monkeypatch.setenv('WEBAPP_KEY_ENCRYPTION_SECRET',key.decode())
    path=tmp_path/'kite.sqlite'; monkeypatch.setenv('KITE_DB_PATH',str(path))
    now=datetime.now(timezone.utc)
    app=f.encrypt(json.dumps(dict(api_key='fake-key',api_secret='fake-secret')).encode()).decode()
    def session(expiry):
        return f.encrypt(json.dumps(dict(access_token='fake-token',kite_user_id='fake-broker',
             expires_at=expiry.isoformat(),login_at=now.isoformat())).encode()).decode()
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE kite_secrets(user_id TEXT,kind TEXT,ciphertext TEXT)')
        c.executemany('INSERT INTO kite_secrets VALUES (?,?,?)',[(OWNER,'app',app),(OWNER,'session',session(now+timedelta(hours=1)))])
    before=path.read_bytes()
    credentials=Credentials()
    assert credentials.owner()==OWNER
    assert credentials.context(OWNER) is not None
    assert credentials.context('someone-else') is None
    assert path.read_bytes()==before
    with sqlite3.connect(path) as c:
        c.execute("UPDATE kite_secrets SET ciphertext=? WHERE kind='session'",(session(now-timedelta(seconds=1)),))
    assert credentials.context(OWNER) is None
    with sqlite3.connect(path) as c:
        c.execute("UPDATE kite_secrets SET ciphertext='corrupted' WHERE kind='session'")
    assert credentials.context(OWNER) is None


def test_offline_replay_uses_existing_engine_without_broker_io(tmp_path,monkeypatch):
    from apps.research.replay import run_comparison
    from apps.api.desk.profile import DeskProfile
    from apps.api.kite import rest
    from tests.desk_utils import random_walk_sessions
    cfg=config() | dict(symbols={'SBIN':1},start='2026-03-01',end='2026-10-30',
                        daily_start='2025-03-01',profile=DeskProfile().model_dump())
    a=Archive(tmp_path,cfg)
    a.set_meta('research_symbols',['SBIN'])
    rows=random_walk_sessions(date(2026,10,30),3)
    normalized=[[r[0].isoformat(),*r[1:]] for r in rows]
    with a.db() as c:
        tasks=[dict(r) for r in c.execute("SELECT * FROM tasks WHERE interval='5m' ORDER BY start")]
    for task in tasks:
        a.complete(task,[r for r in normalized if task['start']<=r[0][:10]<=task['end']])
    def forbidden(*args,**kwargs):
        pytest.fail('offline replay tried broker I/O')
    monkeypatch.setattr(rest,'_httpx_transport',forbidden)
    out=run_comparison(a)
    assert len(out)==8
    assert a.get_meta('research_complete') is True
    assert (a.root/'comparison.json').exists()
    assert 'variant,period,trades' in (a.root/'comparison.csv').read_text()
    for result in out:
        detail=json.loads((a.root/(result['id']+'.json')).read_text())
        assert detail['strategy_snapshot']['mode']=='off'
        assert detail['config']['start_date']=='2026-03-01'
        assert detail['summary']['all']['trades']>=0
        assert 'test' in detail['summary']
        assert '2026-03' in detail['regimes']['months']
        assert detail['config']['split_date']=='2026-05-01'


def test_regime_summary_keeps_empty_months_and_quarters():
    from apps.research.replay import regime_summary
    from apps.api.desk.profile import DeskProfile
    result=regime_summary([],DeskProfile(),date(2025,12,15),date(2026,2,1))
    assert list(result['months'])==['2025-12','2026-01','2026-02']
    assert list(result['quarters'])==['2025-Q4','2026-Q1']
    assert all(v['trades']==0 for v in result['months'].values())


def test_probe_disk_budget_pauses_in_watch_mode(tmp_path,monkeypatch):
    from apps.research import cli
    a=Archive(tmp_path,config())
    class Worker:
        archive=a
        def step(self,task):raise ArchiveError('disk budget reached')
    waits=[]
    monkeypatch.setattr(cli.time,'sleep',waits.append)
    cli.step_or_pause(Worker(),{},True)
    assert waits==[60]
    assert a.get_meta('phase')=='paused_disk_budget'


def test_live_wal_reader_follows_updates_and_refuses_mutations(tmp_path):
    import sqlite3
    from apps.research.cli import read_database
    path=tmp_path/'wal.sqlite'
    writer=sqlite3.connect(path)
    writer.execute('PRAGMA journal_mode=WAL')
    writer.execute('CREATE TABLE records(value TEXT)')
    writer.execute("INSERT INTO records VALUES ('first')");writer.commit()
    with read_database(path) as reader:
        assert reader.execute('SELECT value FROM records').fetchone()[0]=='first'
        writer.execute("UPDATE records SET value='second'");writer.commit()
        assert reader.execute('SELECT value FROM records').fetchone()[0]=='second'
        for sql in ["UPDATE records SET value='bad'",'PRAGMA query_only=OFF',"ATTACH DATABASE ':memory:' AS other"]:
            with pytest.raises(sqlite3.DatabaseError):reader.execute(sql)
    writer.close()
    # After the last writer closes, missing WAL/SHM must still allow a query.
    with read_database(path) as reader:
        assert reader.execute('SELECT value FROM records').fetchone()[0]=='second'


@pytest.mark.parametrize('profile',['weekend','long-daily'])
def test_launcher_keeps_env_on_stdin_and_database_files_readonly(tmp_path,monkeypatch,profile):
    import base64,io,os,runpy,subprocess,sys,tarfile
    from pathlib import Path
    script=runpy.run_path(str(Path(__file__).resolve().parents[1]/'deploy/weekend-research.py'))
    for name in ['kite','desk','scanner']:(tmp_path/(name+'.sqlite')).touch()
    env={'WEBAPP_KEY_ENCRYPTION_SECRET':'private-test-secret',**{
        name.upper()+'_DB_PATH':'/home/appuser/.tradingagents/'+name+'.sqlite'
        for name in ['kite','desk','scanner']}}
    info={'Image':'sha256:test-image','Mounts':[{'Source':str(tmp_path),'Destination':'/home/appuser/.tradingagents'}],
          'Config':{'Env':[k+'='+v for k,v in env.items()]}}
    calls=[]
    def fake_run(args,**kwargs):
        calls.append((args,kwargs))
        if args[:2]==['docker','inspect']:
            return subprocess.CompletedProcess(args,0,json.dumps([info]),'') if args[2]=='drishti-qzkexq-api-1' else subprocess.CompletedProcess(args,1,'','')
        assert args[:2]==['docker','run']
        return subprocess.CompletedProcess(args,0,'container-test\n','')
    payload=io.BytesIO()
    with tarfile.open(fileobj=payload,mode='w:gz') as tf:
        member=tarfile.TarInfo('research/__init__.py');member.size=0
        tf.addfile(member,io.BytesIO(b''))
    monkeypatch.setattr(subprocess,'run',fake_run)
    monkeypatch.setattr(os,'chown',lambda *args:None)
    monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps({'action':'launch','profile':profile,'tar':base64.b64encode(payload.getvalue()).decode()})))
    exec(compile(script['REMOTE'],'remote-launch-test','exec'),{})
    args,kwargs=calls[-1]
    assert 'private-test-secret' not in ' '.join(args)
    lines=dict(line.split('=',1) for line in kwargs['input'].splitlines())
    assert lines['WEBAPP_KEY_ENCRYPTION_SECRET']=='private-test-secret'
    assert lines['KITE_ORDERS_ENABLED']=='0'
    mounts=[args[i+1] for i,v in enumerate(args[:-1]) if v=='--mount']
    assert sum('.sqlite' in m and m.endswith(',readonly') for m in mounts)==3
    assert '--read-only' in args and '--cap-drop=ALL' in args
    assert ('research.long_daily' if profile=='long-daily' else 'research.cli') in args
    assert ('--memory=512m' if profile=='long-daily' else '--memory=1100m') in args
