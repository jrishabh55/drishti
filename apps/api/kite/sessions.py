"""Dated end-of-day timing for live MIS orders: NSE's closing auction session.

From 2026-08-03 NSE runs a closing auction session (CAS) for stocks with
F&O contracts: their continuous session ends at 15:15, and Zerodha squares
off MIS positions in them at 15:12
(https://zerodha.com/z-connect/updates/track-closing-auction-session-on-kite-web).
Stop-loss orders aren't accepted in the auction. SEBI's 2026-10-15 circular
restores the 15:30 continuous close from 2026-12-07. Other stocks keep the
profile's broker square-off time.

The engine never sends an order into the auction or past the broker's
square-off, and it flattens ``MARGIN_MIN`` before the square-off. A user flat-by
later than that isn't changed: new live entries in those stocks are refused
with the time to set instead (the paper desk is unaffected).

CAS membership is the bundled NSE F&O underlyings list (scanner/data/nse_fo.csv,
from NSE's fo_mktlots.csv). Update CAS_WINDOWS when NSE/SEBI change the schedule.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from typing import List, Optional

from apps.api.desk.market.session import NSE, parse_clock

MARGIN_MIN = 2


@dataclass(frozen=True)
class CasWindow:
    start: date
    end: date               # inclusive
    continuous_end: str     # continuous trading ends; the auction follows
    broker_square_off: str  # Zerodha's MIS auto square-off for CAS stocks
    source: str


CAS_WINDOWS = (
    CasWindow(date(2026, 8, 3), date(2026, 12, 6), "15:15", "15:12",
              "NSE closing auction for F&O stocks; Zerodha MIS square-off 15:12; "
              "SEBI circular 2026-10-15 restores the 15:30 close from 2026-12-07"),
)


@lru_cache(maxsize=1)
def _fo() -> frozenset:
    from apps.api.scanner.universe import fo_symbols

    return frozenset(fo_symbols())


def cas_rule(symbol: str, day: date) -> Optional[CasWindow]:
    if symbol.upper() not in _fo():
        return None
    return next((w for w in CAS_WINDOWS if w.start <= day <= w.end), None)


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@dataclass(frozen=True)
class Cutoffs:
    flatten_by: int       # minutes: start the exit no later than this
    no_orders_after: int  # minutes: never place an order at or after this
    cas: Optional[CasWindow]


def cutoffs(symbol: str, day: date, flat_by: str, broker_square_off: str) -> Cutoffs:
    rule = cas_rule(symbol, day)
    broker = parse_clock(rule.broker_square_off if rule else broker_square_off)
    return Cutoffs(min(parse_clock(flat_by), broker - MARGIN_MIN), broker, rule)


def timing_reasons(symbol: str, now: datetime, flat_by: str, broker_square_off: str
                   ) -> List[str]:
    """Why a live MIS entry in ``symbol`` can't go out now (no network)."""
    local = now.astimezone(NSE.tz)
    rule = cas_rule(symbol, local.date())
    if rule is None:
        return []
    latest = parse_clock(rule.broker_square_off) - MARGIN_MIN
    out = []
    if parse_clock(flat_by) > latest:
        out.append(
            f"action required: {symbol} is in NSE's closing auction session until "
            f"{rule.end.isoformat()} (continuous trading ends {rule.continuous_end}; Zerodha "
            f"squares off MIS at {rule.broker_square_off}). Your flat-by {flat_by} is too late "
            f"— set flat-by to {_hhmm(latest)} or earlier in Desk settings")
    if local.hour * 60 + local.minute >= latest:
        out.append(f"too late for {symbol} today: live MIS entries stop at {_hhmm(latest)} "
                   f"(Zerodha squares off at {rule.broker_square_off}; the auction after "
                   f"{rule.continuous_end} accepts no stop-loss orders)")
    return out


def status(now: datetime, flat_by: str) -> dict:
    """For the connection screen: is a CAS window active today, and is the
    profile's flat-by early enough for it."""
    day = now.astimezone(NSE.tz).date()
    rule = next((w for w in CAS_WINDOWS if w.start <= day <= w.end), None)
    if rule is None:
        return {"active": False}
    latest = _hhmm(parse_clock(rule.broker_square_off) - MARGIN_MIN)
    return {"active": True, "until": rule.end.isoformat(), "continuous_end": rule.continuous_end,
            "broker_square_off": rule.broker_square_off, "latest_flat_by": latest,
            "profile_ok": flat_by <= latest, "applies_to": "stocks with F&O contracts"}
