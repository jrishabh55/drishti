from datetime import date
from pathlib import Path
import json
import pandas as pd
import pytest
from apps.research.archive import Archive,ArchiveError


def entry(key='NSE:TCS',exchange='NSE',symbol='TCS',token=1):
    return dict(key=key,exchange=exchange,symbol=symbol,name='Example',isin='INE467B01029',
                kite_token=token,yahoo_symbol=symbol+('.NS' if exchange=='NSE' else '.BO'))


def test_plan_reuses_existing_provider_series_and_keeps_venues_separate():
    from apps.research.market_history import plan_targets
    entries=[entry(),entry('BSE:532540','BSE','TCS',2),entry('NSE:NEW',symbol='NEW',token=3)]
    refs={'NSE:TCS':{'kite':{'state':'existing_partial','source':'legacy'},'yahoo':{'state':'existing_complete','source':'legacy'}}}
    plan=plan_targets(entries,refs)
    assert 'NSE:TCS' not in plan['new_symbols']['kite']
    assert 'NSE:TCS' not in plan['new_symbols']['yahoo']
    assert plan['new_symbols']['kite']=={'BSE:532540':2,'NSE:NEW':3}
    assert plan['references']['NSE:TCS']==refs['NSE:TCS']


def test_plan_records_unmapped_series_without_fake_tickers():
    from apps.research.market_history import plan_targets
    item=entry();item.update(kite_token=None,yahoo_symbol=None)
    plan=plan_targets([item],{})
    assert not plan['new_symbols']['kite'] and not plan['new_symbols']['yahoo']
    assert plan['unavailable']['NSE:TCS']==['kite_mapping_unavailable','yahoo_mapping_unavailable']
    with pytest.raises(ArchiveError):plan_targets([item,item],{})


def test_yahoo_identity_requires_correct_venue_currency_symbol_and_type():
    from apps.research.market_history import validate_yahoo_identity
    meta=dict(symbol='TCS.NS',exchangeName='NSI',currency='INR',instrumentType='EQUITY')
    validate_yahoo_identity(entry(),meta)
    for change in [dict(symbol='OTHER.NS'),dict(exchangeName='BSE'),dict(currency='USD'),dict(instrumentType='ETF')]:
        with pytest.raises(ArchiveError):validate_yahoo_identity(entry(),meta|change)


def test_legacy_reference_is_readonly_and_preserves_partial_history(tmp_path):
    from apps.research.market_history import existing_references
    cfg=dict(owner='owner',broker_hash='broker',symbols={'TCS':1},start='2000-01-01',daily_start='2000-01-01',
             end='2026-10-30',intervals=['1d'],provider='kite',daily_chunk_days=1900,engine_version='old')
    a=Archive(tmp_path/'kite',cfg);task=a.next_task()
    a.complete(task,[['2000-01-03T00:00:00+05:30',100,102,99,101,10]])
    before={p.relative_to(a.root):(p.read_bytes(),p.stat().st_mtime_ns) for p in a.root.rglob('*') if p.is_file()}
    refs=existing_references({'kite':a.root},cfg)
    assert refs['NSE:TCS']['kite']['state']=='existing_partial'
    assert before=={p.relative_to(a.root):(p.read_bytes(),p.stat().st_mtime_ns) for p in a.root.rglob('*') if p.is_file()}
    with pytest.raises(ArchiveError):existing_references({'kite':a.root},cfg|{'owner':'other'})


def test_budget_counts_metadata_and_reserves_space(tmp_path,monkeypatch):
    from apps.research.market_history import ArtifactBudget
    (tmp_path/'metadata.json').write_bytes(b'x'*100)
    budget=ArtifactBudget(tmp_path,maximum=120,reserve=0)
    assert budget.used==100
    with pytest.raises(ArchiveError,match='disk budget'):budget.check(estimate=30)


def test_archive_queue_keeps_daily_and_selected_priority_without_materializing_all(tmp_path):
    cfg=dict(owner='owner',symbols={'AAA':1,'ZZZ':2},start='2026-01-01',daily_start='2025-01-01',end='2026-10-30')
    a=Archive(tmp_path,cfg)
    assert a.next_task(['ZZZ'])['interval']=='1d'
    with a.db() as c:c.execute("UPDATE tasks SET status='complete' WHERE interval='1d'")
    assert a.next_task(['ZZZ'])['symbol']=='ZZZ'
    assert a.next_task()['symbol']=='AAA'
    with a.db() as c:
        c.execute("UPDATE tasks SET attempts=6 WHERE symbol='AAA'")
    assert a.next_task()['symbol']=='ZZZ'


