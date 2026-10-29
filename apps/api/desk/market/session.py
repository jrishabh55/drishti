"""Trading-session clock, parameterised so the ported algorithms stay
session-agnostic.

`NSE` is the product session: 09:15–15:30 IST, 75 five-minute bars, pre-open
excluded. Bar ``k`` starts at ``open + 5k`` and *closes* at ``open + 5(k+1)``;
an entry "at 09:35" is the close of bar 3. Special sessions (Muhurat, weekend
budget-day sessions) and shortened days are detected from the data: a session
without every bar is "not full" and is never used as replay pool material.

The NYSE spec only exists so tests can reproduce the upstream reel's numbers
on its TSLA fixture (port-correctness); nothing in the product uses it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Optional
from zoneinfo import ZoneInfo

from apps.api.scanner.calendar import IST, is_trading_day as nse_is_trading_day


@dataclass(frozen=True)
class SessionSpec:
    name: str
    tz: tzinfo
    open_min: int          # minutes after midnight, local
    close_min: int
    bar_min: int = 5

    @property
    def n_bars(self) -> int:
        return (self.close_min - self.open_min) // self.bar_min

    def slot_of(self, local: datetime) -> Optional[int]:
        """Bar index of a bar *starting* at ``local``; None when off-grid or
        outside the session."""
        mins = local.hour * 60 + local.minute - self.open_min
        if local.second or local.microsecond or mins < 0 or mins % self.bar_min:
            return None
        k = mins // self.bar_min
        return k if k < self.n_bars else None

    def bar_start_min(self, k: int) -> int:
        return self.open_min + self.bar_min * k

    def bar_close_min(self, k: int) -> int:
        return self.open_min + self.bar_min * (k + 1)

    def bar_for_close(self, close_min: int) -> int:
        """Bar index whose close is at ``close_min``; ValueError off the grid."""
        off = close_min - self.open_min
        if off <= 0 or off % self.bar_min:
            raise ValueError(f"{clock24(close_min)} is not a {self.bar_min}-minute bar close")
        return off // self.bar_min - 1

    def local(self, d: date, minute: int) -> datetime:
        return datetime.combine(d, time(0), self.tz) + timedelta(minutes=minute)

    def to_local(self, ts: datetime) -> datetime:
        if ts.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return ts.astimezone(self.tz)

    def is_trading_day(self, d: date) -> bool:
        if self.name == "NSE":
            return nse_is_trading_day(d)
        return d.weekday() < 5


NSE = SessionSpec("NSE", IST, 9 * 60 + 15, 15 * 60 + 30)
NYSE = SessionSpec("NYSE", ZoneInfo("America/New_York"), 9 * 60 + 30, 16 * 60)


def parse_clock(text: str) -> int:
    """'10:15', '10:15 AM', '3:20 pm' -> minutes after midnight."""
    s = str(text).strip().upper().replace(".", "")
    ampm = None
    for tag in ("AM", "PM"):
        if s.endswith(tag):
            ampm, s = tag, s[: -len(tag)].strip()
    try:
        hh, mm = s.split(":")
        h, m = int(hh), int(mm)
    except ValueError:
        raise ValueError(f"time {text!r} is not like 10:15 or 3:20 PM") from None
    if ampm:
        if not 1 <= h <= 12:
            raise ValueError(f"time {text!r} has an hour outside 1-12")
        h = h % 12 + (12 if ampm == "PM" else 0)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"time {text!r} is not a clock time")
    return h * 60 + m


def clock24(minutes: int) -> str:
    return "%02d:%02d" % divmod(minutes, 60)


def clock12(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return "%d:%02d %s" % (h % 12 or 12, m, "AM" if h < 12 else "PM")
