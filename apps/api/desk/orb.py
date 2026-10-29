# Ported from day-trader-skill (https://github.com/Jakeschincariol/day-trader-skill,
# commit 3d0f4ef), MIT License, Copyright (c) 2026 Jake Schincariol.
# Adapted for NSE (09:15 IST open, F&O/NIFTY500 universes, ₹ liquidity) by QuberTech.
"""Opening-range breakout scan over the scan universe.

The opening range is the high and low of the first ``or_minutes`` of the
session (15 = the 09:15, 09:20 and 09:25 bars). A *hit* is the first
COMPLETED bar after the range that CLOSES above the range high — a wick
above it is not a breakout. A symbol is scanned only when every bar from
09:15 to the cutoff is present (a gap would hide or invent a breakout).

Input bars come from the bar seam (scanner store: Yahoo, ~15–20 min
delayed), so ``as_of`` is honest about what "so far" means.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from apps.api.desk.bars import Bar, Row, session_bars
from apps.api.desk.market.session import NSE, SessionSpec, clock12, clock24, parse_clock


class ORBError(ValueError):
    """A plain-language reason the scan can't run right now."""


def orb_window(now: datetime, or_minutes: int, no_entries_after: Optional[str] = None,
               at: Optional[str] = None, day: Optional[date] = None,
               spec: SessionSpec = NSE) -> tuple[date, datetime]:
    """-> (session day, cutoff as an aware datetime). Port of upstream orb_window."""
    if or_minutes < spec.bar_min or or_minutes % spec.bar_min or or_minutes > 120:
        raise ORBError("or_minutes must be a multiple of 5 from 5 to 120 (15 is the classic)")
    local_now = now.astimezone(spec.tz)
    d = day or local_now.date()
    if not spec.is_trading_day(d):
        raise ORBError(f"No NSE session on {d.isoformat()} ({d.strftime('%A')}): the market is "
                       "closed. Pick a trading day to replay.")
    range_end_min = spec.open_min + or_minutes
    first_close_min = range_end_min + spec.bar_min
    close_dt = spec.local(d, spec.close_min)
    if at is not None:
        cutoff = spec.local(d, parse_clock(at))
        if day is None and cutoff > local_now + timedelta(seconds=59):
            raise ORBError(f"at {at} is later than now ({clock12(local_now.hour * 60 + local_now.minute)}).")
        if cutoff > close_dt:
            raise ORBError(f"at {at} is after the 3:30 PM close.")
    else:
        if day is not None and d < local_now.date():
            cutoff = close_dt
        else:
            cutoff = local_now.replace(second=0, microsecond=0)
            if cutoff >= close_dt:
                cutoff = close_dt   # after the close: the whole session, marked as such
    if cutoff < spec.local(d, first_close_min):
        raise ORBError(f"It is {clock12(cutoff.hour * 60 + cutoff.minute)}: the {or_minutes}-minute "
                       f"opening range runs 9:15 to {clock12(range_end_min)} and the first bar after "
                       f"it closes at {clock12(first_close_min)}. Check again after that.")
    return d, cutoff


