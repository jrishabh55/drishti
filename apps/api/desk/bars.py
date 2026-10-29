"""Bar conversions and on-demand deep history.

Two shapes flow through the desk:

* **session bars** (intraday): ``{"date", "slot", "start", "o", "h", "l", "c", "v"}``
  — one dict per *completed* regular-session bar, slot = bar index in the
  session (see market/session.py). Same shape as upstream's parse_bars so
  the ported algorithms stay line-for-line comparable.
* **daily bars**: ``{"date", "o", "h", "l", "c", "v"}``.

The scanner store keeps only 320 bars per timeframe (≈4 NSE sessions of 5m),
enough for today's ORB but not for a Time Machine pool. `fetch_history`
pulls 59 days of 5m (Yahoo's intraday window is 60) or ~2 years of 1d for
one symbol and caches it in desk.sqlite (shared — it's public data).
Uses an explicit clamped ``start`` exactly like scanner/ingest.py (see the
"aged young listings" fix): ``period=`` breaks for recently listed tickers.
"""
from __future__ import annotations

import logging
import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from apps.api.desk.market.session import NSE, SessionSpec

logger = logging.getLogger(__name__)

Bar = Dict[str, Any]
Row = Tuple[datetime, float, float, float, float, float]

_SYMBOL_RE = re.compile(r"^[A-Z0-9&\-]{1,20}$")


class BarsError(RuntimeError):
    """Plain-language data problem (missing symbol, no bars, too few sessions)."""


# ---------------------------------------------------------------- symbols

def nse_symbol(text: str) -> str:
    """'reliance', 'RELIANCE.NS', 'NSE:RELIANCE' -> 'RELIANCE'. Raises BarsError."""
    s = str(text or "").strip().upper()
    if s.startswith("NSE:"):
        s = s[4:]
    if s.endswith(".NS"):
        s = s[:-3]
    if not _SYMBOL_RE.match(s):
        raise BarsError(f"{text!r} is not an NSE symbol")
    return s


def yf_symbol(text: str) -> str:
    return nse_symbol(text) + ".NS"


# ---------------------------------------------------------------- conversions

def session_bars(rows: Iterable[Row], spec: SessionSpec = NSE,
                 as_of: Optional[datetime] = None) -> Tuple[List[Bar], Dict[str, int]]:
    """Raw (ts, o, h, l, c, v) rows -> completed regular-session bars, oldest first.

    Port of upstream ``parse_bars``: drops rows with a missing price, rows off
    the bar grid (a live tick like 10:49:45), bars outside the session, and
    bars not complete at ``as_of`` (start + bar length > as_of). A forming bar
    is never used.
    """
    dropped = {"no_price": 0, "off_grid": 0, "not_complete": 0, "outside_hours": 0}
    by_ts: Dict[datetime, Bar] = {}
    bar_len = timedelta(minutes=spec.bar_min)
    for ts, o, h, l, c, v in rows:
        if ts is None or any(x is None or (isinstance(x, float) and math.isnan(x))
                             for x in (o, h, l, c)):
            dropped["no_price"] += 1
            continue
        local = spec.to_local(ts)
        mins = local.hour * 60 + local.minute - spec.open_min
        if local.second or local.microsecond or mins % spec.bar_min:
            dropped["off_grid"] += 1
            continue
        if mins < 0 or mins >= spec.n_bars * spec.bar_min:
            dropped["outside_hours"] += 1
            continue
        if as_of is not None and ts + bar_len > as_of:
            dropped["not_complete"] += 1
            continue
        vol = 0 if v is None or (isinstance(v, float) and math.isnan(v)) else v
        by_ts[ts] = {"date": local.date().isoformat(), "slot": mins // spec.bar_min,
                     "start": local.strftime("%H:%M"),
                     "o": float(o), "h": float(h), "l": float(l), "c": float(c),
                     "v": float(vol)}
    return [by_ts[t] for t in sorted(by_ts)], dropped


def split_sessions(bars: Sequence[Bar]) -> Dict[str, Dict[int, Bar]]:
    days: Dict[str, Dict[int, Bar]] = {}
    for b in bars:
        days.setdefault(b["date"], {})[b["slot"]] = b
    return days