def test_unavailable_yahoo_is_terminal_and_keeps_reason(tmp_path):
    from apps.research.market_history import HistoryUnavailable
    from apps.research.long_daily import YahooWorker
    cfg=dict(owner='owner',symbols={'NSE:TCS':1},start='2000-01-01',daily_start='2000-01-01',
             end='2026-10-30',intervals=['1d'],provider='yahoo',daily_chunk_days=10000)
    a=Archive(tmp_path,cfg,append_only=True)
    def unavailable(_):raise HistoryUnavailable('not available')
    YahooWorker(a,fetch=unavailable,sleep=lambda _:None).step(a.next_task())
    with a.db() as c:
        task=dict(c.execute('SELECT * FROM tasks').fetchone())
    assert task['error']=='yahoo_history_unavailable' and task['status']=='failed' and task['attempts']==1


def test_prepare_recovers_frozen_catalog_without_refetch_or_rewrite(tmp_path,monkeypatch):
    import hashlib
    from apps.research import market_history as m
    sources={name:dict(url='public',sha256=hashlib.sha256(name.encode()).hexdigest(),bytes=len(name))
             for name in [*m.CATALOG_URLS,'bse_csv']}
    directory=tmp_path/'catalog-sources';directory.mkdir()
    for name in sources:(directory/(name+'.raw')).write_bytes(name.encode())
    (tmp_path/'catalog-sources.json').write_text(json.dumps(dict(sources=sources)))
    catalog=dict(entries=[entry('NSE:S'+str(n),symbol='S'+str(n),token=n+1) for n in range(1000)],
                 sources={k:v for k,v in sources.items() if k!='bse_zip'},policy=dict(scope='test'))
    (tmp_path/'catalog.json').write_text(json.dumps(catalog))
    before={p.name:p.read_bytes() for p in directory.iterdir()}
    monkeypatch.setattr(m,'fetch_catalogs',lambda **_:pytest.fail('frozen catalogs were fetched again'))
    monkeypatch.setattr(m,'existing_references',lambda *args:{})
    monkeypatch.setattr(m,'build_universe',lambda **kwargs:catalog)
    parent=dict(owner='owner',broker_hash='broker',end='2026-10-30')
    plan,result=m.prepare(tmp_path,parent,tmp_path/'old','engine')
    assert len(plan['new_symbols']['kite'])==1000
    assert before=={p.name:p.read_bytes() for p in directory.iterdir()}
    again,_=m.prepare(tmp_path,parent,tmp_path/'old','engine')
    assert again==plan


def test_missing_legacy_archive_never_means_redownload_old_stocks(tmp_path):
    from apps.research.market_history import existing_references
    with pytest.raises(ArchiveError,match='legacy'):
        existing_references({'kite':tmp_path/'missing'},dict(owner='owner',broker_hash='broker',end='2026-10-30'))


def test_provider_without_session_does_not_block_public_provider(tmp_path,monkeypatch):
    import threading
    from types import SimpleNamespace
    from apps.research import market_history as m
    cfg=dict(owner='owner',broker_hash='broker',symbols={'NSE:TCS':1},start='2026-01-01',daily_start='2026-01-01',
             end='2026-01-31',intervals=['1d'],provider='kite')
    a=Archive(tmp_path/'kite',cfg,append_only=True);states={}
    credentials=SimpleNamespace(context=lambda _:None)
    budget=m.ArtifactBudget(tmp_path,maximum=100*1024**2,reserve=0)
    m.run_provider('kite',a,credentials,cfg,{'NSE:TCS':entry()},budget,states,threading.Lock(),threading.Event(),False,'test')
    assert states['kite']=='awaiting_kite_login' and a.next_task()['attempts']==0
    y=Archive(tmp_path/'yahoo',cfg|dict(provider='yahoo'),append_only=True)
    def fetch(task,entries,observed):
        observed['coverage']=dict(first='2026-01-02T00:00:00+05:30',last='2026-01-02T00:00:00+05:30',rows=1)
        return [['2026-01-02T00:00:00+05:30',100,102,99,101,10]],dict(events=[])
    monkeypatch.setattr(m,'fetch_yahoo_listing',fetch)
    m.run_provider('yahoo',y,credentials,cfg,{'NSE:TCS':entry()},budget,states,threading.Lock(),threading.Event(),False,'test')
    assert states['yahoo']=='complete' and y.progress()['rows']==1
    with y.db() as c:assert c.execute("SELECT v FROM meta WHERE k LIKE 'coverage:%'").fetchone()


