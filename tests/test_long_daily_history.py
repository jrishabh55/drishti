from datetime import date
import json

import pandas as pd
import pytest

from apps.research.archive import Archive, ArchiveError


def config(provider='kite'):
    return dict(owner='owner',symbols={'RELIANCE':1},start='2000-01-01',
                daily_start='2000-01-01',end='2026-10-30',intervals=['1d'],
                provider=provider,daily_chunk_days=1900)


def test_daily_only_chunks_are_bounded_and_source_stamped(tmp_path):
    a=Archive(tmp_path,config())
    with a.db() as c:tasks=[dict(r) for r in c.execute('SELECT * FROM tasks ORDER BY start')]
    assert len(tasks)==6
    assert all(t['interval']=='1d' and (date.fromisoformat(t['end'])-date.fromisoformat(t['start'])).days<1900 for t in tasks)
    for left,right in zip(tasks,tasks[1:]):
        assert (date.fromisoformat(right['start'])-date.fromisoformat(left['end'])).days==1
    a.complete(tasks[0],[['2000-01-03T00:00:00+05:30',100,102,99,101,1000]])
    import gzip
    doc=json.loads(gzip.decompress(a.chunk_path(tasks[0]).read_bytes()))
    assert doc['provider']=='kite'


def test_yahoo_normalization_preserves_adjustment_metadata():
    from apps.research.long_daily import normalize_yahoo
    frame=pd.DataFrame({'Open':[100.], 'High':[102.], 'Low':[99.], 'Close':[101.],
                        'Volume':[1000], 'Adj Close':[90.], 'Dividends':[1.], 'Stock Splits':[0.]},
                       index=pd.DatetimeIndex(['2000-01-03'],tz='Asia/Kolkata'))
    rows,metadata=normalize_yahoo(frame,date(2000,1,1),date(2000,1,31))
    assert rows[0][4]==101
    assert metadata['events'][0]['adjusted_close']==90
    assert metadata['settings']['auto_adjust'] is False
    assert metadata['events'][0]['dividends']==1


def test_comparison_flags_price_basis_difference_without_merging():
    from apps.research.long_daily import compare_sources
    kite=[['2008-01-01T00:00:00+05:30',339,340,338,339.4,1000]]
    yahoo=[['2008-01-01T00:00:00+05:30',325,326,324,325.531,1000]]
    report=compare_sources(kite,yahoo)
    assert report['overlap_days']==1
    assert report['ohlc_mismatch_days']==1
    assert report['automatic_merge_allowed'] is False
    assert report['kite']['first']=='2008-01-01'
    assert kite[0][4]==339.4


def test_yahoo_metadata_is_checksummed_and_recovers(tmp_path):
    from apps.research.long_daily import YahooWorker, verify_metadata
    cfg=config('yahoo')|dict(daily_chunk_days=10000)
    a=Archive(tmp_path,cfg);task=a.next_task()
    rows=[['2000-01-03T00:00:00+05:30',100,102,99,101,1000]]
    worker=YahooWorker(a,fetch=lambda _: (rows,dict(settings={},events=[])),sleep=lambda _:None)
    worker.step(task)
    assert a.progress()['completed']==1
    metadata=a.root/(task['key']+'.metadata.json')
    assert metadata.stat().st_mode&0o077==0
    metadata.write_text('{}')
    verify_metadata(a)
    assert a.progress()['completed']==0
    assert a.next_task()['attempts']==0


def test_yahoo_invalid_data_and_transient_failure_stay_explicit(tmp_path):
    from apps.research.long_daily import YahooWorker
    a=Archive(tmp_path,config('yahoo')|dict(daily_chunk_days=10000))
    task=a.next_task()
    def failure(_):raise RuntimeError('private upstream details')
    worker=YahooWorker(a,fetch=failure,sleep=lambda _:None)
    worker.step(task)
    assert a.next_task()['error']=='yahoo_request_failed'
    worker.fetch=lambda _:([['2000-01-03T00:00:00+05:30',100,102,99,float('nan'),10]],{})
    worker.step(a.next_task())
    assert a.progress()['failed']==1
    assert a.progress()['completed']==0


def test_legacy_archive_task_configuration_unchanged(tmp_path):
    from tests.test_weekend_research import config as old_config
    a=Archive(tmp_path,old_config())
    assert a.progress()['total']==20
    with a.db() as c:
        assert c.execute("SELECT COUNT(*) FROM tasks WHERE interval='1d'").fetchone()[0]==2


def test_incomplete_latest_yahoo_candle_does_not_discard_valid_history():
    from apps.research.long_daily import normalize_yahoo
    frame=pd.DataFrame({'Open':[100.,110.], 'High':[102.,112.], 'Low':[99.,109.],
                        'Close':[101.,float('nan')], 'Volume':[1000,100],
                        'Adj Close':[90.,float('nan')], 'Dividends':[0.,0.], 'Stock Splits':[0.,0.]},
                       index=pd.DatetimeIndex(['2000-01-03','2000-01-04'],tz='Asia/Kolkata'))
    rows,meta=normalize_yahoo(frame,date(2000,1,1),date(2000,1,31))
    assert len(rows)==1 and rows[0][4]==101
    assert meta['rejected_rows'][0]['date']=='2000-01-04'
    assert meta['rejected_rows'][0]['ohlcv'][3] is None
    json.dumps(meta,allow_nan=False)
