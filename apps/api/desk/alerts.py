# Ported from day-trader-skill (https://github.com/Jakeschincariol/day-trader-skill,
# commit 3d0f4ef), MIT License, Copyright (c) 2026 Jake Schincariol.
# Adapted for NSE (IST clock, 09:15 opening bell, bar-based prices) by QuberTech.
"""Price alerts: rules that ring once.

Rule types (upstream's):
  above / target   price at or above ``price``
  below / stop     price at or below ``price``
  pct_move         moved ``pct`` % from the previous close (optional direction)
  time             the IST clock passed ``at`` on a trading day (flat-by reminders)
  opening_bell     NSE is open (09:15 IST)

Prices: the newest completed 5-minute bar (delayed). above/below look at the
bar's HIGH/LOW, not just its close — a delayed feed would otherwise miss a
touch that reversed within the bar. Each rule rings once; ``rearm`` lets an
above/below rule ring again after the price comes back. Rules with a
``date`` are watched on that IST date only, so yesterday's stop never rings
today. It never places an order.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from apps.api.desk.market.session import NSE, SessionSpec, clock24, parse_clock

TYPES = ("above", "below", "target", "stop", "pct_move", "time", "opening_bell")


class AlertError(ValueError):
    pass


def validate(rule: Dict[str, Any]) -> Dict[str, Any]:
    typ = rule.get("type")
    if typ not in TYPES:
        raise AlertError(f"type must be one of {', '.join(TYPES)}")
    r = dict(rule)
    if typ in ("above", "below", "target", "stop"):
        try:
            r["price"] = float(r["price"])
        except (KeyError, TypeError, ValueError):
            raise AlertError(f"a {typ} alert needs a price") from None
        if r["price"] <= 0:
            raise AlertError("price must be above ₹0")
    if typ == "pct_move":
        try:
            r["pct"] = float(r["pct"])
        except (KeyError, TypeError, ValueError):
            raise AlertError("a pct_move alert needs pct") from None
        if not 0 < r["pct"] < 100:
            raise AlertError("pct must be between 0 and 100")
        if r.get("direction") not in (None, "up", "down"):
            raise AlertError("direction is up or down")
    if typ == "time":
        try:
            r["at"] = clock24(parse_clock(r["at"]))
        except (KeyError, ValueError):
            raise AlertError("a time alert needs at, like 15:10") from None
    if typ not in ("time", "opening_bell"):
        sym = str(r.get("symbol") or "").strip().upper()
        if not sym:
            raise AlertError(f"a {typ} alert needs a symbol")
        r["symbol"] = sym
    elif r.get("symbol"):
        r["symbol"] = str(r["symbol"]).strip().upper()
    r.setdefault("rearm", False)
    r.setdefault("active", True)
    return r


def from_plan(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Upstream's `add --from-plan`: stop, target and flat-by for the plan's day."""
    sym, day = plan["symbol"], plan.get("date")
    base = {"symbol": sym, "date": day, "plan_id": plan.get("id")}
    out = [dict(base, type="stop", price=plan["stop"], note="plan stop"),
           dict(base, type="target", price=plan["target"], note="plan target")]
    if plan.get("flat_by"):
        out.append(dict(base, type="time", at=plan["flat_by"], label=f"flat by for {sym}"))
    return [validate(r) for r in out]


def evaluate(rule: Dict[str, Any], *, now: datetime, last_bar: Optional[Dict[str, Any]] = None,
             prev_close: Optional[float] = None, spec: SessionSpec = NSE
             ) -> Tuple[Optional[str], Dict[str, Any]]:
    """Pure: (message or None, updated rule). The rule carries its own state:
    ``rang_at`` (ISO) / ``rang_day``."""
    r = dict(rule)
    if not r.get("active", True):
        return None, r
    local = now.astimezone(spec.tz)
    today = local.date().isoformat()
    minute = local.hour * 60 + local.minute
    if r.get("date") and r["date"] != today:
        return None, r
    trading = spec.is_trading_day(local.date())
    is_open = trading and spec.open_min <= minute < spec.close_min
    typ = {"stop": "below", "target": "above"}.get(r["type"], r["type"])

    def ring(msg: str) -> Tuple[str, Dict[str, Any]]:
        r["rang_at"] = now.isoformat()
        r["rang_day"] = today
        return msg, r

    if typ == "opening_bell":
        if is_open and r.get("rang_day") != today:
            return ring(f"Opening bell: NSE is open ({local.strftime('%H:%M')} IST).")
        return None, r
    if typ == "time":
        if trading and minute >= parse_clock(r["at"]) and r.get("rang_day") != today:
            label = r.get("label") or "time alert"
            msg = f"{label[:1].upper()}{label[1:]}: it's {local.strftime('%H:%M')} IST (set for {r['at']})"
            if r.get("symbol"):
                msg += f". If {r['symbol']} is still open, close it."
            return ring(msg)
        return None, r
    if not last_bar:
        return None, r
    sym = r["symbol"]
    if typ in ("above", "below"):
        hit = last_bar["h"] >= r["price"] if typ == "above" else last_bar["l"] <= r["price"]
        if hit and not r.get("rang_at"):
            word = "reached" if typ == "above" else "fell to"
            label = {"stop": "stop", "target": "target"}.get(r["type"], "alert level")
            note = f" ({r['note']})" if r.get("note") else ""
            return ring(f"{sym} {word} your {label} ₹{r['price']:,.2f}{note} — the "
                        f"{last_bar['start']} bar traded ₹{last_bar['l']:,.2f}–"
                        f"₹{last_bar['h']:,.2f}, last ₹{last_bar['c']:,.2f}.")
        if not hit and r.get("rang_at") and r.get("rearm"):
            r.pop("rang_at", None)
            r.pop("rang_day", None)
        return None, r
    if typ == "pct_move":
        ref = r.get("ref") or prev_close
        if not ref:
            return None, r
        move = 100.0 * (last_bar["c"] / float(ref) - 1)
        want = r.get("direction")
        hit = abs(move) >= r["pct"] and (not want or (want == "up") == (move > 0))
        if hit and r.get("rang_day") != today:
            return ring(f"{sym} moved {move:+.2f}% to ₹{last_bar['c']:,.2f} (from ₹{float(ref):,.2f}).")
    return None, r