def usable(bars: Sequence[Bar], cutoff: datetime, n_range: int, spec: SessionSpec = NSE) -> bool:
    """Every slot from the open to the cutoff is present, and at least the range."""
    d = date.fromisoformat(bars[0]["date"]) if bars else None
    if d is None:
        return False
    open_dt = spec.local(d, spec.open_min)
    want = int((cutoff - open_dt).total_seconds() // (spec.bar_min * 60))
    want = min(want, spec.n_bars)
    if want < n_range or len(bars) != want:
        return False
    return all(b["slot"] == k for k, b in enumerate(bars))


def orb_hit(bars: Sequence[Bar], n_range: int, spec: SessionSpec = NSE,
            last_entry_min: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """The first completed bar to CLOSE above the range high (upstream stage 2)."""
    rng = bars[:n_range]
    oh = max(b["h"] for b in rng)
    ol = min(b["l"] for b in rng)
    avg_vol = sum(b["v"] for b in rng) / len(rng) if rng else 0
    for k in range(n_range, len(bars)):
        b = bars[k]
        close_min = spec.bar_close_min(b["slot"])
        if last_entry_min is not None and close_min > last_entry_min:
            break
        if b["c"] > oh:
            return {"price": round(b["c"], 2), "at": clock24(close_min),
                    "bar_start": b["start"], "bar_index": b["slot"],
                    "or_high": round(oh, 2), "or_low": round(ol, 2),
                    "pct_over": round((b["c"] / oh - 1) * 100, 2),
                    "volume_x": round(b["v"] / avg_vol, 2) if avg_vol else None}
    return None


def _avg_value_cr(daily_long, symbols: Iterable[str]) -> Dict[str, float]:
    """20-session average traded value (close × volume) in ₹ crore, per symbol."""
    out: Dict[str, float] = {}
    if daily_long is None or daily_long.empty:
        return out
    df = daily_long[daily_long["symbol"].isin(set(symbols))]
    if df.empty:
        return out
    df = df.assign(value=df["close"] * df["volume"])
    for sym, g in df.groupby("symbol"):
        tail = g.sort_values("ts").tail(20)
        out[sym] = round(float(tail["value"].mean()) / 1e7, 2)
    return out


def pick_universe(instruments, universe: str) -> tuple[List[str], List[str]]:
    """(symbols, notes) for 'fno' | 'nifty500' | 'all'."""
    notes: List[str] = []
    if instruments is None or instruments.empty:
        return [], ["the scan universe is empty: the scanner hasn't loaded instruments yet"]
    if universe == "fno":
        fno = instruments.index[instruments["fno"]].tolist() if "fno" in instruments else []
        if fno:
            return sorted(fno), notes
        notes.append("The F&O stock list isn't loaded yet, so this scan used NIFTY500 with the "
                     "liquidity filter instead.")
        universe = "nifty500"
    if universe == "nifty500":
        mask = instruments["index_memberships"].map(lambda xs: "NIFTY500" in (xs or []))
        syms = instruments.index[mask].tolist()
        if syms:
            return sorted(syms), notes
        notes.append("NIFTY500 memberships aren't loaded yet, so this scan used every symbol.")
    return sorted(instruments.index.tolist()), notes


def scan(rows_by_symbol: Dict[str, List[Row]], now: datetime, *, or_minutes: int = 15,
         no_entries_after: Optional[str] = "14:45", at: Optional[str] = None,
         day: Optional[date] = None, symbols: Optional[Sequence[str]] = None,
         min_price: float = 0.0, min_avg_value_cr: float = 0.0,
         avg_value_cr: Optional[Dict[str, float]] = None, names: Optional[Dict[str, str]] = None,
         spec: SessionSpec = NSE, source: str = "delayed:yahoo") -> Dict[str, Any]:
    """The scan document: hits sorted by trigger time, plus counts and notes."""
    d, cutoff = orb_window(now, or_minutes, no_entries_after, at, day, spec)
    n_range = or_minutes // spec.bar_min
    last_entry_min = parse_clock(no_entries_after) if no_entries_after else None
    syms = list(symbols) if symbols is not None else sorted(rows_by_symbol)
    counts = {"universe": len(syms), "with_data": 0, "unusable": 0, "illiquid": 0,
              "scanned": 0, "hits": 0}
    hits: List[Dict[str, Any]] = []
    as_of = cutoff.astimezone(timezone.utc)
    for sym in syms:
        rows = rows_by_symbol.get(sym)
        if not rows:
            continue
        counts["with_data"] += 1
        bars, _ = session_bars(rows, spec, as_of)
        bars = [b for b in bars if b["date"] == d.isoformat()]
        if not usable(bars, cutoff, n_range, spec):
            counts["unusable"] += 1
            continue
        value = (avg_value_cr or {}).get(sym)
        if bars[-1]["c"] < min_price or (min_avg_value_cr and (value or 0) < min_avg_value_cr):
            counts["illiquid"] += 1
            continue
        counts["scanned"] += 1
        h = orb_hit(bars, n_range, spec, last_entry_min)
        if h:
            last = bars[-1]["c"]
            hits.append(dict(h, symbol=sym, name=(names or {}).get(sym, ""),
                             last=round(last, 2),
                             last_vs_or_high_pct=round((last / h["or_high"] - 1) * 100, 2),
                             avg_value_cr=value))
    hits.sort(key=lambda x: (x["at"], x["symbol"]))
    counts["hits"] = len(hits)
    after_close = cutoff >= spec.local(d, spec.close_min)
    late = last_entry_min is not None and \
        cutoff.hour * 60 + cutoff.minute > last_entry_min
    notes = []
    if late and not after_close:
        notes.append(f"It's after {no_entries_after}, your last time for new entries: these "
                     "breakouts are for review, not for entering now.")
    if after_close:
        notes.append("The session is over: this is a replay of today's breakouts.")
    return {"date": d.isoformat(), "as_of": cutoff.isoformat(), "or_minutes": or_minutes,
            "range": f"09:15–{clock24(spec.open_min + or_minutes)}", "source": source,
            "counts": counts, "hits": hits, "notes": notes,
            "note": "Bars are delayed and checked bar by bar, never tick by tick. A breakout is a "
                    "setup, not a recommendation."}
