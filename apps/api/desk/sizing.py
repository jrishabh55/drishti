# Ported from day-trader-skill (https://github.com/Jakeschincariol/day-trader-skill,
# commit 3d0f4ef), MIT License, Copyright (c) 2026 Jake Schincariol.
# Adapted for NSE (₹, whole shares, tick sizes, charges net of the trade) by QuberTech.
"""Position sizing: one plan + your limits -> the share count and the order.

Every limit gives a share count and the smallest one wins (upstream
``size_trade``):

  max loss       max_loss_per_trade_inr / risk per share
  position cap   max_position_pct of capital / entry
  max order      max_order_inr / entry
  cash           (cash − buffer) / entry      — no leverage in v1

The entry it sizes from: the limit price for a limit order; for a market
order the HIGHER of the plan's entry and a fresh price, so a plan made at
09:40 can't understate the risk at 10:20 after the price ran up.

New for India: round-trip charges are estimated at the stop and at the
target, so the max loss and the reward:risk are shown gross AND net. A plan
whose charges eat more than a quarter of the risk gets a note — small
intraday trades are often charge-dominated.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from apps.api.desk.market import charges as charges_mod, ticks
from apps.api.desk.profile import DeskProfile

CHARGES_HEAVY = 0.25


class SizeError(ValueError):
    pass


def _shares(rupees: float, per_share: float) -> int:
    if per_share <= 0:
        return 0
    return max(0, int(math.floor(rupees / per_share + 1e-9)))


def size_trade(plan: Dict[str, Any], profile: DeskProfile, *, cash: Optional[float] = None,
               price: Optional[float] = None, order_type: str = "LIMIT",
               today: Optional[str] = None) -> Dict[str, Any]:
    """Pure: plan + profile -> sizing dict (qty may be 0 with a refusal)."""
    sym = str(plan.get("symbol") or "").strip().upper()
    if not sym:
        raise SizeError("the plan has no symbol")
    if str(plan.get("side") or "buy").lower() != "buy":
        raise SizeError("the desk sizes long (buy) trades only")
    if today and plan.get("date") and str(plan["date"]) != today and profile.product == "MIS":
        raise SizeError(f"the plan is for {plan['date']} and today is {today}: an intraday plan "
                        "is for its own day — make a new plan")
    entry_plan, stop, target = (float(plan.get(k) or 0) for k in ("entry", "stop", "target"))
    for name, v in (("entry", entry_plan), ("stop", stop), ("target", target)):
        if not (v > 0 and math.isfinite(v)):  # NaN fails every comparison: test the good case
            raise SizeError(f"the plan needs a positive {name}")
    if sym in set(profile.blocked):
        raise SizeError(f"{sym} is on your blocked list")
    order_type = order_type.upper()
    if order_type not in ("LIMIT", "MARKET"):
        raise SizeError("order type is LIMIT or MARKET")
    if order_type == "LIMIT":
        ref, ref_from = ticks.round_to_tick(entry_plan), "limit"
    elif price is not None and price > entry_plan:
        ref, ref_from = ticks.round_to_tick(price), "price"
    else:
        ref, ref_from = entry_plan, "plan"
    if stop >= ref:
        raise SizeError(f"the stop ₹{stop:.2f} is not below the entry ₹{ref:.2f}")
    if target <= ref:
        raise SizeError(f"the target ₹{target:.2f} is not above the entry ₹{ref:.2f}: the move "
                        "has already happened. Skip it or make a new plan")
    risk = round(ref - stop, 4)
    capital = profile.capital
    buffer = round(capital * profile.cash_buffer_pct / 100, 2)
    available = (cash if cash is not None else capital) - buffer
    cash_note = (f"cash ₹{cash:,.2f}" if cash is not None else f"capital ₹{capital:,.2f}") + \
        f" minus the ₹{buffer:,.2f} buffer"
    available = max(round(available, 2), 0.0)
    limits: List[Dict[str, Any]] = [
        {"name": "max loss", "inr": profile.max_loss_per_trade_inr, "per": risk,
         "shares": _shares(profile.max_loss_per_trade_inr, risk),
         "how": f"₹{profile.max_loss_per_trade_inr:,.0f} max loss / ₹{risk:.2f} risk a share"},
        {"name": "position cap", "inr": capital * profile.max_position_pct / 100, "per": ref,
         "shares": _shares(capital * profile.max_position_pct / 100, ref),
         "how": f"{profile.max_position_pct:g}% of ₹{capital:,.0f} / ₹{ref:.2f}"},
        {"name": "max order", "inr": profile.max_order_inr, "per": ref,
         "shares": _shares(profile.max_order_inr, ref),
         "how": f"₹{profile.max_order_inr:,.0f} / ₹{ref:.2f}"},
        {"name": "cash", "inr": available, "per": ref, "shares": _shares(available, ref),
         "how": f"{cash_note} = ₹{available:,.2f} / ₹{ref:.2f}"},
    ]
    qty = min(lim["shares"] for lim in limits)
    bound = next(lim["name"] for lim in limits if lim["shares"] == qty)
    for lim in limits:
        lim["binds"] = lim["name"] == bound
    cost = round(qty * ref, 2)
    loss = round(qty * risk, 2)
    brk = profile.brokerage()
    c_stop = charges_mod.round_trip_cost(ref, stop, qty, profile.product, brk) if qty else 0.0
    c_target = charges_mod.round_trip_cost(ref, target, qty, profile.product, brk) if qty else 0.0
    reward = round(qty * (target - ref), 2)
    rr = round((target - ref) / risk, 2)
    rr_net = round((reward - c_target) / (loss + c_stop), 2) if qty else None
    refusal = None
    notes: List[str] = []
    if qty == 0:
        if bound == "max loss":
            refusal = (f"one share risks ₹{risk:.2f} (entry ₹{ref:.2f} minus stop ₹{stop:.2f}), "
                       f"more than your ₹{profile.max_loss_per_trade_inr:,.0f} max loss per trade. "
                       "No order. Skip it, or plan a closer stop.")
        else:
            how = next(lim["how"] for lim in limits if lim["name"] == bound)
            refusal = f"one share of {sym} costs ₹{ref:.2f}, more than your {bound} limit ({how})."
    elif rr_net is not None and profile.min_reward_risk and rr_net < profile.min_reward_risk - 1e-9:
        refusal = (f"reward:risk after charges is {rr_net:.2f} (gross {rr:.2f}), under your minimum "
                   f"{profile.min_reward_risk:g}, so the risk check would block it. No order.")
    elif cost < profile.min_order_inr - 0.005:
        refusal = (f"{qty} share{'s' if qty != 1 else ''} of {sym} is ₹{cost:,.2f}, under your "
                   f"₹{profile.min_order_inr:,.0f} minimum order.")
    if qty and loss and c_stop / loss > CHARGES_HEAVY:
        notes.append(f"Charges (≈₹{c_stop:.2f} round trip at the stop) are "
                     f"{100 * c_stop / loss:.0f}% of the ₹{loss:,.2f} you risk: a wider stop with "
                     "fewer shares, or skipping it, may be better.")
    return {
        "symbol": sym, "side": "buy", "setup": plan.get("setup", ""), "date": plan.get("date"),
        "entry_time": plan.get("entry_time"), "entry_plan": entry_plan, "entry": ref,
        "entry_from": ref_from, "stop": stop, "target": target, "risk_per_share": round(risk, 2),
        "reward_risk": rr, "reward_risk_net": rr_net, "capital": capital,
        "cash_buffer": buffer, "cash_available": available, "limits": limits, "qty": qty,
        "bound_by": bound, "cost": cost, "max_loss": loss,
        "max_loss_net": round(loss + c_stop, 2), "reward_at_target": reward,
        "reward_at_target_net": round(reward - c_target, 2),
        "charges_at_stop": round(c_stop, 2), "charges_at_target": round(c_target, 2),
        "flat_by": plan.get("flat_by") or profile.flat_by, "product": profile.product,
        "order_type": order_type, "refusal": refusal, "notes": notes,
    }


def order_from(s: Dict[str, Any], plan_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The proposed entry order (broker-neutral, Kite-shaped fields) or None."""
    if s["refusal"] or s["qty"] <= 0:
        return None
    o = {"symbol": s["symbol"], "exchange": "NSE", "series": "EQ", "side": "buy",
         "qty": int(s["qty"]), "order_type": s["order_type"], "product": s["product"],
         "validity": "DAY", "stop": s["stop"], "target": s["target"]}
    if s["order_type"] == "LIMIT":
        o["price"] = s["entry"]
    o["plan"] = {"plan_id": plan_id, "entry": s["entry"], "stop": s["stop"], "target": s["target"],
                 "risk_per_share": s["risk_per_share"], "max_loss": s["max_loss"],
                 "setup": s["setup"], "entry_time": s["entry_time"], "flat_by": s["flat_by"],
                 "date": s["date"]}
    o["why"] = (f"{s['qty']} shares of {s['symbol']}, bound by your {s['bound_by']} "
                f"({s['qty']} × ₹{s['risk_per_share']:.2f} = ₹{s['max_loss']:,.2f} at the stop, "
                f"≈₹{s['max_loss_net']:,.2f} with charges)")
    return o
