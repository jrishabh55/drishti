"""Paper trading: confirmed tickets filled against the plan's own bars
(5-minute by default, 1-minute for 1m plans; delayed unless the user streams
live). Each trade records its ``interval`` and is only ever advanced on bars
of that interval.

Upstream defaults to Alpaca's paper account; Drishti keeps its own ledger so
paper trading needs no broker at all. The fill model is deliberately
conservative and bar-based (we never see ticks):

* **Entry** — only bars that START at/after the confirmation count (no
  back-filling into a bar that was already printing). A MARKET buy fills at
  that bar's open plus ``slippage_ticks`` adverse ticks. A LIMIT buy fills at
  the open if the open is at/below the limit, else at the limit once a bar's
  low reaches it. Not filled by ``no_entries_after`` → cancelled.
* **Exit** — bar by bar, the STOP is checked before the target in every bar
  (a bar touching both is a loss, as in the Time Machine), including the
  fill bar itself. A stop gapped through fills at the bar's open — worse
  than planned, as a real stop would. The target fills at the target (or a
  better open). Still open at ``flat_by`` → out at the close of the bar
  ending at flat-by.
* Charges come from market/charges.py with the user's broker plan, so the
  ledger shows gross and net P&L and R.
* An optional strategy ``exit_policy`` (breakeven / trailing stop / one
  scale-out, all in R of the INITIAL risk) only acts on completed bars: a
  stop moved by a bar protects from the NEXT bar on, the stop still beats the
  scale-out and target inside a bar, and processed bars are never replayed.
  Without a policy the fixed stop/target semantics above are unchanged.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from apps.api.desk.bars import Bar
from apps.api.desk.market import charges as charges_mod, ticks
from apps.api.desk.market.session import NSE, SessionSpec, clock24, parse_clock


#: Strategy attribution stamped on plans, tickets, orders and trades.
ATTRIBUTION = ("strategy_id", "strategy_name", "strategy_version")


def interval_of(trade_or_plan: Dict[str, Any]) -> str:
    """Legacy trades and plans without an interval were 5-minute."""
    return trade_or_plan.get("interval") or "5m"


def spec_of(trade: Dict[str, Any]) -> SessionSpec:
    return replace(NSE, bar_min=1) if interval_of(trade) == "1m" else NSE


def new_trade(order: Dict[str, Any], *, ticket_id: str, placed_at: datetime,
              profile, spec: SessionSpec = NSE) -> Dict[str, Any]:
    plan = order.get("plan") or {}
    local = placed_at.astimezone(spec.tz)
    extra = {k: order[k] for k in ATTRIBUTION + ("exit_policy",) if order.get(k) is not None}
    return {**extra,
        "ticket_id": ticket_id, "symbol": order["symbol"], "side": "buy", "qty": order["qty"],
        "interval": interval_of(plan),
        "order_type": order["order_type"], "limit": order.get("price"),
        "stop": order.get("stop") or plan.get("stop"),
        "target": order.get("target") or plan.get("target"),
        "planned_entry": plan.get("entry"), "risk_per_share": plan.get("risk_per_share"),
        "flat_by": plan.get("flat_by") or profile.flat_by,
        "entry_deadline": profile.no_entries_after or clock24(spec.close_min),
        "product": order.get("product", "MIS"), "trade_date": local.date().isoformat(),
        "placed_at": placed_at.isoformat(), "slippage_ticks": profile.slippage_ticks,
        "status": "pending", "entry_fill": None, "exit_fill": None, "events": [
            {"at": local.strftime("%H:%M"), "what": f"order placed (paper): buy {order['qty']} "
                                                    f"{order['symbol']} {order['order_type']}"}],
    }


def _close_min(b: Bar, spec: SessionSpec) -> int:
    return spec.bar_close_min(b["slot"])


def advance(trade: Dict[str, Any], bars: Sequence[Bar], now: datetime, profile,
            spec: Optional[SessionSpec] = None) -> Dict[str, Any]:
    """Pure: walk the trade forward over completed session bars of the trade's
    own interval (``spec`` defaults to it). Idempotent."""
    spec = spec or spec_of(trade)
    if spec.bar_min != spec_of(trade).bar_min:
        raise ValueError(f"a {interval_of(trade)} paper trade can't be advanced on "
                         f"{spec.bar_min}-minute bars")
    t = dict(trade)
    t["events"] = list(trade.get("events") or [])
    if t["status"] in ("closed", "cancelled"):
        return t
    day = t["trade_date"]
    placed = datetime.fromisoformat(t["placed_at"]).astimezone(spec.tz)
    placed_min = placed.hour * 60 + placed.minute + (1 if placed.second or placed.microsecond else 0)
    day_bars = sorted((b for b in bars if b["date"] == day), key=lambda b: b["slot"])
    now_local = now.astimezone(spec.tz)
    day_over = now_local.date().isoformat() > day or \
        now_local.hour * 60 + now_local.minute >= spec.close_min
    flat_min = parse_clock(t["flat_by"])
    deadline = parse_clock(t["entry_deadline"])
    tick = ticks.tick_size(t.get("limit") or t.get("planned_entry") or t["stop"])
    policy = t.get("exit_policy") or None

    def ev(b_min: int, what: str) -> None:
        t["events"].append({"at": clock24(b_min), "what": what})

    for b in day_bars:
        start_min = spec.bar_start_min(b["slot"])
        close_min = _close_min(b, spec)
        if t["status"] == "open" and t.get("entry_fill") and \
                start_min <= parse_clock(t["entry_fill"]["time"]):
            # A later refresh starts with an open row: never replay prices
            # before its fill, or award a target on the already-processed fill bar.
            continue
        if t["status"] == "pending":
            if start_min < placed_min:
                continue
            if start_min >= deadline or start_min >= flat_min:
                t["status"] = "cancelled"
                ev(start_min, f"not filled by {clock24(min(deadline, flat_min))}: cancelled")
                break
            fill = None
            if t["order_type"] == "MARKET":
                fill = b["o"] + t["slippage_ticks"] * tick
            elif b["o"] <= t["limit"]:
                fill = b["o"]
            elif b["l"] <= t["limit"]:
                fill = t["limit"]
            if fill is None:
                continue
            fill = ticks.round_to_tick(fill, tick)
            t["entry_fill"] = {"time": clock24(start_min), "price": fill}
            t["status"] = "open"
            t["mae"] = t["mfe"] = 0.0
            if policy:
                t.update(initial_stop=t["stop"], open_qty=t["qty"], bars_through=start_min,
                         exit_legs=[])
            ev(start_min, f"bought {t['qty']} at ₹{fill:.2f}")
            # The fill bar can stop us out (conservative), never hit the target.
            if b["l"] <= t["stop"]:
                _exit(t, t["stop"], start_min, "stop", ev)
                break
            continue
        if t["status"] != "open":
            break
        if policy and start_min <= t.get("bars_through", -1):
            continue
        entry = t["entry_fill"]["price"]
        risk = entry - t.get("initial_stop", t["stop"])
        if risk > 0:
            t["mae"] = round(min(t.get("mae", 0.0), (b["l"] - entry) / risk), 2)
            t["mfe"] = round(max(t.get("mfe", 0.0), (b["h"] - entry) / risk), 2)
        if b["l"] <= t["stop"]:
            _exit(t, min(b["o"], t["stop"]), start_min, t.get("stop_kind", "stop"), ev)
            break
        if policy and risk > 0:
            _scale_out(t, b, entry + (policy.get("scale_out_at_r") or 0) * risk, start_min, ev)
        if b["h"] >= t["target"]:
            _exit(t, max(b["o"], t["target"]), start_min, "target", ev)
            break
        if close_min >= flat_min:
            _exit(t, b["c"], close_min, "flat_by", ev)
            break
        if policy:
            if risk > 0:
                _move_stop(t, b, entry, risk, start_min, ev)
            t["bars_through"] = start_min
    if t["status"] == "pending" and day_over:
        t["status"] = "cancelled"
        ev(spec.close_min, "session over without a fill: cancelled")
    if t["status"] == "open" and day_over and day_bars:
        last = day_bars[-1]
        _exit(t, last["c"], _close_min(last, spec), "session_end", ev)
    if t["status"] == "closed":
        _settle(t, profile)
    return t


def _exit(t: Dict[str, Any], price: float, at_min: int, reason: str, ev) -> None:
    price = ticks.round_to_tick(price)
    t["exit_fill"] = {"time": clock24(at_min), "price": price, "reason": reason}
    t["status"] = "closed"
    if "open_qty" in t:
        t["exit_legs"] = (t.get("exit_legs") or []) + [
            {"time": clock24(at_min), "price": price, "qty": t["open_qty"], "reason": reason}]
        t["open_qty"] = 0
    words = {"stop": "stopped out", "target": "target hit", "flat_by": "flat by",
             "session_end": "closed at the session end", "manual": "closed by you",
             "breakeven": "stopped at breakeven", "trail": "trailing stop hit"}
    ev(at_min, f"{words.get(reason, reason)} at ₹{price:.2f}")


def _scale_out(t: Dict[str, Any], b: Bar, level: float, at_min: int, ev) -> None:
    """One partial exit at ``scale_out_at_r`` (once). Called after the stop check."""
    pol = t["exit_policy"]
    if t.get("scaled_out") or not pol.get("scale_out_at_r") or b["h"] < level:
        return
    if max(b["o"], level) >= t["target"]:
        # Rising through the bar (or gapping past both at the open), price
        # reaches the target no later than the scale-out level: the target
        # exit right after this closes the whole position there.
        return
    t["scaled_out"] = True
    q = int(t["qty"] * pol["scale_out_pct"] // 100)
    if q <= 0 or q >= t["open_qty"]:
        ev(at_min, f"scale-out of {pol['scale_out_pct']:g}% rounds to {q} shares: skipped")
        return
    price = ticks.round_to_tick(max(b["o"], level))
    t["open_qty"] -= q
    t["exit_legs"].append({"time": clock24(at_min), "price": price, "qty": q,
                           "reason": "scale_out"})
    ev(at_min, f"scaled out {q} at ₹{price:.2f}")


def _move_stop(t: Dict[str, Any], b: Bar, entry: float, risk: float, at_min: int, ev) -> None:
    """After a COMPLETED bar: raise the stop for the next bar (never lower it)."""
    pol = t["exit_policy"]
    hw = t["high_water"] = max(t.get("high_water", entry), b["h"])
    new, kind = t["stop"], t.get("stop_kind", "stop")
    if pol.get("breakeven_after_r") and hw >= entry + pol["breakeven_after_r"] * risk             and entry > new:
        new, kind = entry, "breakeven"
    if pol.get("trail_after_r") and hw >= entry + pol["trail_after_r"] * risk:
        trail = ticks.round_to_tick(hw - pol["trail_distance_r"] * risk)
        if trail > new:
            new, kind = trail, "trail"
    if new > t["stop"]:
        t["stop"], t["stop_kind"] = new, kind
        ev(at_min, f"stop raised to ₹{new:.2f} ({kind})")


def _settle(t: Dict[str, Any], profile) -> None:
    entry, exit_p, q = t["entry_fill"]["price"], t["exit_fill"]["price"], t["qty"]
    legs = t.get("exit_legs") or [{"price": exit_p, "qty": q}]
    gross = round(sum((x["price"] - entry) * x["qty"] for x in legs), 2)
    # ponytail: one round trip per leg — an estimate, like every paper charge.
    c = sum(charges_mod.round_trip_cost(entry, x["price"], x["qty"], t.get("product", "MIS"),
                                        profile.brokerage()) for x in legs)
    risk = (entry - t.get("initial_stop", t["stop"])) * q
    t["pnl_gross"] = gross
    t["charges"] = round(c, 2)
    t["pnl_net"] = round(gross - c, 2)
    t["r_gross"] = round(gross / risk, 2) if risk > 0 else None
    t["r_net"] = round((gross - c) / risk, 2) if risk > 0 else None
    t["slippage_vs_plan"] = (round(entry - t["planned_entry"], 2)
                             if t.get("planned_entry") else None)


def accounting(trade: Dict[str, Any], profile) -> Dict[str, float]:
    """What a paper trade means for its portfolio's cash right now — shared by
    the runtime account (routes.desk._account), backtest replay and analytics.

    pending: reserves the intended entry × qty. open: the fill price × the
    REMAINING open_qty is committed, and completed scale-out legs are booked
    (gross minus their per-leg estimated charges, as _settle charges them).
    closed: pnl_net/charges unchanged. Anything else (cancelled): nothing."""
    st = trade.get("status")
    zero = {"open_qty": 0, "committed": 0.0, "realized": 0.0, "charges": 0.0}
    if st == "closed":
        return dict(zero, realized=trade.get("pnl_net") or 0.0, charges=trade.get("charges") or 0.0)
    if st == "pending":
        px = trade.get("limit") or trade.get("planned_entry") or 0.0
        return dict(zero, committed=round(px * trade["qty"], 2))
    if st != "open":
        return zero
    entry = trade["entry_fill"]["price"]
    legs = trade.get("exit_legs") or []
    c = round(sum(charges_mod.round_trip_cost(entry, x["price"], x["qty"],
                                              trade.get("product", "MIS"), profile.brokerage())
                  for x in legs), 2)
    gross = sum((x["price"] - entry) * x["qty"] for x in legs)
    q = trade.get("open_qty", trade["qty"])
    return {"open_qty": q, "committed": round(entry * q, 2), "realized": round(gross - c, 2),
            "charges": c}


def close_now(trade: Dict[str, Any], price: float, now: datetime, profile,
              spec: SessionSpec = NSE) -> Dict[str, Any]:
    """Manual close at a given price (the user's call, e.g. on news)."""
    t = dict(trade)
    t["events"] = list(trade.get("events") or [])
    local = now.astimezone(spec.tz)
    m = local.hour * 60 + local.minute
    if t["status"] == "pending":
        t["status"] = "cancelled"
        t["events"].append({"at": clock24(m), "what": "cancelled by you"})
        return t
    if t["status"] != "open":
        return t
    _exit(t, price, m, "manual", lambda at, w: t["events"].append({"at": clock24(at), "what": w}))
    _settle(t, profile)
    return t


def stats(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Ledger summary over closed trades (net figures)."""
    closed = [t for t in trades if t.get("status") == "closed" and t.get("r_net") is not None]
    if not closed:
        return {"trades": 0}
    wins = [t for t in closed if t["pnl_net"] > 0]
    losses = [t for t in closed if t["pnl_net"] <= 0]
    gross_win = sum(t["pnl_net"] for t in wins)
    gross_loss = -sum(t["pnl_net"] for t in losses)
    equity, peak, dd = 0.0, 0.0, 0.0
    for t in sorted(closed, key=lambda x: (x["trade_date"], x["exit_fill"]["time"])):
        equity += t["pnl_net"]
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return {
        "trades": len(closed), "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / len(closed), 3),
        "avg_r_net": round(sum(t["r_net"] for t in closed) / len(closed), 3),
        "pnl_net": round(sum(t["pnl_net"] for t in closed), 2),
        "charges": round(sum(t["charges"] for t in closed), 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "max_drawdown": round(dd, 2),
    }
