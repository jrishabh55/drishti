"""NSE equity tick sizes and rounding.

NSE moved stocks under ₹250 to a ₹0.01 tick on 10 Jun 2024 and introduced
price-band tick sizes for higher-priced stocks from 15 Apr 2025. NSE actually
assigns each stock's tick from its closing price at month end, so a live
price straddling a band edge can be one band off for the rest of the month —
harmless for planning arithmetic (it only moves a stop by a paisa or five).

VERIFY the table against NSE's current circular before relying on it for
order prices (the broker rejects off-tick limit prices; Kite rounds nothing).
ponytail: a static table; swap for NSE's per-symbol tick list if we ever
place orders off these numbers.
"""
from __future__ import annotations

import math
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

AS_OF = "2025-04-15"

#: (upper price bound exclusive, tick) — ascending.
TICK_TABLE: tuple[tuple[float, float], ...] = (
    (250.0, 0.01),
    (1000.0, 0.05),
    (5000.0, 0.10),
    (10000.0, 0.50),
    (20000.0, 1.00),
    (math.inf, 5.00),
)


def tick_size(price: float) -> float:
    for bound, tick in TICK_TABLE:
        if price < bound:
            return tick
    return TICK_TABLE[-1][1]  # pragma: no cover


def _round(price: float, tick: float, mode: str) -> float:
    q = Decimal(str(tick))
    units = (Decimal(str(price)) / q).quantize(Decimal(1), rounding=mode)
    return float(units * q)


def round_to_tick(price: float, tick: float | None = None) -> float:
    return _round(price, tick or tick_size(price), ROUND_HALF_UP)


def round_away(price: float, long: bool, tick: float | None = None) -> float:
    """Round a STOP away from the entry: down for a long, up for a short.

    The NSE version of upstream's ``cents_away``: a stop that rounds toward
    the entry would quietly shrink the risk the size was computed from.
    """
    t = tick or tick_size(price)
    # Kill float noise first (358.32000000001 must stay 358.32, not 358.33).
    p = round(price, 6)
    return _round(p, t, ROUND_FLOOR if long else ROUND_CEILING)


def round_toward(price: float, long: bool, tick: float | None = None) -> float:
    """Round a TARGET toward the entry: down for a long, up for a short, so a
    limit at the rounded price fills no later than the computed one would."""
    t = tick or tick_size(price)
    p = round(price, 6)
    return _round(p, t, ROUND_FLOOR if long else ROUND_CEILING)
