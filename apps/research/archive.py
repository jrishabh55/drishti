"""Owner-scoped, immutable-config candle archive for bounded research jobs."""
from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import math
import os
import shutil
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')


class ArchiveError(ValueError):
    pass


def validate_rows(rows, interval, start, end):
    if interval not in ('1d', '5m') or len(rows) > 20000:
        raise ArchiveError('invalid candle interval or chunk size')
    unique = {}
    for r in rows:
        if not isinstance(r, (list, tuple)) or len(r) < 6:
            raise ArchiveError('malformed candle')
        try:
            stamp = datetime.fromisoformat(r[0])
            if stamp.tzinfo is None:
                raise ArchiveError('candle timezone is missing')
            stamp = stamp.astimezone(IST)
            vals = [float(v) for v in r[1:6]]
        except (ValueError, TypeError, OverflowError) as exc:
            raise ArchiveError('invalid candle values') from exc
        if not start <= stamp.date() <= end:
            raise ArchiveError('candle outside requested range')
        if interval == '5m' and (stamp.minute % 5 or stamp.second or stamp.microsecond):
            raise ArchiveError('candle is off the five-minute grid')
        o, h, l, c, v = vals
        if not all(math.isfinite(x) for x in vals) or min(o, h, l, c) <= 0 or v < 0:
            raise ArchiveError('nonfinite or nonpositive candle')
        if h < max(o, l, c) or l > min(o, h, c):
            raise ArchiveError('incoherent candle range')
        row = [stamp.isoformat(), *vals]
        if stamp in unique and unique[stamp] != row:
            raise ArchiveError('conflicting duplicate candle')
        unique[stamp] = row
    return [unique[t] for t in sorted(unique)]


