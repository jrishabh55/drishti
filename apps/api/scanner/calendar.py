"""NSE market calendar: fixed hours + a static holiday list.

Normal sessions only. The dated exchange snapshot must be refreshed when
NSE publishes another year or an extraordinary closure.
"""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# Official NSE capital-market trading holidays, retrieved 2026-10-29.
# https://www.nseindia.com/api/holiday-master?type=trading (CM segment)
# Muhurat trading is a separate special session; these are normal-session hours.
HOLIDAYS: set[str] = {
    "2026-01-15",  # Municipal Corporation Election - Maharashtra
    "2026-01-26",  # Republic Day
    "2026-02-15",  # Mahashivratri
    "2026-03-03",  # Holi
    "2026-03-21",  # Id-Ul-Fitr (Ramadan Eid)
    "2026-03-26",  # Shri Ram Navami
    "2026-03-31",  # Shri Mahavir Jayanti
    "2026-04-03",  # Good Friday
    "2026-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
    "2026-05-01",  # Maharashtra Day
    "2026-05-28",  # Bakri Id
    "2026-06-26",  # Muharram
    "2026-08-15",  # Independence Day
    "2026-09-14",  # Ganesh Chaturthi
    "2026-10-02",  # Mahatma Gandhi Jayanti
    "2026-10-20",  # Dussehra
    "2026-11-08",  # Diwali Laxmi Pujan*
    "2026-11-10",  # Diwali-Balipratipada
    "2026-11-24",  # Prakash Gurpurb Sri Guru Nanak Dev
    "2026-12-25",  # Christmas
}

OPEN = time(9, 15)
CLOSE = time(15, 30)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in HOLIDAYS


def is_market_open(now: datetime | None = None) -> bool:
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        raise ValueError("is_market_open requires a timezone-aware datetime")
    now = now.astimezone(IST)
    return is_trading_day(now.date()) and OPEN <= now.time() <= CLOSE