def full_sessions(days: Dict[str, Dict[int, Bar]], before: str, spec: SessionSpec = NSE
                  ) -> Tuple[List[Tuple[str, List[Bar]]], List[Dict[str, Any]]]:
    """Full sessions (every bar present) strictly before ``before``, oldest
    first, plus the short ones left out (special sessions, gaps)."""
    full, short = [], []
    n = spec.n_bars
    for d in sorted(days):
        if d >= before:
            continue
        s = days[d]
        if len(s) == n and all(i in s for i in range(n)):
            full.append((d, [s[i] for i in range(n)]))
        else:
            short.append({"date": d, "bars": len(s)})
    return full, short


def rows_from_yahoo_chart(result: Dict[str, Any]) -> List[Row]:
    """A Yahoo v8 chart ``result`` object -> rows (used by fixtures/tests)."""
    stamps = result.get("timestamp") or []
    q = ((result.get("indicators") or {}).get("quote") or [{}])[0] or {}
    cols = [q.get(k) or [] for k in ("open", "high", "low", "close", "volume")]
    rows: List[Row] = []
    for i, ts in enumerate(stamps):
        vals = [col[i] if i < len(col) else None for col in cols]
        t = datetime.fromtimestamp(int(ts), timezone.utc) if ts is not None else None
        rows.append((t, *vals))  # type: ignore[arg-type]
    return rows


def rows_from_long(df) -> List[Row]:
    """Scanner-store long frame (symbol, ts ISO, open..volume) -> rows."""
    out: List[Row] = []
    for r in df.itertuples(index=False):
        ts = datetime.fromisoformat(r.ts)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        out.append((ts, r.open, r.high, r.low, r.close, r.volume))
    return out


def rows_from_yf(df) -> List[Row]:
    """A single-ticker yfinance download frame -> rows."""
    out: List[Row] = []
    if df is None or df.empty:
        return out
    if getattr(df.columns, "nlevels", 1) > 1:
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    for ts, r in df.iterrows():
        t = ts.to_pydatetime()
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        out.append((t, r.get("Open"), r.get("High"), r.get("Low"), r.get("Close"),
                    r.get("Volume")))
    return out


def daily_bars(rows: Iterable[Row], spec: SessionSpec = NSE,
               before: Optional[str] = None) -> List[Bar]:
    """Daily rows -> daily bars keyed by the exchange-local date, oldest first."""
    out: Dict[str, Bar] = {}
    for ts, o, h, l, c, v in rows:
        if ts is None or any(x is None or (isinstance(x, float) and math.isnan(x))
                             for x in (o, h, l, c)):
            continue
        d = spec.to_local(ts).date().isoformat() if ts.tzinfo else ts.date().isoformat()
        if before and d >= before:
            continue
        out[d] = {"date": d, "o": float(o), "h": float(h), "l": float(l), "c": float(c),
                  "v": float(v or 0)}
    return [out[d] for d in sorted(out)]


# ---------------------------------------------------------------- deep history

#: interval -> (yfinance interval, lookback days). One day inside Yahoo's
#: windows (60d for 5m, 730d for 1h/1d practical) so clock skew can't trip them.
HISTORY_FETCH = {"1m": ("1m", 6), "5m": ("5m", 59), "1d": ("1d", 729)}
#: Yahoo serves only ~7 days of 1m bars, too few sessions for a replay pool, so
#: each 1m fetch is merged into the cached history (real bars only, never
#: filled in) and the merged history is kept for this many calendar days.
ACCUMULATE_DAYS = {"1m": 60}
#: Cache TTL: intraday history is refreshed at most every 10 minutes during
#: the session; outside it, the last fetch is good until the next open.
INTRADAY_TTL_S = 600


def _download(yf_sym: str, interval: str, start: datetime):  # pragma: no cover - network
    import yfinance as yf

    return yf.download(tickers=yf_sym, start=start, interval=interval,
                       auto_adjust=False, progress=False, threads=False)


def _serialize(rows: List[Row]) -> List[list]:
    return [[r[0].timestamp(), *[None if x is None else float(x) for x in r[1:]]]
            for r in rows if r[0] is not None]


