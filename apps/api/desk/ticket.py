# Ported from day-trader-skill (https://github.com/Jakeschincariol/day-trader-skill,
# commit 3d0f4ef), MIT License, Copyright (c) 2026 Jake Schincariol.
# Adapted for Drishti: paper ledger + Kite Publisher hand-off instead of Alpaca.
"""Tickets: the order in plain words, matched to its risk verdict by fingerprint.

Nothing goes anywhere until the user confirms THAT exact ticket. Confirming
re-runs the risk check on the current state (the price, today's trades and
the clock may have moved) and requires the same fingerprint the verdict was
issued for — an edited order is a new ticket.

On confirm the ticket goes to one of:
  paper    the in-app paper ledger (desk/paper.py)
  broker   handed to the user's broker: a Kite Publisher basket (the user
           confirms the order on Zerodha's own screen) or a copyable ticket.
           Drishti never sends an order through a broker API from this
           feature — that's the separate, owner-only Kite Auto-trader.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def plain_words(order: Dict[str, Any]) -> str:
    qty, sym = order["qty"], order["symbol"]
    how = (f"at ₹{order['price']:,.2f} limit" if order["order_type"] == "LIMIT"
           else "at market")
    product = "intraday (MIS)" if order.get("product") == "MIS" else "delivery (CNC)"
    plan = order.get("plan") or {}
    tail = []
    if order.get("stop"):
        tail.append(f"stop ₹{order['stop']:,.2f}")
    if order.get("target"):
        tail.append(f"target ₹{order['target']:,.2f}")
    if plan.get("flat_by") and order.get("product") == "MIS":
        tail.append(f"flat by {plan['flat_by']}")
    return f"Buy {qty} {sym} on NSE {how}, {product}." + (
        " " + ", ".join(tail).capitalize() + "." if tail else "")


def kite_basket(order: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Kite Publisher basket items for the ENTRY only.

    The stop isn't in the basket on purpose: a stop-loss SELL placed before
    the entry fills would open a short if it triggered first. Place the stop
    (SL-M, or a GTT for delivery) right after the entry fills.
    """
    item = {"exchange": "NSE", "tradingsymbol": order["symbol"], "quantity": int(order["qty"]),
            "transaction_type": "BUY", "order_type": order["order_type"],
            "product": order.get("product", "MIS"), "variety": "regular"}
    if order["order_type"] == "LIMIT":
        item["price"] = order["price"]
    return [item]


def build(*, plan: Dict[str, Any], sizing: Dict[str, Any], order: Optional[Dict[str, Any]],
          verdict: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The ticket document stored in desk_tickets."""
    if order is None:
        return {"plan_id": plan.get("id"), "symbol": plan["symbol"], "status": "refused",
                "order": None, "sizing": sizing, "verdict": None, "fingerprint": "",
                "words": sizing.get("refusal") or "no order", "kite_basket": []}
    res = (verdict or {}).get("results", [{}])[0]
    status = "ready" if res.get("verdict") == "PASS" else "blocked"
    return {"plan_id": plan.get("id"), "symbol": order["symbol"], "status": status,
            "order": order, "sizing": sizing, "verdict": res,
            "verdict_warnings": (verdict or {}).get("warnings", []),
            "fingerprint": res.get("fingerprint", ""), "words": plain_words(order),
            "kite_basket": kite_basket(order)}
