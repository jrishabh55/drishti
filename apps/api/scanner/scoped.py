"""Selected-owner scanner views; private candles never enter shared caches."""
from dataclasses import replace
from datetime import datetime, timedelta

import pandas as pd

from apps.api.desk.market.session import NSE
from apps.api.kite import live, sharing
from apps.api.scanner.engine import ScanEngine, get_engine, _AGG, _RESAMPLE

COLS = ['symbol', 'ts', 'open', 'high', 'low', 'close', 'volume']


class ScopedEngine(ScanEngine):
    def _resolve_panels(self, timeframes):
        # Pin the published public snapshot once. A connected/stopped hub must
        # never force a new full-universe SQL read and five pivots per request.
        active = self._store.hub is not None and self._store.hub.state == 'live'
        base = {('1d' if tf in _RESAMPLE else tf) for tf in timeframes} if active else timeframes
        cached = get_engine()._resolve_panels(base)
        panels = {}
        for tf, panel in cached.items():
            index = panel.close.index
            local = index.tz_localize(NSE.tz) if index.tz is None else index.tz_convert(NSE.tz)
            keep = self._store.completed_index(local, tf)
            frames = {fld: getattr(panel, fld).loc[keep]
                      for fld in ('open', 'high', 'low', 'close', 'volume')}
            sources = dict.fromkeys(frames['close'].columns[frames['close'].notna().any()], 'delayed:yahoo')
            private = self._store.private_rows(tf, frames['close']) if active else {}
            if private:
                long = pd.DataFrame([(sym, self._store.local(r[0]), *r[1:])
                                     for sym, rows in private.items() for r in rows], columns=COLS)
                for fld, public in frames.items():
                    # Work only on disposable views. Private columns replace
                    # their public series; they never enter the shared cache.
                    public = public.drop(columns=list(private), errors='ignore').copy()
                    public.index = public.index.tz_localize(NSE.tz) if public.index.tz is None else public.index.tz_convert(NSE.tz)
                    overlay = long.pivot(index='ts', columns='symbol', values=fld)
                    frames[fld] = public.join(overlay, how='outer').sort_index()
                sources.update(dict.fromkeys(private, 'live:kite'))
            panels[tf] = replace(panel, present=None, **frames)
            if tf not in _RESAMPLE:
                self._store.sources[tf] = sources
                close = frames['close']
                self._store.panel_times[tf] = close.index.max().isoformat() if len(close) else None
        if active:
            # Aggregate only after replacing daily columns and dropping forming
            # candles, so weekly/monthly conditions cannot see unfinished data.
            for tf in timeframes & _RESAMPLE.keys():
                daily = panels['1d']
                frames = {}
                for fld in ('open', 'high', 'low', 'close', 'volume'):
                    frame = getattr(daily, fld).resample(_RESAMPLE[tf]).agg(_AGG[fld])
                    frames[fld] = frame.loc[self._store.completed_index(frame.index, tf)]
                panels[tf] = replace(daily, present=None, **frames)
        return {tf: panels[tf] for tf in timeframes}


class ScopedStore:
    def __init__(self, store, user_id: str, now: datetime):
        self.public = store
        self.user_id, self.now = user_id, now
        self.owner = sharing.owner_for(user_id)
        self.hub = live.get_hub(self.owner) if live.access_valid(user_id, self.owner, now) else None
        self.sources = {}
        self.panel_times = {}
        self._instruments = None

    def instruments_df(self):
        if self._instruments is None:
            self._instruments = self.public.instruments_df()
        return self._instruments

    def latest_ts(self, timeframe):
        return self.panel_times.get(timeframe)

    def private_rows(self, timeframe, public_close, limit=320):
        """Read the already authorized, pinned hub; reauthorize before return.

        Avoid per-symbol owner/session DB lookups: run_for_user checks access
        before constructing this snapshot and again after all evaluation and
        annotation. A revoked or changed source discards the whole result.
        """
        private = {}
        if self.hub is None or self.hub.state != 'live':
            return private
        for sym in self.hub.symbols:
            last = self.hub.builder.last_tick.get(sym)
            if (self.hub.state != 'live' or last is None or self.now - last > live.STALE_AFTER
                    or sym not in self.hub.backfilled):
                continue
            if timeframe == '1d':
                rows = self.hub.history.get('1d', {}).get(sym, [])
            else:
                five = {r[0]: r for r in self.hub.history.get('5m', {}).get(sym, [])}
                five.update({r[0]: r for r in self.hub.builder.rows(sym, '5m')})
                rows = self.resample(list(five.values()), timeframe)
            rows = [r for r in rows if self.complete(r[0], timeframe)][-limit:]
            # Fresh ticks cannot make an older private daily history newer
            # than the public closed session. Provider time labels may differ.
            if rows and timeframe == '1d' and sym in public_close:
                latest = public_close[sym].last_valid_index()
                if latest is not None and max(self.local(r[0]).date() for r in rows) < latest.date():
                    continue
            if rows:
                private[sym] = rows
        return private

    @staticmethod
    def local(ts):
        return ts.replace(tzinfo=NSE.tz) if ts.tzinfo is None else ts.astimezone(NSE.tz)

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
