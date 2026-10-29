"""Round-trip charge estimates for NSE cash equity — a versioned schedule.

Statutory rates change (STT in Union Budgets, exchange fees by circular), so
they live in dated schedules rather than constants: `schedule_for(day)` picks
the latest schedule effective on that day. Brokerage is a *profile* setting
(it's the broker's price, not the exchange's).

Rates below are what's published as of the ``effective_from`` date; VERIFY
against your contract notes — a contract note is the only authoritative
number. Everything here is an estimate shown next to P&L, never used to
decide whether an order is legal.

Not included (small or account-specific): NSE IPFT, DP charges are optional
via the profile (`dp_charge_per_sell`, delivery only), clearing charges.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Literal

Product = Literal["MIS", "CNC"]


@dataclass(frozen=True)
class ChargeSchedule:
    effective_from: str
    #: Securities Transaction Tax, as a fraction of turnover.
    stt_intraday_sell: float = 0.00025       # 0.025 % on the sell side
    stt_delivery_both: float = 0.001         # 0.1 % on buy and sell
    #: NSE transaction charge (cash segment), fraction of turnover, both sides.
    exchange_txn: float = 0.0000297          # 0.00297 % — NSE/FA/64232 (IPFT excluded)
    #: SEBI turnover fee: ₹10 per crore.
    sebi_fee: float = 10 / 1e7
    #: Stamp duty, buy side only.
    stamp_intraday_buy: float = 0.00003      # 0.003 %
    stamp_delivery_buy: float = 0.00015      # 0.015 %
    gst: float = 0.18                        # on brokerage + exchange + SEBI fee


SCHEDULES: tuple[ChargeSchedule, ...] = (
    ChargeSchedule(effective_from="2024-10-01"),
)


def schedule_for(day: date | None = None) -> ChargeSchedule:
    key = (day or date.today()).isoformat()
    eligible = [s for s in SCHEDULES if s.effective_from <= key]
    return (eligible or list(SCHEDULES))[-1]


@dataclass(frozen=True)
class Brokerage:
    """The user's broker plan. Default: the common discount-broker plan —
    intraday min(₹20, 0.03 %) per executed order, delivery free."""

    intraday_flat: float = 20.0
    intraday_pct: float = 0.0003
    delivery_flat: float = 0.0
    delivery_pct: float = 0.0
    dp_charge_per_sell: float = 0.0   # e.g. 15.34 incl. GST on Zerodha CNC sells

    def per_order(self, value: float, product: Product) -> float:
        if product == "MIS":
            return min(self.intraday_flat, self.intraday_pct * value)
        if self.delivery_pct:
            return min(self.delivery_flat or float("inf"), self.delivery_pct * value)
        return self.delivery_flat


@dataclass
class ChargeBreakdown:
    brokerage: float
    stt: float
    exchange_txn: float
    sebi_fee: float
    stamp_duty: float
    gst: float
    dp_charge: float
    total: float
    schedule: str = field(default="")

    def as_dict(self) -> dict:
        return {k: round(v, 2) if isinstance(v, float) else v for k, v in asdict(self).items()}


def estimate(buy_value: float, sell_value: float, product: Product = "MIS",
             brokerage: Brokerage | None = None, day: date | None = None) -> ChargeBreakdown:
    """Charges for one round trip (one buy order + one sell order).

    ``buy_value`` / ``sell_value`` are rupee turnovers (qty × price).
    """
    if buy_value < 0 or sell_value < 0:
        raise ValueError("turnover cannot be negative")
    s = schedule_for(day)
    b = brokerage or Brokerage()
    turnover = buy_value + sell_value
    brk = (b.per_order(buy_value, product) if buy_value else 0.0) + \
          (b.per_order(sell_value, product) if sell_value else 0.0)
    if product == "MIS":
        stt = s.stt_intraday_sell * sell_value
        stamp = s.stamp_intraday_buy * buy_value
        dp = 0.0
    else:
        stt = s.stt_delivery_both * turnover
        stamp = s.stamp_delivery_buy * buy_value
        dp = b.dp_charge_per_sell if sell_value else 0.0
    txn = s.exchange_txn * turnover
    sebi = s.sebi_fee * turnover
    gst = s.gst * (brk + txn + sebi)
    total = brk + stt + txn + sebi + stamp + gst + dp
    return ChargeBreakdown(brokerage=brk, stt=stt, exchange_txn=txn, sebi_fee=sebi,
                           stamp_duty=stamp, gst=gst, dp_charge=dp, total=total,
                           schedule=s.effective_from)


def round_trip_cost(entry: float, exit_price: float, qty: int, product: Product = "MIS",
                    brokerage: Brokerage | None = None, side: str = "buy") -> float:
    """Total charges for qty shares entered at ``entry`` and exited at ``exit_price``."""
    if side == "buy":
        buy_v, sell_v = entry * qty, exit_price * qty
    else:
        buy_v, sell_v = exit_price * qty, entry * qty
    return estimate(buy_v, sell_v, product, brokerage).total
