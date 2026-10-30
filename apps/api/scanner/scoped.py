"""Disposable private scanner snapshots. No writes to ScannerStore or engine caches."""
from dataclasses import replace
from datetime import datetime, timedelta

import pandas as pd

from apps.api.desk.market.session import NSE
from apps.api.kite import live, sharing
from apps.api.scanner.engine import ScanEngine, get_engine

COLS = ['symbol', 'ts', 'open', 'high', 'low', 'close', 'volume']


class ScopedEngine(ScanEngine):
    def _resolve_panels(self, timeframes):
        if self._store.hub is not None:
            return super()._resolve_panels(timeframes)
        # Public users share the versioned snapshot published/warmed by ingest.
        # Filter its small time index, never rebuild/pivot the long bar table or
        # mutate the cached Panel (legacy run() must keep its original view).
        cached = get_engine()._resolve_panels(timeframes)
        panels = {}
        for tf, panel in cached.items():
            index = panel.close.index
            local = index.tz_localize(NSE.tz) if index.tz is None else index.tz_convert(NSE.tz)
            keep = self._store.completed_index(local, tf)
            frames = {fld: getattr(panel, fld).loc[keep]
                      for fld in ('open', 'high', 'low', 'close', 'volume')}
            panels[tf] = replace(panel, **frames)
            if tf not in ('1w', '1mo'):
                close = frames['close']
                self._store.sources[tf] = dict.fromkeys(close.columns[close.notna().any()], 'delayed:yahoo')
                self._store.panel_times[tf] = close.index.max().isoformat() if len(close) else None
        return panels

    def _build_panel(self, timeframe):
        panel = super()._build_panel(timeframe)
        if timeframe in ('1w', '1mo'):
            # W/ME labels are the period's last date. Require it to pass.
            for fld in ('open', 'high', 'low', 'close', 'volume'):
                frame = getattr(panel, fld)
                frame = frame.loc[self._store.completed_index(frame.index, timeframe)]
                setattr(panel, fld, frame)
        return panel