class Archive:
    def __init__(self, root, config=None, *, recover=False):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        (self.root/'chunks').mkdir(exist_ok=True, mode=0o700)
        self.path = self.root/'manifest.sqlite'
        with self.db() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY,v TEXT);
            CREATE TABLE IF NOT EXISTS tasks(key TEXT PRIMARY KEY,symbol TEXT,interval TEXT,
             start TEXT,end TEXT,status TEXT DEFAULT 'pending',attempts INTEGER DEFAULT 0,
             rows INTEGER DEFAULT 0,sha TEXT,bytes INTEGER DEFAULT 0,error TEXT);''')
            old = c.execute("SELECT v FROM meta WHERE k='config'").fetchone()
            encoded = json.dumps(config, sort_keys=True, allow_nan=False) if config is not None else None
            if old and encoded is not None and old[0] != encoded:
                raise ArchiveError('archive configuration does not match')
            if not old:
                if config is None:
                    raise ArchiveError('archive configuration is required')
                c.execute("INSERT INTO meta VALUES ('config',?)", (encoded,))
                tasks = []
                for sym in sorted(config['symbols']):
                    intervals = config.get('intervals', ['1d','5m'])
                    if not intervals or set(intervals)-{'1d','5m'}:
                        raise ArchiveError('invalid archive intervals')
                    ranges = []
                    if '1d' in intervals:
                        at, end = date.fromisoformat(config['daily_start']), date.fromisoformat(config['end'])
                        chunk_days = int(config.get('daily_chunk_days',1900))
                        if not 1 <= chunk_days <= 10000:
                            raise ArchiveError('invalid daily chunk bound')
                        while at <= end:
                            to = min(at+timedelta(days=chunk_days-1),end)
                            ranges.append(('1d',at,to)); at = to+timedelta(days=1)
                    at, end = date.fromisoformat(config['start']), date.fromisoformat(config['end'])
                    while '5m' in intervals and at <= end:
                        to = min(at+timedelta(days=89), end)
                        ranges.append(('5m', at, to))
                        at = to+timedelta(days=1)
                    for interval, start, end in ranges:
                        key = hashlib.sha256(f'{sym}|{interval}|{start}|{end}'.encode()).hexdigest()
                        tasks.append((key, sym, interval, start.isoformat(), end.isoformat()))
                c.executemany('INSERT INTO tasks(key,symbol,interval,start,end) VALUES (?,?,?,?,?)', tasks)
            self.config = json.loads(old[0] if old else encoded)
            if recover:
                c.execute("UPDATE tasks SET status='pending' WHERE status='running'")
        self.path.chmod(0o600)

    @contextlib.contextmanager
    def db(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def set_meta(self, key, value):
        with self.db() as c:
            c.execute('INSERT INTO meta VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v',
                      (key, json.dumps(value, allow_nan=False)))

    def get_meta(self, key, default=None):
        with self.db() as c:
            r = c.execute('SELECT v FROM meta WHERE k=?', (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def next_task(self, selected=None):
        with self.db() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM tasks WHERE status='pending' AND attempts<6 "
                                               "ORDER BY CASE interval WHEN '1d' THEN 0 ELSE 1 END,symbol,start")]
        if selected:
            daily = [r for r in rows if r['interval'] == '1d']
            if daily:
                return daily[0]
            preferred = [r for r in rows if r['symbol'] in selected]
            if preferred:
                return preferred[0]
        return rows[0] if rows else None

    def claim(self, task):
        with self.db() as c:
            n = c.execute("UPDATE tasks SET status='running',attempts=attempts+1 WHERE key=? AND status='pending'",
                          (task['key'],)).rowcount
        if n != 1:
            raise ArchiveError('task was already claimed')

    def retry(self, task, reason, *, terminal=False, refund=False):
        with self.db() as c:
            c.execute("UPDATE tasks SET status=?,error=? WHERE key=?",
                      ('failed' if terminal else 'pending', reason[:160], task['key']))
            if refund:
                c.execute('UPDATE tasks SET attempts=MAX(0,attempts-1) WHERE key=?', (task['key'],))

    def chunk_path(self, task):
        if len(task['key']) != 64 or any(ch not in '0123456789abcdef' for ch in task['key']):
            raise ArchiveError('invalid chunk key')
        return self.root/'chunks'/f"{task['key']}.json.gz"

    def check_space(self, reserve=5*1024**3, maximum=4*1024**3):
        if shutil.disk_usage(self.root).free < reserve or self.progress()['bytes'] >= maximum:
            raise ArchiveError('disk budget reached; download paused')

    def complete(self, task, rows):
        rows = validate_rows(rows, task['interval'], date.fromisoformat(task['start']), date.fromisoformat(task['end']))
        doc = dict(schema=1, owner_hash=hashlib.sha256(self.config['owner'].encode()).hexdigest(),
                   symbol=task['symbol'], interval=task['interval'], start=task['start'], end=task['end'], rows=rows)
        if 'provider' in self.config:
            doc['provider'] = self.config['provider']
        payload = gzip.compress(json.dumps(doc, separators=(',', ':'), allow_nan=False).encode(), mtime=0)
        path = self.chunk_path(task)
        temp = path.with_name(path.name+f'.tmp-{os.getpid()}')
        try:
            with temp.open('wb') as f:
                os.chmod(temp, 0o600)
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, path)
            with self.db() as c:
                c.execute("UPDATE tasks SET status='complete',rows=?,sha=?,bytes=?,error=NULL WHERE key=?",
                          (len(rows), hashlib.sha256(payload).hexdigest(), len(payload), task['key']))
        finally:
            temp.unlink(missing_ok=True)

    def verify(self):
        with self.db() as c:
            done = [dict(r) for r in c.execute("SELECT * FROM tasks WHERE status='complete'")]
        for t in done:
            try:
                valid = hashlib.sha256(self.chunk_path(t).read_bytes()).hexdigest() == t['sha']
            except OSError:
                valid = False
            if not valid:
                with self.db() as c:
                    c.execute("UPDATE tasks SET status='pending',attempts=0,error='integrity re-download' WHERE key=?", (t['key'],))

    def load(self, owner, symbol, interval):
        if owner != self.config['owner']:
            raise ArchiveError('archive owner does not match')
        with self.db() as c:
            tasks = [dict(r) for r in c.execute('SELECT * FROM tasks WHERE symbol=? AND interval=? ORDER BY start', (symbol, interval))]
        if not tasks or any(t['status'] != 'complete' for t in tasks):
            raise ArchiveError('requested archive series is not complete')
        all_rows = []
        for t in tasks:
            try:
                data = self.chunk_path(t).read_bytes()
                if hashlib.sha256(data).hexdigest() != t['sha']:
                    raise ArchiveError('archive integrity failure')
                doc = json.loads(gzip.decompress(data))
                if doc['owner_hash'] != hashlib.sha256(owner.encode()).hexdigest() or doc['symbol'] != symbol or doc['interval'] != interval:
                    raise ArchiveError('archive integrity metadata failure')
                if 'provider' in self.config and doc.get('provider') != self.config['provider']:
                    raise ArchiveError('archive integrity provider failure')
                all_rows.extend(validate_rows(doc['rows'], interval, date.fromisoformat(t['start']), date.fromisoformat(t['end'])))
            except (OSError, ValueError, KeyError, gzip.BadGzipFile) as exc:
                raise ArchiveError('archive integrity failure') from exc
        start = date.fromisoformat(self.config['daily_start'] if interval == '1d' else self.config['start'])
        return validate_rows(all_rows, interval, start, date.fromisoformat(self.config['end'])) if len(all_rows) <= 20000 else self._merge_long(all_rows, interval, start)

    def _merge_long(self, rows, interval, start):
        # Validation stays chunk-bounded; all chunks were checked above. Dedup
        # exact timestamps across chunks without accepting conflicting values.
        result = {}
        for r in rows:
            if r[0] in result and result[r[0]] != r:
                raise ArchiveError('archive integrity duplicate conflict')
            result[r[0]] = r
        return [result[t] for t in sorted(result)]

    def progress(self):
        with self.db() as c:
            total = c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
            r = c.execute("SELECT COUNT(*),COALESCE(SUM(rows),0),COALESCE(SUM(bytes),0),COALESCE(SUM(rows=0),0) FROM tasks WHERE status='complete'").fetchone()
            failed = c.execute("SELECT COUNT(*) FROM tasks WHERE status='failed' OR (status='pending' AND attempts>=6)").fetchone()[0]
        return dict(total=total, completed=r[0], rows=r[1], bytes=r[2], empty=r[3], failed=failed)
