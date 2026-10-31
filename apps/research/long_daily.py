"""Private daily-only history from2000, preserving separate provider series."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
import math
import os
import re
import sys
import time
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .archive import Archive, ArchiveError, ArchiveIntegrityError, IST, validate_rows
from .cli import Credentials, DownloadWorker, read_database
from .kite_cache import KiteDailyCache
from .replay import write_json

SETTINGS = dict(interval='1d',auto_adjust=False,back_adjust=False,repair=False,actions=True)


def normalize_yahoo(frame, start, end):
    if len(frame)>20000:
        raise ArchiveError('Yahoo daily response exceeds the archive bound')
    rows, events, rejected = [], [], []
    for stamp, record in frame.iterrows():
        at = stamp.to_pydatetime()
        if at.tzinfo is None:
            raise ArchiveError('Yahoo daily timezone is missing')
        at = at.astimezone(IST)
        # Daily keys are exchange-local dates, independent of chart label time.
        at = datetime.combine(at.date(),datetime.min.time(),IST)
        row=[at.isoformat(),*[float(record[k]) for k in ('Open','High','Low','Close','Volume')]]
        if not start<=at.date()<=end:
            raise ArchiveError('Yahoo daily response is outside the requested period')
        try:
            rows.extend(validate_rows([row],'1d',start,end))
        except ArchiveError as exc:
            rejected.append(dict(date=at.date().isoformat(),reason=str(exc),
                ohlcv=[v if math.isfinite(v) else None for v in row[1:]]))
        event = dict(date=at.date().isoformat())
        for column,key in [('Adj Close','adjusted_close'),('Dividends','dividends'),('Stock Splits','stock_splits')]:
            value = float(record[column]) if column in record else None
            event[key] = value if value is not None and math.isfinite(value) else None
        events.append(event)
    return validate_rows(rows,'1d',start,end),dict(settings=SETTINGS,events=events,rejected_rows=rejected,
        price_basis='Provider-supplied OHLC; auto_adjust=False does not imply historical as-traded prices.')


def compare_sources(kite, yahoo):
    def keyed(rows):return {r[0][:10]:r for r in rows}
    def coverage(rows):
        years={}
        for row in rows:years[row[0][:4]]=years.get(row[0][:4],0)+1
        return dict(rows=len(rows),first=rows[0][0][:10] if rows else None,last=rows[-1][0][:10] if rows else None,year_counts=years)
    left,right=keyed(kite),keyed(yahoo)
    overlap=sorted(left.keys()&right.keys())
    mismatch,volumes,examples=0,0,[]
    for day in overlap:
        a,b=left[day],right[day]
        different=any(abs(a[i]-b[i])>max(.01,abs(a[i])*.005) for i in range(1,5))
        mismatch+=different
        volumes+=abs(a[5]-b[5])>max(1,abs(a[5])*.05)
        if different and len(examples)<5:
            examples.append(dict(date=day,kite_close=a[4],yahoo_close=b[4]))
    return dict(kite=coverage(kite),yahoo=coverage(yahoo),overlap_days=len(overlap),
        kite_only_days=len(left.keys()-right.keys()),yahoo_only_days=len(right.keys()-left.keys()),
        ohlc_mismatch_days=mismatch,volume_mismatch_days=volumes,examples=examples,
        thresholds=dict(ohlc_relative=.005,ohlc_absolute=.01,volume_relative=.05),
        automatic_merge_allowed=False,
        note='Separate source series. Differences may reflect adjustments or data errors; equality is not guaranteed.')


def fetch_yahoo(task):
    import yfinance as yf
    yf.set_tz_cache_location('/tmp/drishti-long-daily-yahoo')
    logging.getLogger('yfinance').setLevel(logging.CRITICAL)
    # Prevent provider/library warnings from overwhelming operational logs.
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        frame=yf.Ticker(task['symbol']+'.NS').history(start=task['start'],
            end=str(date.fromisoformat(task['end'])+timedelta(days=1)),timeout=15,raise_errors=True,**SETTINGS)
    return normalize_yahoo(frame,date.fromisoformat(task['start']),date.fromisoformat(task['end']))


class YahooWorker:
    def __init__(self,archive,*,fetch=fetch_yahoo,sleep=time.sleep,clock=time.monotonic):
        self.archive,self.fetch,self.sleep,self.clock=archive,fetch,sleep,clock
        self.next_at=0.

    def step(self,task):
        a=self.archive; a.check_space()
        if a.append_only and (task.get('sha') or a.chunk_path(task).exists()):
            a.recover_existing();a.verify();verify_metadata(a)
            with a.db() as c:
                if c.execute('SELECT status FROM tasks WHERE key=?',(task['key'],)).fetchone()[0]=='complete':return
        if a.append_only and (a.root/(task['key']+'.metadata.json')).exists():
            raise ArchiveIntegrityError('incomplete saved Yahoo metadata requires review; history preserved')
        self.sleep(max(0,self.next_at-self.clock()))
        self.next_at=self.clock()+2.
        a.claim(task)
        try:
            rows,metadata=self.fetch(task)
            rows=validate_rows(rows,'1d',date.fromisoformat(task['start']),date.fromisoformat(task['end']))
            path=a.root/(task['key']+'.metadata.json')
            metadata=metadata|dict(provider='yahoo',symbol=task['symbol'],start=task['start'],end=task['end'],
                owner_hash=hashlib.sha256(a.config['owner'].encode()).hexdigest(),
                runtime_source_version=a.get_meta('runtime_source_version',a.config.get('engine_version')),
                fetched_at=datetime.now(timezone.utc).isoformat())
            write_json(path,metadata,create_only=a.append_only)
            a.set_meta('metadata:'+task['key'],hashlib.sha256(path.read_bytes()).hexdigest())
            a.complete(task,rows)
        except ArchiveIntegrityError:
            a.retry(task,'saved_source_integrity_review_required',refund=True)
            raise
        except ArchiveError:
            a.retry(task,'invalid_yahoo_candles',terminal=True)
        except Exception:
            # Never log upstream messages, URLs, cookies or headers.
            a.retry(task,'yahoo_request_failed')
            self.sleep(min(120,10*(task['attempts']+1)))


def verify_metadata(archive):
    with archive.db() as c:
        condition="status IN ('complete','pending','running')" if archive.append_only else "status='complete'"
        tasks=[dict(r) for r in c.execute('SELECT * FROM tasks WHERE '+condition)]
    for task in tasks:
        path=archive.root/(task['key']+'.metadata.json')
        if task['status']!='complete':
            if path.exists():
                raise ArchiveIntegrityError('incomplete saved Yahoo metadata requires review; history preserved')
            continue
        expected=archive.get_meta('metadata:'+task['key'])
        try:valid=expected and hashlib.sha256(path.read_bytes()).hexdigest()==expected
        except OSError:valid=False
        if not valid:
            if archive.append_only:
                raise ArchiveIntegrityError('Yahoo metadata integrity failure; saved history preserved for review')
            with archive.db() as c:
                c.execute("UPDATE tasks SET status='pending',attempts=0,error='metadata integrity re-download' WHERE key=?",(task['key'],))


def snapshot(parent_base):
    prefix=json.loads((parent_base/'current.json').read_text())['owner_hash']
    if not re.fullmatch('[0-9a-f]{64}',prefix):raise ArchiveError('invalid parent owner scope')
    with read_database(parent_base/prefix/'archive'/'manifest.sqlite') as c:
        config=json.loads(c.execute("SELECT v FROM meta WHERE k='config'").fetchone()[0])
    if hashlib.sha256(config['owner'].encode()).hexdigest()!=prefix:
        raise ArchiveError('parent owner scope mismatch')
    return config,prefix


def report(root,archives):
    records={}
    for sym in sorted(set(archives['kite'].config['symbols'])|set(archives['yahoo'].config['symbols'])):
        loaded={}
        for provider,archive in archives.items():
            try:loaded[provider]=archive.load(archive.config['owner'],sym,'1d')
            except ArchiveError:loaded[provider]=None
        if all(rows is not None for rows in loaded.values()):
            records[sym]=compare_sources(loaded['kite'],loaded['yahoo'])
            yahoo=archives['yahoo']
            with yahoo.db() as c:
                tasks=[dict(r) for r in c.execute("SELECT * FROM tasks WHERE symbol=? AND status='complete'",(sym,))]
            records[sym]['yahoo_rejected_rows']=sum(len(json.loads(
                (yahoo.root/(t['key']+'.metadata.json')).read_text()).get('rejected_rows',[])) for t in tasks)
        else:
            records[sym]=dict(status='provider_series_pending_or_failed',coverage={
                p:dict(rows=len(rows),first=rows[0][0][:10] if rows else None,last=rows[-1][0][:10] if rows else None)
                for p,rows in loaded.items() if rows is not None})
    write_json(root/'source-comparison.json',dict(requested_start='2000-01-01',symbols=records,
        generated_at=datetime.now(timezone.utc).isoformat(),
        automatic_merge_allowed=False,
        limitations=['Current constituents only; survivorship and missing/delisted stocks remain.',
          'Pre-listing and unavailable dates remain absent, not synthesized.',
          'Daily history cannot reconstruct intraday breakout entries/exits.',
          'Historical adjustment conventions and revisions can differ between providers.']))


def status(base,archives,phase):
    result=dict(phase=phase,at=datetime.now(timezone.utc).isoformat(),
        requested_start='2000-01-01',providers={p:a.progress() for p,a in archives.items()},
        automatic_merge_allowed=False,live_orders_enabled=False)
    write_json(base/'status.json',result)
    print(json.dumps(result),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base',required=True);p.add_argument('--parent-base',required=True)
    p.add_argument('--engine-version',default='unknown');p.add_argument('--watch',action='store_true')
    p.add_argument('--runtime-version',default=None)
    args=p.parse_args()
    base=Path(args.base).resolve();base.mkdir(parents=True,exist_ok=True,mode=0o700);base.chmod(0o700)
    lock=(base/'worker.lock').open('a');os.chmod(base/'worker.lock',0o600)
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise ArchiveError('long daily worker already active')
    parent,prefix=snapshot(Path(args.parent_base))
    root=base/prefix;root.mkdir(exist_ok=True,mode=0o700)
    archives={}
    for provider in ('kite','yahoo'):
        config=parent|dict(start='2000-01-01',daily_start='2000-01-01',intervals=['1d'],provider=provider,
            daily_chunk_days=1900 if provider=='kite' else 10000,engine_version=args.engine_version)
        if provider=='yahoo':
            config['symbols']=dict(parent['symbols'])|{s:None for s in parent.get('skipped_symbols',[])}
        path=root/provider
        archive=Archive(path,config,recover=True,append_only=True)
        archive.recover_existing();archive.verify()
        version=args.runtime_version or args.engine_version
        history=archive.get_meta('runtime_source_versions',[])
        if version not in history:archive.set_meta('runtime_source_versions',history+[version])
        archive.set_meta('runtime_source_version',version)
        if provider=='yahoo':verify_metadata(archive)
        archives[provider]=archive
    credentials=Credentials()
    def context():
        ctx=credentials.context(parent['owner'])
        return ctx if ctx and ctx.broker_hash==parent['broker_hash'] else None
    cache=KiteDailyCache(Path(args.parent_base)/prefix/'archive',archives['kite'].config)
    workers={'kite':DownloadWorker(archives['kite'],cache.wrap_context(context)),'yahoo':YahooWorker(archives['yahoo'])}
    # Separate worker at<=0.5Kite request/s, below the combined historical budget.
    next_kite_at=time.monotonic()
    last_status=0.
    status(base,archives,'downloading')
    while True:
        progressed=False; paused_disk=False; awaiting=False;warming=False
        for provider,archive in archives.items():
            task=archive.next_task()
            if task is None:continue
            if provider=='kite':
                ctx=context()
                if ctx is None or ctx.fingerprint==archive.get_meta('blocked_fingerprint'):
                    awaiting=True;continue
                headstart=(ctx.login_at+timedelta(minutes=30)-datetime.now(timezone.utc)).total_seconds()
                if headstart>0:
                    warming=True;continue
                # DownloadWorker enforces1s; this adds a second, slower bound.
                time.sleep(max(0,next_kite_at-time.monotonic()))
            try:
                done=workers[provider].step(task)
                if provider=='kite':next_kite_at=time.monotonic()+2.
                progressed=progressed or done is not False
            except ArchiveError as exc:
                if 'disk budget' not in str(exc):raise
                paused_disk=True
        now=time.monotonic()
        # Full comparisons grow with the entire archive. Keep that work out of
        # the download loop; cached progress stays cheap and reports run once.
        if now-last_status>=30:
            status(base,archives,'paused_disk_budget' if paused_disk else 'awaiting_kite_login' if awaiting else 'waiting_for_initial_warmup' if warming else 'downloading')
            last_status=now
        if all(a.next_task() is None for a in archives.values()):
            report(root,archives)
            status(base,archives,'complete' if not any(a.progress()['failed'] for a in archives.values()) else 'partial_with_failed_chunks')
            return
        if not progressed:
            if not args.watch:return
            time.sleep(60)


if __name__=='__main__':
    os.umask(0o077)
    try:main()
    except Exception as exc:
        print(json.dumps(dict(phase='worker_error',error_type=type(exc).__name__)),flush=True)
        sys.exit(1)