class ScopedStore:
    def __init__(self, store, user_id: str, now: datetime):
        self.public = store
        self.user_id, self.now = user_id, now
        self.owner = sharing.owner_for(user_id)
        self.hub = live.get_hub(self.owner) if live.access_valid(user_id, self.owner, now) else None
        self.sources = {}
        self.frames = {}
        self.panel_times = {}

    def instruments_df(self):
        return self.public.instruments_df()

    def version(self):
        return 0  # one disposable engine per immutable snapshot

    def latest_ts(self, timeframe):
        if timeframe in self.panel_times:
            return self.panel_times[timeframe]
        df = self.frames.get(timeframe)
        if df is None:
            return None
        return df.ts.max().isoformat() if len(df) else None

    def load_bars(self, timeframe, limit=320):
        if timeframe in self.frames:
            return self.frames[timeframe]
        public = self.public.load_bars(timeframe, limit)
        # Drop forming candles from both sources before any condition/indicator.
        if len(public):
            public = public.copy()
            public['ts'] = self.local_times(public.ts)
            public = public.loc[self.completed_index(pd.DatetimeIndex(public.ts), timeframe)]
        daily_latest = public.groupby('symbol').ts.max() if timeframe == '1d' else {}
        private = {}
        if self.hub:
            for sym in self.hub.symbols:
                if timeframe == '1d':
                    last = self.hub.builder.last_tick.get(sym)
                    if (self.hub.state != 'live' or last is None or self.now - last > live.STALE_AFTER
                            or sym not in self.hub.backfilled):
                        continue
                    rows = self.hub.history.get('1d', {}).get(sym, [])
                else:
                    series = live.provider(self.user_id, sym, '5m', 'session', now=self.now, expected_owner=self.owner)
                    if series is None:
                        continue
                    five = {r[0]: r for r in self.hub.history.get('5m', {}).get(sym, [])}
                    five.update({r[0]: r for r in series.rows})
                    rows = self.resample(list(five.values()), timeframe)
                rows = [r for r in rows if self.complete(r[0], timeframe)][-limit:]
                # A fresh tick cannot make older closed history more recent.
                # Daily providers may label the same session at different times.
                if (rows and timeframe == '1d' and sym in daily_latest
                        and max(self.local(r[0]).date() for r in rows) < daily_latest[sym].date()):
                    continue
                if rows:
                    private[sym] = rows
        self.sources[timeframe] = {s: 'live:kite' if s in private else 'delayed:yahoo'
                                   for s in set(public.symbol) | set(private)}
        frames = [public.loc[~public.symbol.isin(private)]]
        frames.append(pd.DataFrame([(s, self.local(r[0]), *r[1:]) for s, rows in private.items() for r in rows], columns=COLS))
        frame = pd.concat(frames, ignore_index=True)
        # Normalize mixed public naive / Kite aware timestamps for pivot ordering.
        if len(frame):
            frame['ts'] = pd.to_datetime(frame.ts)
        self.frames[timeframe] = frame
        return frame

    @staticmethod
    def local(ts):
        return ts.replace(tzinfo=NSE.tz) if ts.tzinfo is None else ts.astimezone(NSE.tz)

    @staticmethod
    def local_times(values):
        # Public timestamps are exchange-local when naive. Handle mixed ISO
        # formats/offsets in vectorized batches without a Python call per bar.
        aware = values.astype(str).str.contains(r'(?:Z|[+-]\d{2}:?\d{2})$', regex=True)
        parsed = pd.to_datetime(values, format='mixed', utc=True)
        parsed = parsed.where(aware, parsed - pd.Timedelta(hours=5, minutes=30))
        return parsed.dt.tz_convert(NSE.tz)

    def completed_index(self, local, timeframe):
        if timeframe in ('1w', '1mo'):
            end = local.normalize() + pd.Timedelta(days=1)
        elif timeframe == '1d':
            end = local.normalize() + pd.Timedelta(minutes=NSE.close_min, seconds=2)
        else:
            end = local + pd.Timedelta(minutes={'5m': 5, '15m': 15, '1h': 60}[timeframe], seconds=2)
        return end <= self.now

    def complete(self, ts, timeframe):
        ts = self.local(ts)
        if timeframe == '1d':
            end = datetime.combine(ts.date(), datetime.min.time(), NSE.tz) + timedelta(minutes=NSE.close_min, seconds=2)
        else:
            end = ts + timedelta(minutes={'5m': 5, '15m': 15, '1h': 60}[timeframe], seconds=2)
        return end <= self.now

    def resample(self, rows, timeframe):
        if timeframe == '5m':
            return sorted(rows)
        size = {'15m': 15, '1h': 60}[timeframe]
        grouped = {}
        for ts, o, h, lo, c, v in sorted(rows):
            ts = self.local(ts)
            minute = ts.hour * 60 + ts.minute
            start = ts.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=NSE.open_min + ((minute - NSE.open_min) // size) * size)
            b = grouped.setdefault(start, [o, h, lo, c, 0., set()])
            b[1], b[2], b[3], b[4] = max(b[1], h), min(b[2], lo), c, b[4] + v
            b[5].add(minute)
        # Do not manufacture complete higher-timeframe candles from gaps.
        return [(ts, *b[:5]) for ts, b in grouped.items() if len(b[5]) == size // 5]


def annotate(result, snapshot, timeframes):
    symbols = list(snapshot.instruments_df().index)
    sources = {}
    for sym in symbols:
        used = {snapshot.sources.get('1d' if tf in ('1w', '1mo') else tf, {}).get(sym)
                for tf in timeframes}
        sources[sym] = 'live:kite' if used == {'live:kite'} else 'mixed' if 'live:kite' in used else 'delayed:yahoo'
    values = set(sources.values())
    result['source'] = next(iter(values)) if len(values) == 1 else 'mixed' if values else 'delayed:yahoo'
    result['coverage'] = {'requested': len(symbols), 'live': sum(s == 'live:kite' for s in sources.values()),
                          'mixed': sum(s == 'mixed' for s in sources.values()),
                          'delayed': sum(s == 'delayed:yahoo' for s in sources.values()),
                          'by_timeframe': {tf: {'kite': sum(s == 'live:kite' for s in src.values()),
                                                'delayed': sum(s == 'delayed:yahoo' for s in src.values()),
                                                'missing': len(set(symbols) - set(src))}
                                           for tf, src in snapshot.sources.items()}}
    result['notes'] = ['Conditions use completed candles. Price/volume columns use closed daily candles; fundamentals and liquidity metadata use the public snapshot.']
    for match in result['matches']:
        sym = match['symbol']
        match.update(source=sources.get(sym, 'delayed:yahoo'), price_basis='closed_daily',
                     sources={tf: snapshot.sources.get('1d' if tf in ('1w', '1mo') else tf, {}).get(sym, 'no_data') for tf in timeframes})
        if snapshot.hub:
            last = snapshot.hub.builder.last_tick.get(sym)
            if last and snapshot.hub.state == 'live' and snapshot.now - last <= live.STALE_AFTER:
                rows = snapshot.hub.builder.rows(sym, '1m')
                if rows:
                    match.update(quote=rows[-1][4], quote_as_of=last.isoformat())
    return result
