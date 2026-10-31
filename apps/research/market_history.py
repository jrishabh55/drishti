"""Append-only current NSE/BSE ordinary-share daily history expansion."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date,datetime,timedelta,timezone
import fcntl
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
import warnings
import urllib.request

from .archive import Archive,ArchiveError,ArchiveIntegrityError,validate_rows
from .cli import Credentials,Context,DownloadWorker,read_database
from .long_daily import SETTINGS,YahooWorker,normalize_yahoo,snapshot,verify_metadata
from .market_universe import CATALOG_URLS,MAX_BYTES,build_universe,fetch_catalogs
from .replay import write_json

START='2000-01-01'


class HistoryUnavailable(ArchiveError):
    archive_reason='yahoo_history_unavailable'


class IdentityMismatch(ArchiveError):
    archive_reason='yahoo_identity_mismatch'


def validate_yahoo_identity(entry,metadata):
    venues={'NSE':{'NSI','NSE'},'BSE':{'BSE','BOM'}}
    if (not entry.get('yahoo_symbol') or metadata.get('symbol','').upper()!=entry['yahoo_symbol'].upper()
        or metadata.get('exchangeName') not in venues.get(entry['exchange'],set())
        or metadata.get('currency')!='INR' or metadata.get('instrumentType')!='EQUITY'):
        raise IdentityMismatch('Yahoo chart identity does not match the frozen listing')


def plan_targets(entries,references):
    keys=[e['key'] for e in entries]
    if len(keys)!=len(set(keys)) or not 1<=len(keys)<=20000:
        raise ArchiveError('ambiguous or out-of-bounds listing universe')
    symbols={'kite':{},'yahoo':{}};unavailable={};reused={};seen={'kite':set(),'yahoo':set()}
    for entry in entries:
        key=entry['key']
        for provider,field in [('kite','kite_token'),('yahoo','yahoo_symbol')]:
            old=references.get(key,{}).get(provider)
            if old:
                reused.setdefault(key,{})[provider]=dict(old)
                if provider=='kite' and entry.get(field) and old.get('token') and entry[field]!=old['token']:
                    reused[key][provider]['state']='existing_identity_review'
                continue
            value=entry.get(field)
            if value is None:
                unavailable.setdefault(key,[]).append(provider+'_mapping_unavailable')
                continue
            if provider=='kite' and (not isinstance(value,int) or isinstance(value,bool) or value<=0):
                raise ArchiveError('invalid planned broker token')
            if value in seen[provider]:raise ArchiveError('provider identity shared by distinct listings')
            seen[provider].add(value)
            symbols[provider][key]=value
    return dict(new_symbols=symbols,references=reused,unavailable=unavailable,
                reference_counts={p:sum(p in v for v in reused.values()) for p in symbols})


def existing_references(paths,expected):
    """Verify old source artifacts and reference them without opening a writer."""
    references={}
    for provider,path in paths.items():
        root=Path(path);manifest=root/'manifest.sqlite'
        if not manifest.exists():
            raise ArchiveIntegrityError('legacy history source missing; refusing duplicate download')
        with read_database(manifest) as c:
            c.row_factory=__import__('sqlite3').Row
            meta=dict(c.execute('SELECT k,v FROM meta'))
            cfg=json.loads(meta['config'])
            if any(cfg.get(k)!=expected.get(k) for k in ('owner','broker_hash','end')):
                raise ArchiveIntegrityError('legacy history scope does not match')
            if cfg.get('provider')!=provider or cfg.get('daily_start')!=START or cfg.get('intervals')!=['1d']:
                raise ArchiveIntegrityError('legacy history contract does not match')
            tasks=[dict(r) for r in c.execute("SELECT * FROM tasks WHERE interval='1d' ORDER BY symbol,start")]
        grouped={}
        for task in tasks:
            grouped.setdefault(task['symbol'],[]).append(task)
            key=task['key']
            if not re.fullmatch('[0-9a-f]{64}',key):raise ArchiveIntegrityError('invalid legacy source key')
            payload=root/'chunks'/(key+'.json.gz')
            if task['status']!='complete':
                if task['sha'] or payload.exists():
                    raise ArchiveIntegrityError('inconsistent legacy source checkpoint')
                continue
            data=payload.read_bytes()
            if len(data)!=task['bytes'] or hashlib.sha256(data).hexdigest()!=task['sha']:
                raise ArchiveIntegrityError('legacy history integrity failure')
            if provider=='yahoo':
                metadata=root/(key+'.metadata.json')
                expected_sha=json.loads(meta.get('metadata:'+key,'null'))
                if not expected_sha or hashlib.sha256(metadata.read_bytes()).hexdigest()!=expected_sha:
                    raise ArchiveIntegrityError('legacy Yahoo metadata integrity failure')
        for symbol,series in grouped.items():
            if symbol not in cfg['symbols'] or not re.fullmatch(r'[A-Z0-9][A-Z0-9&._-]{0,39}',symbol):
                raise ArchiveIntegrityError('legacy history symbol identity failure')
            references.setdefault('NSE:'+symbol,{})[provider]=dict(
                state='existing_complete' if all(t['status']=='complete' for t in series) else 'existing_partial',
                source=str(root),engine_version=cfg['engine_version'],token=cfg['symbols'][symbol] if provider=='kite' else None,
                completed_chunks=sum(t['status']=='complete' for t in series),
                failed_chunks=sum(t['status']=='failed' or t['attempts']>=6 for t in series),
                rows=sum(t['rows'] for t in series if t['status']=='complete'),
                requested_start=cfg['daily_start'],requested_end=cfg['end'])
    return references


class ArtifactBudget:
    def __init__(self,root,*,maximum=8*1024**3,reserve=5*1024**3):
        self.root=Path(root);self.maximum=maximum;self.reserve=reserve
        self.used=sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file())
        self.reserved=0;self.lock=threading.RLock()

    def check(self,estimate=4*1024**2):
        with self.lock:
            if self.used+self.reserved+estimate>self.maximum or shutil.disk_usage(self.root).free<self.reserve+estimate:
                raise ArchiveError('disk budget reached; collection paused')

    @contextmanager
    def allocation(self,paths):
        estimate=4*1024**2
        with self.lock:
            self.check(estimate);self.reserved+=estimate
            before=sum(p.stat().st_size for p in paths if p.exists())
        try:yield
        finally:
            after=sum(p.stat().st_size for p in paths if p.exists())
            with self.lock:
                self.used+=max(0,after-before);self.reserved-=estimate


def _write_bytes(path,value):
    path.parent.mkdir(exist_ok=True,parents=True,mode=0o700)
    if path.exists():
        if path.read_bytes()!=value:raise ArchiveIntegrityError('frozen catalog differs; saved data preserved')
        return
    temporary=path.with_suffix(path.suffix+'.tmp')
    try:
        with temporary.open('wb') as f:
            os.chmod(temporary,0o600);f.write(value);f.flush();os.fsync(f.fileno())
        os.link(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def prepare(base,parent,history_base,engine_version):
    prefix=hashlib.sha256(parent['owner'].encode()).hexdigest()
    plan_path=base/'plan.json';catalog_path=base/'catalog.json'
    if plan_path.exists():
        plan=json.loads(plan_path.read_text())
        if (plan['owner_hash']!=prefix or plan['broker_hash']!=parent['broker_hash'] or plan['end']!=parent['end']
            or plan['dataset_engine']!=engine_version or hashlib.sha256(catalog_path.read_bytes()).hexdigest()!=plan['catalog_sha256']):
            raise ArchiveIntegrityError('frozen market plan scope or catalog changed')
        catalog=json.loads(catalog_path.read_text())
        reconciled=plan_targets(catalog['entries'],plan['references'])
        if any(plan.get(k)!=v for k,v in reconciled.items()):
            raise ArchiveIntegrityError('frozen provider plan no longer matches its catalog')
        return plan,catalog
    source_path=base/'catalog-sources.json';source_dir=base/'catalog-sources'
    if source_path.exists():
        sources=json.loads(source_path.read_text())['sources'];artifacts={}
        if set(sources)!=set(CATALOG_URLS)|{'bse_csv'}:
            raise ArchiveIntegrityError('frozen catalog source set changed')
        for name,info in sources.items():
            path=source_dir/(name+'.raw')
            if not path.exists() or path.stat().st_size>MAX_BYTES:
                raise ArchiveIntegrityError('frozen public source missing or too large')
            data=path.read_bytes()
            if len(data)!=info['bytes'] or hashlib.sha256(data).hexdigest()!=info['sha256']:
                raise ArchiveIntegrityError('frozen public source integrity failure')
            artifacts[name]=data
        fetched=dict(artifacts=artifacts,metadata=sources,inputs={k:v for k,v in artifacts.items() if k!='bse_zip'})
    else:
        names={url:name for name,url in CATALOG_URLS.items()}
        def frozen_opener(request,timeout):
            path=source_dir/(names[request.full_url]+'.raw')
            if path.exists():
                if path.stat().st_size>MAX_BYTES:raise ArchiveIntegrityError('frozen public source too large')
                return io.BytesIO(path.read_bytes())
            return urllib.request.urlopen(request,timeout=timeout)
        fetched=fetch_catalogs(opener=frozen_opener)
        for name,value in fetched['artifacts'].items():_write_bytes(source_dir/(name+'.raw'),value)
        write_json(source_path,dict(fetched_at=datetime.now(timezone.utc).isoformat(),sources=fetched['metadata']),create_only=True)
    rebuilt=build_universe(**fetched['inputs'])
    if catalog_path.exists():
        catalog=json.loads(catalog_path.read_text())
        if catalog!=rebuilt:
            raise ArchiveIntegrityError('frozen market catalog source mismatch')
    else:
        catalog=rebuilt
        write_json(catalog_path,catalog,create_only=True)
    if not 1000<=len(catalog['entries'])<=20000:
        raise ArchiveError('official market universe outside operational bound')
    refs=existing_references({p:history_base/prefix/p for p in ('kite','yahoo')},parent)
    plan=plan_targets(catalog['entries'],refs)|dict(schema=1,owner_hash=prefix,broker_hash=parent['broker_hash'],
        start=START,end=parent['end'],dataset_engine=engine_version,
        catalog_sha256=hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
        created_at=datetime.now(timezone.utc).isoformat(),scope=catalog['policy']['scope'])
    write_json(plan_path,plan,create_only=True)
    return plan,catalog


def fetch_yahoo_listing(task,entries,observed):
    import yfinance as yf
    entry=entries[task['symbol']]
    yf.set_tz_cache_location('/tmp/drishti-market-yahoo')
    logging.getLogger('yfinance').setLevel(logging.CRITICAL)
    ticker=yf.Ticker(entry['yahoo_symbol'])
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            frame=ticker.history(start=task['start'],end=str(date.fromisoformat(task['end'])+timedelta(days=1)),
                                 timeout=15,raise_errors=True,**SETTINGS)
            metadata=ticker.history_metadata
    except Exception as exc:
        observed['error_type']=type(exc).__name__
        if type(exc).__name__ in ('YFPricesMissingError','YFTzMissingError','YFInvalidPeriodError'):
            raise HistoryUnavailable('Yahoo history unavailable for verified catalog listing') from None
        raise
    if frame.empty:raise HistoryUnavailable('Yahoo returned no historical candles')
    validate_yahoo_identity(entry,metadata)
    rows,details=normalize_yahoo(frame,date.fromisoformat(task['start']),date.fromisoformat(task['end']))
    coverage=dict(first=rows[0][0] if rows else None,last=rows[-1][0] if rows else None,rows=len(rows),
                  rejected_rows=len(details.get('rejected_rows',[])))
    observed['coverage']=coverage
    return rows,details|dict(listing_key=entry['key'],exchange=entry['exchange'],isin=entry['isin'],
        chart_identity={k:metadata.get(k) for k in ('symbol','exchangeName','currency','instrumentType')},coverage=coverage)


class ObservedClient:
    def __init__(self,client,entries_by_token,observed,source_version):
        self.client=client;self.entries=entries_by_token;self.observed=observed;self.version=source_version
        self.historical_provenance=None

    def historical(self,token,interval,start,end):
        entry=self.entries.get(token)
        if entry is None or interval!='day':raise ArchiveIntegrityError('market historical instrument not in frozen plan')
        rows=validate_rows(self.client.historical(token,interval,start,end),'1d',start.date(),end.date())
        coverage=dict(first=rows[0][0] if rows else None,last=rows[-1][0] if rows else None,rows=len(rows))
        self.observed['coverage']=coverage
        self.historical_provenance=dict(provider='kite',exchange=entry['exchange'],listing_key=entry['key'],
            isin=entry['isin'],source_version=self.version,coverage=coverage)
        return rows

    def __getattr__(self,name):return getattr(self.client,name)


class MarketQueue:
    """Indexed per-listing tasks; verified venues first, no state resets."""
    def __init__(self,archive,entries):
        self.archive=archive;self.position=0
        self.symbols=sorted(archive.config['symbols'],key=lambda key:(
            entries[key]['exchange']!='NSE',not bool(entries[key].get('kite_token')),key))

    def next_task(self):
        while self.position<len(self.symbols):
            with self.archive.db() as c:
                row=c.execute("SELECT * FROM tasks WHERE status='pending' AND interval='1d' "
                    "AND symbol=? AND attempts<6 ORDER BY start LIMIT 1",(self.symbols[self.position],)).fetchone()
            if row:return dict(row)
            self.position+=1
        return None


def run_provider(provider,archive,credentials,parent,entries,budget,states,state_lock,stop,watch,version):
    observed={};tokens={e['kite_token']:e for e in entries.values() if e.get('kite_token')}
    queue=MarketQueue(archive,entries)
    def update(phase):
        with state_lock:states[provider]=phase
    def context():
        ctx=credentials.context(parent['owner'])
        if ctx is None or ctx.broker_hash!=parent['broker_hash']:return None
        return Context(ObservedClient(ctx.client,tokens,observed,version),ctx.fingerprint,ctx.login_at,ctx.broker_hash)
    worker=DownloadWorker(archive,context) if provider=='kite' else YahooWorker(
        archive,fetch=lambda task:fetch_yahoo_listing(task,entries,observed))
    failures=0
    while not stop.is_set():
        task=queue.next_task()
        if task is None:
            update('partial_with_failed_chunks' if archive.progress()['failed'] else 'complete');return
        if provider=='kite':
            ctx=context()
            if ctx is None or ctx.fingerprint==archive.get_meta('blocked_fingerprint'):
                update('awaiting_kite_login')
                if not watch:return
                stop.wait(60);continue
            remaining=(ctx.login_at+timedelta(minutes=30)-datetime.now(timezone.utc)).total_seconds()
            if remaining>0:
                update('waiting_for_initial_warmup');stop.wait(min(60,remaining));continue
        observed.clear()
        paths=[archive.chunk_path(task),archive.root/(task['key']+'.metadata.json')]
        try:
            with budget.allocation(paths):worker.step(task)
            with archive.db() as c:
                result=c.execute('SELECT status,error FROM tasks WHERE key=?',(task['key'],)).fetchone()
            if result['status']=='complete':
                if observed.get('coverage'):archive.set_meta('coverage:'+task['key'],observed['coverage'])
                failures=0
            elif result['error']=='yahoo_request_failed':failures+=1
            else:failures=0
            update('downloading')
            if provider=='yahoo' and failures>=3:
                update('provider_backoff');stop.wait(600);failures=0
            if provider=='kite':stop.wait(1.)
        except ArchiveIntegrityError:
            update('paused_integrity_review');return
        except ArchiveError as exc:
            if 'disk budget' not in str(exc):raise
            update('paused_disk_budget')
            if not watch:return
            stop.wait(60)
        except Exception as exc:
            archive.set_meta('last_error_type',type(exc).__name__)
            update('worker_error');return


def write_status(base,plan,catalog,archives,states,budget,*,final=False):
    providers={}
    for p,a in archives.items():providers[p]=a.progress()|dict(phase=states.get(p,'starting'),reused_listings=plan['reference_counts'][p])
    finished='complete' if all(v=='complete' for v in states.values()) and len(states)==2 else 'partial_requires_review'
    doc=dict(at=datetime.now(timezone.utc).isoformat(),phase=finished if final else 'collecting',
        requested_start=START,requested_end=plan['end'],universe=catalog['counts'],providers=providers,
        unmapped_listings=len(plan['unavailable']),disk_bytes=budget.used,maximum_disk_bytes=budget.maximum,
        historical_files_append_only=True,automatic_merge_allowed=False,live_orders_enabled=False)
    write_json(base/'status.json',doc);print(json.dumps(doc),flush=True)
    if final:
        coverage={}
        for p,a in archives.items():
            with a.db() as c:
                rows=[dict(r) for r in c.execute('SELECT key,symbol,status,attempts,rows,error FROM tasks')]
                observed={k[9:]:json.loads(v) for k,v in c.execute("SELECT k,v FROM meta WHERE k LIKE 'coverage:%'")}
            by_symbol={}
            for task in rows:
                item=by_symbol.setdefault(task['symbol'],dict(chunks=0,completed_chunks=0,failed_chunks=0,candles=0,
                                                            first=None,last=None,error_counts={},coverage_unknown_chunks=0))
                item['chunks']+=1;item['completed_chunks']+=task['status']=='complete'
                item['failed_chunks']+=task['status']=='failed' or task['attempts']>=6
                if task['error']:item['error_counts'][task['error']]=item['error_counts'].get(task['error'],0)+1
                if task['status']=='complete':
                    item['candles']+=task['rows'];found=observed.get(task['key'])
                    if not found:item['coverage_unknown_chunks']+=1
                    elif found.get('first'):
                        item['first']=min(item['first'] or found['first'],found['first'])
                        item['last']=max(item['last'] or found['last'],found['last'])
            coverage[p]=by_symbol
        write_json(base/'coverage.json',dict(status=doc,coverage=coverage,existing_references=plan['references'],
            unavailable=plan['unavailable'],note='Requested date coverage is not proof of every exchange session; current stock catalogs exclude historical delistings.'))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base',required=True);p.add_argument('--parent-base',required=True);p.add_argument('--history-base',required=True)
    p.add_argument('--engine-version',default='unknown');p.add_argument('--runtime-version');p.add_argument('--watch',action='store_true')
    args=p.parse_args();base=Path(args.base).resolve();base.mkdir(parents=True,exist_ok=True,mode=0o700);base.chmod(0o700)
    lock=(base/'worker.lock').open('a');os.chmod(base/'worker.lock',0o600)
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise ArchiveError('market history worker already active')
    parent,prefix=snapshot(Path(args.parent_base));plan,catalog=prepare(base,parent,Path(args.history_base),args.engine_version)
    entries={e['key']:e for e in catalog['entries']};root=base/prefix;archives={}
    for provider in ('kite','yahoo'):
        cfg=dict(owner=parent['owner'],broker_hash=parent['broker_hash'],start=START,daily_start=START,end=plan['end'],
            intervals=['1d'],provider=provider,daily_chunk_days=1900 if provider=='kite' else 10000,
            symbols=plan['new_symbols'][provider],engine_version=plan['dataset_engine'],
            universe='frozen_current_NSE_BSE_ordinary_equity',catalog_sha256=plan['catalog_sha256'])
        a=Archive(root/provider,cfg,recover=True,append_only=True);a.recover_existing();a.verify()
        version=args.runtime_version or args.engine_version;a.set_meta('runtime_source_version',version)
        if provider=='yahoo':verify_metadata(a)
        archives[provider]=a
    budget=ArtifactBudget(base);credentials=Credentials();states={};state_lock=threading.Lock();stop=threading.Event()
    threads=[]
    def supervise(provider,archive):
        try:run_provider(provider,archive,credentials,parent,entries,budget,states,state_lock,stop,args.watch,version)
        except Exception:
            with state_lock:states[provider]='worker_error'
        finally:
            with state_lock:fatal=states.get(provider)=='worker_error'
            if fatal:stop.set()
    for provider,archive in archives.items():
        thread=threading.Thread(target=supervise,args=(provider,archive),name='history-'+provider)
        thread.start();threads.append(thread)
    try:
        while any(t.is_alive() for t in threads):
            with state_lock:state=dict(states)
            if 'worker_error' in state.values():
                stop.set()
                break
            write_status(base,plan,catalog,archives,state,budget);stop.wait(30)
        with state_lock:state=dict(states)
        fatal='worker_error' in state.values()
        if fatal:
            stop.set()
            for t in threads:t.join(timeout=20)
        with state_lock:state=dict(states)
        write_status(base,plan,catalog,archives,state,budget,final=True)
        if fatal:raise ArchiveError('provider worker failed; saved checkpoints preserved')
    finally:
        stop.set()
        for t in threads:t.join(timeout=20)


if __name__=='__main__':
    os.umask(0o077)
    try:main()
    except Exception as exc:
        print(json.dumps(dict(phase='worker_error',error_type=type(exc).__name__)),flush=True)
        sys.exit(1)
