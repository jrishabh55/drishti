"""Resumable private research worker. Broker access is GET history/instruments only.

Run in an isolated low-resource container with application database files mounted read-only. Their containing directories
allow SQLite WAL/SHM coordination; all record access is query-only. Session renewal remains the user's
normal Kite login; this utility never authenticates, refreshes or sends orders.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from apps.api.desk.profile import DeskProfile
from apps.api.kite import rest
from .archive import Archive, ArchiveError, IST
from .replay import choose_symbols, run_comparison, write_json


class ReadOnlyKiteClient(rest.KiteClient):
    def _call(self, method, path, *args, **kwargs):
        if method != 'GET' or not (path == '/instruments/NSE' or re.fullmatch(
                r'/instruments/historical/[0-9]+/(day|5minute)', path)):
            raise ArchiveError('research broker interface is read-only historical data')
        return super()._call(method, path, *args, **kwargs)


@dataclass
class Context:
    client: ReadOnlyKiteClient
    fingerprint: str
    login_at: datetime
    broker_hash: str


@contextmanager
def read_database(path):
    """Live WAL reads; allow SQLite sidecars, forbid application-record writes."""
    connection = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True, timeout=15)
    try:
        connection.execute('PRAGMA query_only=ON')
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
        connection.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
        yield connection
    finally:
        connection.close()


class Credentials:
    def __init__(self):
        self.path = Path(os.environ.get('KITE_DB_PATH','data/kite.sqlite')).resolve()
        secret = os.environ.get('WEBAPP_KEY_ENCRYPTION_SECRET','')
        if not secret:
            raise ArchiveError('credential encryption is unavailable')
        self.fernet = Fernet(secret.encode())

    def context(self, owner):
        with read_database(self.path) as c:
            records = dict(c.execute("SELECT kind,ciphertext FROM kite_secrets WHERE user_id=? AND kind IN ('app','session')", (owner,)))
        try:
            app = json.loads(self.fernet.decrypt(records['app'].encode()))
            sess = json.loads(self.fernet.decrypt(records['session'].encode()))
            expiry = datetime.fromisoformat(sess['expires_at'])
            login = datetime.fromisoformat(sess['login_at'])
            if expiry.tzinfo is None or login.tzinfo is None or sess.get('revoked') or expiry <= datetime.now(timezone.utc):
                return None
            broker = str(sess['kite_user_id'])
            return Context(ReadOnlyKiteClient(app['api_key'],sess['access_token']),
                hashlib.sha256((records['app']+records['session']).encode()).hexdigest(),
                login, hashlib.sha256(broker.encode()).hexdigest())
        except (KeyError, ValueError, TypeError, InvalidToken):
            return None

    def owner(self):
        with read_database(self.path) as c:
            owners = [r[0] for r in c.execute("SELECT user_id FROM kite_secrets WHERE kind='app'")]
        valid = [u for u in owners if self.context(u) is not None]
        if len(valid) > 1:
            raise ArchiveError('multiple active connection owners; explicit research scope required')
        return valid[0] if valid else None


def profile_snapshot(owner):
    path = Path(os.environ.get('DESK_DB_PATH','data/desk.sqlite')).resolve()
    with read_database(path) as c:
        row = c.execute('SELECT doc FROM desk_profiles WHERE user_id=?',(owner,)).fetchone()
    return DeskProfile(**(json.loads(row[0]) if row else {})).model_dump()


def make_config(owner, credentials, engine_version):
    ctx = credentials.context(owner)
    if ctx is None:
        raise ArchiveError('daily login is required')
    # One instrument request; the downloader then starts at one request/second.
    table = rest.parse_instruments(ctx.client.instruments_csv())
    after = credentials.context(owner)
    if after is None or after.fingerprint != ctx.fingerprint:
        raise ArchiveError('connection changed during instrument lookup')
    path = Path(os.environ.get('SCANNER_DB_PATH','data/scanner.sqlite')).resolve()
    with read_database(path) as c:
        current = [s for s,members in c.execute('SELECT symbol,index_memberships FROM instruments')
                   if 'NIFTY500' in json.loads(members)]
    if not 1 <= len(current) <= 550:
        raise ArchiveError('NIFTY500 membership is unavailable or exceeds the research bound')
    symbols = {s:table[s]['token'] for s in sorted(current) if s in table}
    if not symbols or len(symbols) > 500:
        raise ArchiveError('eligible instrument universe is outside the research bound')
    now = datetime.now(IST)
    end = now.date() if now.hour*60+now.minute >= 15*60+35 else now.date()-timedelta(days=1)
    start = end-timedelta(days=729)
    return dict(owner=owner, broker_hash=ctx.broker_hash, symbols=symbols,
         skipped_symbols=sorted(set(current)-set(symbols)), start=str(start),end=str(end),
         daily_start=str(start-timedelta(days=365)), profile=profile_snapshot(owner),
         engine_version=engine_version, universe='frozen_current_NIFTY500',
         disclosure='Survivorship, historical membership, corporate actions and exchange facts are not reconstructed.')


class DownloadWorker:
    def __init__(self, archive, context, *, clock=time.monotonic, sleep=time.sleep):
        self.archive, self.context, self.clock, self.sleep = archive,context,clock,sleep
        self.next_at = 0.

    def step(self, task):
        a = self.archive
        a.check_space()
        ctx = self.context()
        if ctx is None or ctx.fingerprint == a.get_meta('blocked_fingerprint'):
            a.set_meta('phase','awaiting_login')
            return False
        wait = max(0., self.next_at-self.clock())
        if wait:
            self.sleep(wait)
        # Recheck authorization after any rate wait as well as after the HTTP.
        current = self.context()
        if current is None or current.fingerprint != ctx.fingerprint:
            a.set_meta('phase','awaiting_login')
            return False
        a.claim(task)
        self.next_at = self.clock()+1.
        try:
            rows = ctx.client.historical(a.config['symbols'][task['symbol']],
                'day' if task['interval']=='1d' else '5minute',
                datetime.combine(datetime.fromisoformat(task['start']).date(),datetime.min.time(),IST),
                datetime.combine(datetime.fromisoformat(task['end']).date(),datetime.max.time().replace(microsecond=0),IST))
            after = self.context()
            if after is None or after.fingerprint != ctx.fingerprint:
                a.retry(task,'connection_changed',refund=True)
                a.set_meta('phase','awaiting_login')
                return False
            a.complete(task, rows)
            a.set_meta('phase','downloading')
        except rest.KiteError as exc:
            if exc.token_error:
                a.retry(task,'daily_login_required',refund=True)
                a.set_meta('blocked_fingerprint',ctx.fingerprint)
                a.set_meta('phase','awaiting_login')
                return False
            transient = exc.status in (0,408,429,500,502,503,504)
            a.retry(task,f'KiteHTTP{int(exc.status)}',terminal=not transient)
            if transient:
                attempt = task['attempts']
                self.sleep((10,15,30,60,120,300)[min(attempt,5)])
        except ArchiveError:
            a.retry(task,'invalid_candles',terminal=True)
        return True


def status(archive, **extra):
    doc = archive.progress() | dict(phase=archive.get_meta('phase','starting'),
        research_complete=archive.get_meta('research_complete',False),
        research_completed_variants=archive.get_meta('research_completed_variants',0),
        at=datetime.now(timezone.utc).isoformat(), **extra)
    write_json(archive.root/'status.json',doc)
    print(json.dumps(doc),flush=True)


def step_or_pause(worker, task, watch):
    """Apply the same disk guard to initial probes and bulk downloads."""
    try:
        worker.step(task)
    except ArchiveError as exc:
        if 'disk budget' not in str(exc):
            raise
        worker.archive.set_meta('phase','paused_disk_budget')
        status(worker.archive)
        if not watch:
            raise SystemExit(0)
        time.sleep(60)


def prepare_research(archive):
    selected = archive.get_meta('research_symbols')
    if not selected:
        with archive.db() as c:
            daily_remaining = c.execute("SELECT COUNT(*) FROM tasks WHERE interval='1d' AND status IN ('pending','running') AND attempts<6").fetchone()[0]
        if not daily_remaining:
            daily = {}
            for sym in archive.config['symbols']:
                try:
                    daily[sym] = archive.load(archive.config['owner'],sym,'1d')
                except ArchiveError:
                    pass   # failed series stay explicit in the manifest; never fill them
            selected = choose_symbols(daily,datetime.fromisoformat(archive.config['start']).date())
            if len(selected)<5:
                raise ArchiveError('insufficient pre-period data for research selection')
            archive.set_meta('research_symbols',selected)
            del daily
    if selected and not archive.get_meta('research_complete'):
        with archive.db() as c:
            missing = c.execute("SELECT COUNT(*) FROM tasks WHERE interval='5m' AND symbol IN ("+
                   ','.join('?' for _ in selected)+") AND status!='complete'",selected).fetchone()[0]
        if not missing:
            archive.set_meta('phase','research'); status(archive)
            run_comparison(archive)
            status(archive)
    return selected


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base',required=True)
    p.add_argument('--engine-version',default='unknown')
    p.add_argument('--watch',action='store_true')
    args = p.parse_args()
    base = Path(args.base).resolve()
    base.mkdir(parents=True,exist_ok=True,mode=0o700); base.chmod(0o700)
    # One lock covers pointer creation, recovery and all network/replay work.
    lock = (base/'worker.lock').open('a'); os.chmod(base/'worker.lock',0o600)
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('research worker already active')
    credentials = Credentials()
    pointer = base/'current.json'
    if pointer.exists():
        prefix = json.loads(pointer.read_text())['owner_hash']
        if not re.fullmatch('[0-9a-f]{64}',prefix):
            raise ArchiveError('invalid archive scope pointer')
        archive = Archive(base/prefix/'archive',recover=True)
    else:
        owner = credentials.owner()
        if owner is None:
            raise ArchiveError('log in to Kite before starting the research job')
        config = make_config(owner,credentials,args.engine_version)
        prefix = hashlib.sha256(owner.encode()).hexdigest()
        archive = Archive(base/prefix/'archive',config)
        write_json(pointer,dict(owner_hash=prefix))
    archive.verify()
    def context():
        ctx = credentials.context(archive.config['owner'])
        return ctx if ctx and ctx.broker_hash == archive.config['broker_hash'] else None
    worker = DownloadWorker(archive,context)
    last_status = 0.
    while True:
        selected = prepare_research(archive)
        if archive.next_task(selected) is None:
            phase = 'complete' if archive.progress()['failed']==0 else 'partial_with_failed_chunks'
            archive.set_meta('phase',phase); status(archive); return
        ctx = context()
        if ctx is None or ctx.fingerprint == archive.get_meta('blocked_fingerprint'):
            archive.set_meta('phase','awaiting_login'); status(archive)
            if not args.watch:
                return
            time.sleep(60); continue
        # Existing in-process API warmup gets a30-minute head start. Afterward
        # this worker stays at1req/s, leaving room under Kite's3req/s budget.
        wait = max(0., (ctx.login_at+timedelta(minutes=30)-datetime.now(timezone.utc)).total_seconds())
        if wait:
            archive.set_meta('phase','waiting_for_initial_warmup'); status(archive,wait_seconds=round(wait))
            time.sleep(min(wait,60)); continue
        # Probe an old daily and90-day intraday request before thousands of calls.
        if not archive.get_meta('probe_complete'):
            probe_symbol = 'RELIANCE' if 'RELIANCE' in archive.config['symbols'] else sorted(archive.config['symbols'])[0]
            with archive.db() as c:
                probes = [dict(c.execute('SELECT * FROM tasks WHERE symbol=? AND interval=? ORDER BY start LIMIT 1',(probe_symbol,iv)).fetchone()) for iv in ('1d','5m')]
            failed_probe = next((t for t in probes if t['status']=='failed' or t['attempts']>=6),None)
            if failed_probe:
                archive.set_meta('phase','probe_failed'); status(archive); return
            pending_probe = next((t for t in probes if t['status']!='complete'),None)
            if pending_probe:
                step_or_pause(worker,pending_probe,args.watch); status(archive); continue
            if any(t['rows']==0 for t in probes):
                archive.set_meta('phase','probe_empty'); status(archive); return
            archive.set_meta('probe_complete',True)
        task = archive.next_task(selected)
        if task is None:
            phase = 'complete' if archive.progress()['failed']==0 else 'partial_with_failed_chunks'
            archive.set_meta('phase',phase); status(archive)
            return
        step_or_pause(worker,task,args.watch)
        if time.monotonic()-last_status >= 30:
            status(archive); last_status=time.monotonic()


if __name__ == '__main__':
    os.umask(0o077)
    main()