def _deserialize(raw: List[list]) -> List[Row]:
    return [(datetime.fromtimestamp(r[0], timezone.utc), *r[1:]) for r in raw]  # type: ignore[misc]


def _fresh(fetched_at: str, interval: str, now: datetime) -> bool:
    from apps.api.scanner.calendar import is_market_open, is_trading_day

    age = (now - datetime.fromisoformat(fetched_at)).total_seconds()
    if interval == "1d":
        # Daily bars change once per session: refetch after a new close.
        return age < 6 * 3600 and not is_market_open(now)
    if is_market_open(now):
        return age < INTRADAY_TTL_S
    # Yahoo can still publish the final bars after the exchange closes.
    # Keep partial close snapshots on the short TTL until that delay passes.
    fetched = datetime.fromisoformat(fetched_at)
    local = now.astimezone(NSE.tz)
    final_ready = NSE.local(local.date(), NSE.close_min) + timedelta(minutes=30)
    if is_trading_day(local.date()) and fetched < final_ready:
        return age < INTRADAY_TTL_S
    return not is_market_open(fetched) and age < 18 * 3600


_YAHOO_RE = re.compile(r"^[A-Z0-9.\-&^=]{1,24}$")


def yahoo_ticker(text: str) -> str:
    """Any Yahoo ticker as given ('RELIANCE.NS', 'AAPL', '^NSEI'). Raises BarsError."""
    s = str(text or "").strip().upper()
    if not _YAHOO_RE.match(s):
        raise BarsError(f"{text!r} is not a ticker")
    return s


def fetch_history(symbol: str, interval: str = "5m", *, now: Optional[datetime] = None,
                  store=None, yahoo: bool = False) -> List[Row]:
    """Deep history, cached. ``symbol`` is an NSE symbol, or — with
    ``yahoo=True`` — any Yahoo ticker as-is (swing mode for analysis runs,
    whose tickers may be US or BSE). Raises BarsError."""
    if interval not in HISTORY_FETCH:
        raise BarsError(f"interval must be one of {sorted(HISTORY_FETCH)}")
    from apps.api.desk.store import get_desk_store

    store = store or get_desk_store()
    sym = yahoo_ticker(symbol) if yahoo else nse_symbol(symbol)
    yf_sym = sym if yahoo else sym + ".NS"
    now = now or datetime.now(timezone.utc)
    cached = store.get_history(yf_sym, interval)
    if cached and _fresh(cached["fetched_at"], interval, now):
        return _deserialize(cached["bars"])
    yf_int, days = HISTORY_FETCH[interval]
    try:
        df = _download(yf_sym, yf_int, now - timedelta(days=days))
    except Exception as exc:  # noqa: BLE001 — vendor errors become a plain message
        if cached:
            logger.warning("history refresh for %s failed, serving cache: %s", sym, exc)
            return _deserialize(cached["bars"])
        raise BarsError(f"could not download {interval} bars for {sym}: {exc}") from exc
    rows = rows_from_yf(df)
    if not rows:
        if cached:
            return _deserialize(cached["bars"])
        raise BarsError(f"Yahoo returned no {interval} bars for {yf_sym} — check the symbol")
    if interval in ACCUMULATE_DAYS and cached:
        rows = _merge_history(_deserialize(cached["bars"]), rows,
                              now - timedelta(days=ACCUMULATE_DAYS[interval]))
    store.put_history(yf_sym, interval, _serialize(rows), fetched_at=now.isoformat())
    return rows


def _merge_history(old: List[Row], new: List[Row], since: datetime) -> List[Row]:
    """Union of two fetches by bar timestamp (the newer fetch wins), from ``since``."""
    by_ts = {r[0]: r for r in old if r[0] is not None and r[0] >= since}
    by_ts.update((r[0], r) for r in new if r[0] is not None)
    return [by_ts[t] for t in sorted(by_ts)]


def last_close_before(bars: Sequence[Bar], day: str) -> Optional[float]:
    prev = [b for b in bars if b["date"] < day]
    return prev[-1]["c"] if prev else None


def today_ist(now: Optional[datetime] = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(NSE.tz).date()
