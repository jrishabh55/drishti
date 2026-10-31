"""Read-only reuse of verified owner-scoped Kite daily archive segments."""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import sqlite3
import time
import zlib
from datetime import date, datetime, timedelta
from pathlib import Path

from apps.api.kite.rest import KiteError
from .archive import ArchiveError, ArchiveIntegrityError, IST, validate_rows
from .cli import Context, read_database


class KiteDailyCache:
    """Wrap credential contexts without changing their identity or broker guards.

    Completed parent task intervals represent coverage, including actual missing
    sessions. Missing candles inside a saved interval are never filled or fetched.
    Only uncovered date intervals reach the original read-only broker client.
    """

    def __init__(self, parent_root, expected_config, *, clock=time.monotonic, sleep=time.sleep):
        self.root = Path(parent_root)
        self.clock, self.sleep = clock, sleep
        self.next_network_at = 0.
        self.last_provenance = {}
        self.config = self._config()
        try:
            if any(self.config.get(k) != expected_config.get(k) for k in ('owner', 'broker_hash', 'end')):
                raise ArchiveIntegrityError('parent archive owner/account/date scope mismatch')
            if not self.config['owner'] or not self.config['broker_hash'] or not self.config['engine_version']:
                raise ArchiveIntegrityError('parent archive source identity is missing')
            if self.config.get('provider', 'kite') != 'kite' or expected_config.get('provider', 'kite') != 'kite':
                raise ArchiveIntegrityError('parent archive provider mismatch')
            self.start = date.fromisoformat(self.config['daily_start'])
            self.end = date.fromisoformat(self.config['end'])
            if self.start > self.end:
                raise ArchiveIntegrityError('parent archive date scope is invalid')
            self.tokens = {}
            for symbol, token in self.config['symbols'].items():
                if not isinstance(token, int) or isinstance(token, bool) or token <= 0 or token in self.tokens:
                    raise ArchiveIntegrityError('parent archive token identity is invalid')
                if expected_config['symbols'].get(symbol) != token:
                    raise ArchiveIntegrityError('parent archive instrument identity mismatch')
                self.tokens[token] = symbol
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ArchiveIntegrityError):
                raise
            raise ArchiveIntegrityError('parent archive configuration integrity failure') from exc

    def _config(self):
        try:
            with read_database(self.root/'manifest.sqlite') as connection:
                row = connection.execute("SELECT v FROM meta WHERE k='config'").fetchone()
            return json.loads(row[0])
        except (OSError, sqlite3.Error, ValueError, TypeError, IndexError) as exc:
            raise ArchiveIntegrityError('parent archive manifest integrity failure') from exc

    def wrap_context(self, factory):
        """Return a factory with the same session semantics as the given factory."""
        def current():
            original = factory()
            if original is None:
                return None
            self._account(original)
            return Context(_CachedClient(self, original, factory), original.fingerprint,
                           original.login_at, original.broker_hash)
        return current

    def _account(self, context):
        if context.broker_hash != self.config['broker_hash']:
            raise ArchiveIntegrityError('parent archive broker account mismatch')

    def _authorized(self, factory, fingerprint):
        current = factory()
        if current is None:
            raise KiteError('research session changed', 'TokenException')
        self._account(current)
        if current.fingerprint != fingerprint:
            raise KiteError('research session changed', 'TokenException')
        return current

    def _cached(self, symbol, start, end):
        if self._config() != self.config:
            raise ArchiveIntegrityError('parent archive source identity changed')
        try:
            with read_database(self.root/'manifest.sqlite') as connection:
                connection.row_factory = sqlite3.Row
                tasks = [dict(row) for row in connection.execute(
                    "SELECT * FROM tasks WHERE symbol=? AND interval='1d' "
                    'AND start<=? AND end>=? ORDER BY start', (symbol, str(end), str(start)))]
            segments, rows = [], []
            for task in tasks:
                lo, hi = date.fromisoformat(task['start']), date.fromisoformat(task['end'])
                key = hashlib.sha256(f'{symbol}|1d|{lo}|{hi}'.encode()).hexdigest()
                if (task['key'] != key or not self.start <= lo <= hi <= self.end
                        or (task['sha'] and not re.fullmatch('[0-9a-f]{64}', task['sha']))):
                    raise ArchiveIntegrityError('parent archive task integrity failure')
                path = self.root/'chunks'/(key+'.json.gz')
                if task['status'] != 'complete':
                    if task['sha'] or path.exists():
                        raise ArchiveIntegrityError('parent archive saved checkpoint requires review')
                    continue
                if not task['sha']:
                    raise ArchiveIntegrityError('parent archive completed checkpoint has no digest')
                payload = path.read_bytes()
                if len(payload) != task['bytes'] or hashlib.sha256(payload).hexdigest() != task['sha']:
                    raise ArchiveIntegrityError('parent archive chunk integrity failure')
                doc = json.loads(gzip.decompress(payload))
                identity = dict(schema=1, owner_hash=hashlib.sha256(self.config['owner'].encode()).hexdigest(),
                                symbol=symbol, interval='1d', start=str(lo), end=str(hi))
                if any(doc.get(k) != v for k, v in identity.items()) or doc.get('provider', 'kite') != 'kite':
                    raise ArchiveIntegrityError('parent archive metadata integrity failure')
                valid = validate_rows(doc['rows'], '1d', lo, hi)
                if valid != doc['rows'] or len(valid) != task['rows']:
                    raise ArchiveIntegrityError('parent archive candle integrity failure')
                selected = [r for r in doc['rows'] if start <= date.fromisoformat(r[0][:10]) <= end]
                rows.extend(selected)
                segments.append(dict(start=str(max(start, lo)), end=str(min(end, hi)),
                                     chunk_key=key, sha256=task['sha'], rows=len(selected),
                                     source_version=self.config['engine_version'], provider='kite'))
            # Check conflicts between saved chunks before any broker request.
            validate_rows(rows, '1d', start, end)
            return rows, segments
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, zlib.error, EOFError) as exc:
            if isinstance(exc, ArchiveIntegrityError):
                raise
            raise ArchiveIntegrityError('parent archive integrity failure') from exc

    def historical(self, original, factory, token, interval, start, end):
        self.last_provenance = {}
        if interval != 'day' or token not in self.tokens:
            raise ArchiveIntegrityError('parent cache requires a known daily Kite instrument')
        if start.tzinfo is None or end.tzinfo is None:
            raise ArchiveError('daily cache request timezone is missing')
        lo, hi = start.astimezone(IST).date(), end.astimezone(IST).date()
        if lo > hi or (hi-lo).days >= 2000:
            raise ArchiveError('daily cache request exceeds the broker date bound')
        symbol = self.tokens[token]
        rows, cached = self._cached(symbol, lo, hi)
        uncovered, cursor = [], lo
        for segment in cached:
            a, b = date.fromisoformat(segment['start']), date.fromisoformat(segment['end'])
            if a > cursor:
                uncovered.append((cursor, a-timedelta(days=1)))
            cursor = max(cursor, b+timedelta(days=1))
        if cursor <= hi:
            uncovered.append((cursor, hi))
        network = []
        for a, b in uncovered:
            self.sleep(max(0., self.next_network_at-self.clock()))
            current = self._authorized(factory, original.fingerprint)
            self.next_network_at = self.clock()+2.
            fetched = current.client.historical(token, interval,
                datetime.combine(a, datetime.min.time(), IST),
                datetime.combine(b, datetime.max.time().replace(microsecond=0), IST))
            self._authorized(factory, original.fingerprint)
            rows.extend(validate_rows(fetched, '1d', a, b))
            network.append(dict(start=str(a), end=str(b), rows=len(fetched), provider='kite'))
        result = validate_rows(rows, '1d', lo, hi)
        self.last_provenance = dict(provider='kite', symbol=symbol,
                                    cached_segments=cached, network_segments=network)
        return result


class _CachedClient:
    def __init__(self, cache, original, factory):
        self.cache, self.original, self.factory = cache, original, factory

    def historical(self, token, interval, start, end):
        return self.cache.historical(self.original, self.factory, token, interval, start, end)

    @property
    def historical_provenance(self):
        return self.cache.last_provenance

    def __getattr__(self, name):
        # Other methods retain the original ReadOnlyKiteClient endpoint allowlist.
        return getattr(self.original.client, name)
