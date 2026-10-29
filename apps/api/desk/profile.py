"""Per-user desk profile: capital, risk limits, session times, broker plan.

Port of upstream's ``trader/config.json`` (config.example.json), in rupees
and IST. Defaults are deliberately conservative and every one is editable on
/desk/settings. The risk checks never change a limit themselves.
"""
from __future__ import annotations

import math
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from apps.api.desk.market.charges import Brokerage
from apps.api.desk.market.session import NSE, parse_clock


def _clock(v: Optional[str]) -> Optional[str]:
    if v is None or v == "":
        return None
    m = parse_clock(v)
    return "%02d:%02d" % divmod(m, 60)


class DeskProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    capital: float = Field(100_000, gt=0, description="Account size in ₹ for sizing and caps")
    cash_buffer_pct: float = Field(10, ge=0, lt=100)
    max_position_pct: float = Field(50, gt=0, le=100)
    min_order_inr: float = Field(500, ge=0)
    max_order_inr: float = Field(50_000, gt=0)
    max_loss_per_trade_inr: float = Field(500, gt=0)
    max_daily_loss_inr: float = Field(1_500, gt=0)
    max_trades_per_day: int = Field(3, ge=1, le=100)
    max_orders_per_day: int = Field(10, ge=1, le=500)
    min_reward_risk: float = Field(1.0, ge=0)
    require_stop: bool = True
    no_entries_before: Optional[str] = "09:30"
    no_entries_after: Optional[str] = "14:45"
    flat_by: str = "15:15"
    broker_square_off: str = "15:20"
    product: Literal["MIS", "CNC"] = "MIS"
    allow_short: bool = False
    blocked: List[str] = Field(default_factory=list)
    slippage_ticks: int = Field(2, ge=0, le=50, description="Paper fills: ticks of adverse slippage")
    # Broker plan (charges estimate only).
    brokerage_intraday_flat: float = Field(20.0, ge=0)
    brokerage_intraday_pct: float = Field(0.03, ge=0, le=5, description="percent")
    brokerage_delivery_flat: float = Field(0.0, ge=0)
    dp_charge_per_sell: float = Field(0.0, ge=0)
    # Intraday universe filters (ORB scan).
    universe: Literal["fno", "nifty500", "all"] = "fno"
    min_price: float = Field(50, ge=0)
    min_avg_value_cr: float = Field(10, ge=0, description="20-day avg traded value, ₹ crore")
    or_minutes: int = Field(15, ge=5, le=120)

    @field_validator("no_entries_before", "no_entries_after", "flat_by", "broker_square_off",
                     mode="before")
    @classmethod
    def _times(cls, v):
        return _clock(v) if v is not None else v

    @field_validator("blocked", mode="before")
    @classmethod
    def _upper(cls, v):
        return sorted({str(s).strip().upper() for s in (v or []) if str(s).strip()})

    @model_validator(mode="after")
    def _coherent(self) -> "DeskProfile":
        if self.flat_by >= self.broker_square_off:
            raise ValueError("flat_by must be before the broker's auto square-off time")
        close = "%02d:%02d" % divmod(NSE.close_min, 60)
        if self.broker_square_off > close:
            raise ValueError("broker_square_off must be at or before the 15:30 close")
        if self.no_entries_before and self.no_entries_after and \
                self.no_entries_before >= self.no_entries_after:
            raise ValueError("no_entries_before must be earlier than no_entries_after")
        if self.max_order_inr < self.min_order_inr:
            raise ValueError("max_order_inr must be at least min_order_inr")
        if self.max_daily_loss_inr < self.max_loss_per_trade_inr:
            raise ValueError("max_daily_loss_inr must be at least max_loss_per_trade_inr")
        return self

    def brokerage(self) -> Brokerage:
        return Brokerage(intraday_flat=self.brokerage_intraday_flat,
                         intraday_pct=self.brokerage_intraday_pct / 100,
                         delivery_flat=self.brokerage_delivery_flat,
                         dp_charge_per_sell=self.dp_charge_per_sell)


def load_profile(user_id: str, store=None) -> DeskProfile:
    from apps.api.desk.store import get_desk_store

    doc = (store or get_desk_store()).get_profile(user_id)
    if not doc:
        return DeskProfile()
    try:
        return DeskProfile(**doc)
    except Exception:  # noqa: BLE001 — a profile saved by an older schema: keep what still parses
        # A non-finite number (saved before they were refused) falls back to its default.
        known = {k: v for k, v in doc.items() if k in DeskProfile.model_fields
                 and not (isinstance(v, float) and not math.isfinite(v))}
        try:
            return DeskProfile(**known)
        except Exception:  # noqa: BLE001
            return DeskProfile()
