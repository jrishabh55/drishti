"""The bar seam: every desk module reads bars ONLY through these functions.

Today the sources are delayed: the scanner store's ingested 5m bars for
today's session (Yahoo, ~15–20 min delayed) and `bars.fetch_history` for
deep history. The Kite Auto-trader feature (separate spec) registers a
per-user *live provider*; when it answers for a user, its bars win and
``source`` says so. Desk code never changes for that.

A provider is ``fn(user_id, symbol, interval, kind) -> Optional[BarSeries]``
where ``kind`` is "session" (today's bars) or "history" (deep pool).
Returning None means "not live for this user/symbol — fall through".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, List, Literal, Optional

from apps.api.desk import bars as bars_mod
from apps.api.desk.bars import Bar, Row
from apps.api.desk.market.session import NSE

logger = logging.getLogger(__name__)

Kind = Literal["session", "history"]


@dataclass
class BarSeries:
    symbol: str
    interval: str
    rows: List[Row]
    source: str                 # "delayed:yahoo" | "live:<provider>"
    data_as_of: Optional[str]   # ISO time of the newest bar's start, if any
    notes: List[str] = field(default_factory=list)


LiveProvider = Callable[[str, str, str, Kind], Optional[BarSeries]]
_providers: List[LiveProvider] = []


def register_live_provider(fn: LiveProvider) -> None:
    if fn not in _providers:
        _providers.append(fn)


def clear_live_providers_for_tests() -> None:
    _providers.clear()


def _from_providers(user_id: str, symbol: str, interval: str, kind: Kind, *, now=None, expected_owner=None
                    ) -> Optional[BarSeries]:
    from apps.api.kite import live

    for fn in list(_providers):
        try:
            # Keep four-argument external providers valid; Kite supports the
            # explicit replay/test clock as an additive keyword argument.
            if fn is live.provider:
                got = fn(user_id, symbol, interval, kind, now=now, expected_owner=expected_owner)
            else:
                got = fn(user_id, symbol, interval, kind)
        except Exception:  # noqa: BLE001 — a broken live feed must degrade, not fail
            logger.exception("live bar provider failed; falling back to delayed bars")
            continue
        if got is not None:
            return got
    return None


def _as_of(rows: List[Row]) -> Optional[str]:
    return max(r[0] for r in rows).isoformat() if rows else None


def get_session_rows(user_id: str, symbol: str, interval: str = "5m", *, now: datetime | None = None) -> BarSeries:
    """Read cache first; recover a missing/stale watched symbol on demand."""
    sym = bars_mod.nse_symbol(symbol)
    live = _from_providers(user_id, sym, interval, "session")
    if live is not None:
        return live
    from apps.api.scanner.store import get_scanner_store
    from apps.api.scanner.ingest import intraday_stale

    now = now or datetime.now(timezone.utc)
    store = get_scanner_store()
    retain = 400 if interval == "1m" else 320
    df = store.load_symbol_bars(interval, sym, limit=retain)
    rows = bars_mod.rows_from_long(df) if not df.empty else []
    notes = []
    if not rows or intraday_stale(_as_of(rows), now):
        try:
            fetched = bars_mod.fetch_history(sym, interval, now=now)
            if fetched and (not rows or max(r[0] for r in fetched) > max(r[0] for r in rows)):
                rows = sorted(fetched, key=lambda row: row[0])[-retain:]
                import pandas as pd

                frame = pd.DataFrame([dict(symbol=sym, ts=r[0].astimezone(NSE.tz).isoformat(), open=r[1],
                                          high=r[2], low=r[3], close=r[4], volume=r[5]) for r in rows])
                store.upsert_bars(interval, frame)
                if interval == "1m":
                    # Desk-only rows: the ingest loop never prunes 1m, and no
                    # scanner reads it, so don't cold-rebuild their panels.
                    store.prune_bars("1m", keep=retain)
                else:
                    store.bump_bar_version()
        except bars_mod.BarsError as exc:
            notes.append(f"Could not refresh {sym}: {exc}")
    if not rows:
        notes.insert(0, f"{sym} has no {interval} bars available. The entry setup cannot be evaluated.")
    elif intraday_stale(_as_of(rows), now):
        notes.append(f"{sym} {interval} data is stale; newest cached bar is {_as_of(rows)}. The current session may be incomplete.")
    return BarSeries(sym, interval, rows, "delayed:yahoo", _as_of(rows), notes)


@dataclass
class UniverseSeries:
    rows: dict
    sources: dict
    coverage: dict

    @property
    def source(self) -> str:
        sources = set(self.sources.values())
        return next(iter(sources)) if len(sources) == 1 else "mixed" if sources else "delayed:yahoo"


def get_session_universe(user_id: str, interval: str = "5m", *, day: date | None = None,
                         symbols=None, now: datetime | None = None) -> UniverseSeries:
    """Source-aware, cache-only universe read; never performs per-symbol HTTP.

    Private providers overlay only today's requested symbols. Historical replay
    keeps the public path. Neither private rows nor their provenance are persisted.
    """
    from apps.api.scanner.store import get_scanner_store
    from apps.api.kite import live, sharing

    clock = live._now if now is None else lambda: now
    stamp = clock()
    day = day or stamp.astimezone(NSE.tz).date()
    store = get_scanner_store()
    selected = sorted({bars_mod.nse_symbol(s) for s in symbols}) if symbols is not None else list(store.instruments_df().index)
    df = store.load_session_bars(interval, day.isoformat(), selected)
    public = {sym: bars_mod.rows_from_long(group) for sym, group in df.groupby("symbol", sort=False)} if not df.empty else {}
    rows, sources = dict(public), {sym: "delayed:yahoo" for sym in public}
    owner = sharing.owner_for(user_id)
    if day == stamp.astimezone(NSE.tz).date():
        for sym in selected:
            series = _from_providers(user_id, sym, interval, "session", now=stamp, expected_owner=owner)
            if series is not None:
                rows[sym] = [r for r in series.rows if r[0].astimezone(NSE.tz).date() == day]
                sources[sym] = series.source
        if not live.access_valid(user_id, owner, clock()):
            rows, sources = dict(public), {sym: "delayed:yahoo" for sym in public}
    size = timedelta(minutes=1 if interval == "1m" else 5, seconds=2)
    rows = {s: [r for r in rr if r[0] + size <= stamp] for s, rr in rows.items()}
    rows = {s: rr for s, rr in rows.items() if rr}
    sources = {s: sources[s] for s in rows}
    nlive = sum(s.startswith("live:") for s in sources.values())
    return UniverseSeries(rows, sources, {"requested": len(selected), "live": nlive,
                                          "delayed": len(rows) - nlive, "missing": len(selected) - len(rows)})


def get_session_rows_universe(user_id: str, interval: str = "5m", *, day: date | None = None, symbols=None) -> dict:
    """Legacy dict contract; source-aware consumers use get_session_universe."""
    return get_session_universe(user_id, interval, day=day, symbols=symbols).rows


def get_history_rows(user_id: str, symbol: str, interval: str = "5m",
                     now: Optional[datetime] = None, *, yahoo: bool = False) -> BarSeries:
    """Deep history (59 days of 5m, or ~2 years of 1d) for the Time Machine.

    ``yahoo=True`` takes any Yahoo ticker as-is (swing replays of analysis
    runs, which may be US/BSE tickers); live providers are NSE-only.
    """
    sym = bars_mod.yahoo_ticker(symbol) if yahoo else bars_mod.nse_symbol(symbol)
    if not yahoo or sym.endswith(".NS"):
        live = _from_providers(user_id, bars_mod.nse_symbol(sym), interval, "history")
        if live is not None:
            return live
    rows = bars_mod.fetch_history(sym, interval, now=now, yahoo=yahoo)
    return BarSeries(sym, interval, rows, "delayed:yahoo", _as_of(rows))


def spec_for(interval: str):
    """The NSE session on the series' own bar grid (1m or 5m)."""
    from dataclasses import replace

    if interval not in ("1m", "5m"):
        raise ValueError(f"session bars are 1m or 5m, not {interval!r}")
    return replace(NSE, bar_min=1) if interval == "1m" else NSE


def session_bars_for(series: BarSeries, as_of: Optional[datetime] = None
                     ) -> tuple[List[Bar], dict]:
    return bars_mod.session_bars(series.rows, spec_for(series.interval),
                                 as_of or datetime.now(timezone.utc))