def test_shared_budget_reserves_both_provider_allocations(tmp_path):
    from apps.research.market_history import ArtifactBudget
    budget=ArtifactBudget(tmp_path,maximum=6*1024**2,reserve=0)
    with budget.allocation([]):
        with pytest.raises(ArchiveError,match='disk budget'):
            with budget.allocation([]):pass
    assert budget.reserved==0


def test_fatal_provider_error_stops_waiting_peer_and_fails_for_restart(tmp_path,monkeypatch):
    from apps.research import market_history as m
    import hashlib,sys
    owner='owner';prefix=hashlib.sha256(owner.encode()).hexdigest()
    parent=dict(owner=owner,broker_hash='broker',end='2000-01-31')
    plan=dict(end='2000-01-31',dataset_engine='test',catalog_sha256='test',
              new_symbols={'kite':{'NSE:TCS':1},'yahoo':{'NSE:TCS':'TCS.NS'}},reference_counts={'kite':0,'yahoo':0},references={},unavailable={})
    catalog=dict(entries=[entry()],counts={})
    monkeypatch.setattr(m,'snapshot',lambda _: (parent,prefix))
    monkeypatch.setattr(m,'prepare',lambda *args:(plan,catalog))
    monkeypatch.setattr(m,'Credentials',lambda:object())
    shared={}
    def provider(name,archive,credentials,parent,entries,budget,states,lock,stop,watch,version):
        shared['stop']=stop
        states[name]='worker_error' if name=='yahoo' else 'awaiting_kite_login'
    monkeypatch.setattr(m,'run_provider',provider)
    class FakeThread:
        def __init__(self,target,args,name):self.target=target;self.args=args
        def start(self):self.target(*self.args)
        def is_alive(self):return self.args[0]=='kite' and not shared['stop'].is_set()
        def join(self,timeout=None):pass
    monkeypatch.setattr(m.threading,'Thread',FakeThread)
    reports=[]
    def status(*args,**kwargs):
        assert shared['stop'].is_set(), 'fatal peer left background waiting forever'
        reports.append(kwargs.get('final'))
    monkeypatch.setattr(m,'write_status',status)
    monkeypatch.setattr(sys,'argv',['market','--base',str(tmp_path/'new'),'--parent-base',str(tmp_path/'parent'),
                                    '--history-base',str(tmp_path/'old'),'--watch'])
    with pytest.raises(ArchiveError,match='provider worker'):
        m.main()
    assert reports==[True]


def test_market_queue_prioritizes_nse_and_corroborated_bse_without_reset(tmp_path):
    from apps.research.market_history import MarketQueue
    items=[entry('BSE:200001','BSE','UNMAPPED',None),entry('BSE:500002','BSE','ABB',2),entry('NSE:NEW',symbol='NEW',token=3)]
    cfg=dict(owner='owner',symbols={e['key']:e['yahoo_symbol'] for e in items},start='2000-01-01',daily_start='2000-01-01',
             end='2026-10-30',intervals=['1d'],provider='yahoo',daily_chunk_days=10000)
    a=Archive(tmp_path,cfg,append_only=True);q=MarketQueue(a,{e['key']:e for e in items})
    first=q.next_task();assert first['symbol']=='NSE:NEW'
    a.complete(first,[['2000-01-03T00:00:00+05:30',100,102,99,101,10]])
    second=q.next_task();assert second['symbol']=='BSE:500002'
    a.claim(second);a.retry(second,'yahoo_history_unavailable',terminal=True)
    assert q.next_task()['symbol']=='BSE:200001'
    restarted=MarketQueue(a,{e['key']:e for e in items})
    assert restarted.next_task()['symbol']=='BSE:200001'
    assert a.progress()['completed']==1 and a.progress()['failed']==1
