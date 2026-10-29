"""Bounded strategies built only from Trade Desk blocks: off | paper | confirm.

A strategy watches up to 20 NSE symbols for an ORB or level breakout (desk
entry.py) on the user's bars — live Kite bars when their hub is streaming,
otherwise the delayed source, and every signal records which. On a fresh
trigger it builds a desk plan and a sized, risk-checked desk ticket through
the existing desk routes (same limits, same fingerprinted verdict), then:

* **paper** — confirms the ticket into the desk paper ledger.
* **confirm** — leaves the ticket ready and records a signal awaiting the
  user's tap within ``confirm_ttl_s``; confirming goes through every live
  execution gate (execution.send_entry). Expired signals cancel their ticket.

There is deliberately no ``auto`` mode here.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Literal, Optional

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.api.desk import bars as bars_mod, bars_source, entry as entry_mod
from apps.api.desk.market.session import NSE
from apps.api.desk.profile import load_profile
from apps.api.kite.store import get_kite_store

logger = logging.getLogger(__name__)

#: A trigger older than this is history, not a signal to act on.
FRESH_MINUTES = 10
MAX_SIGNALS_KEPT = 50


class Strategy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    mode: Literal["off", "paper", "confirm"] = "off"
    symbols: List[str] = Field(min_length=1, max_length=20)
    setup: Literal["orb", "level"] = "orb"
    level: Optional[float] = Field(None, gt=0)
    or_minutes: int = Field(15, ge=5, le=120)
    stop_rule: Literal["or_mid", "or_low", "pct", "atr"] = "or_mid"
    stop_param: Optional[float] = None
    target_r: float = Field(2.0, gt=0, le=10)
    order_type: Literal["LIMIT", "MARKET"] = "LIMIT"
    max_signals_per_day: int = Field(1, ge=1, le=5)
    confirm_ttl_s: int = Field(120, ge=30, le=900)

    @field_validator("symbols", mode="before")
    @classmethod
    def _syms(cls, v):
        try:
            return sorted({bars_mod.nse_symbol(s) for s in (v or [])})
        except bars_mod.BarsError as exc:
            raise ValueError(str(exc)) from exc


def _local(now: datetime) -> datetime:
    return now.astimezone(NSE.tz)


def _signal(strategy: Dict[str, Any], now: datetime, **kw: Any) -> Dict[str, Any]:
    sig = {"id": f"{len(strategy.get('signals') or []) + 1}-{_local(now):%H%M%S}",
           "date": _local(now).date().isoformat(), "at": now.isoformat(), **kw}
    strategy["signals"] = ((strategy.get("signals") or []) + [sig])[-MAX_SIGNALS_KEPT:]
    return sig


def expire_signals(user_id: str, s: Dict[str, Any], now: datetime) -> bool:
    from apps.api.routes import desk as desk_routes

    changed = False
    for sig in s.get("signals") or []:
        if sig.get("status") == "await_confirm" and datetime.fromisoformat(sig["expires_at"]) <= now:
            sig["status"] = "expired"
            changed = True
            try:
                desk_routes.cancel_ticket(sig["ticket_id"], user_id)
            except HTTPException:
                pass
    return changed


def scan(user_id: str, s: Dict[str, Any], now: datetime) -> List[Dict[str, Any]]:
    """One pass of one strategy. Returns the new signals (and saves them)."""
    from apps.api.routes import desk as desk_routes

    store = get_kite_store()
    changed = expire_signals(user_id, s, now)
    new: List[Dict[str, Any]] = []
    today = _local(now).date().isoformat()
    if s.get("mode") not in ("paper", "confirm"):
        if changed:
            store.put_strategy(user_id, s)
        return new
    todays = [g for g in s.get("signals") or [] if g["date"] == today]
    done = {g["symbol"] for g in todays}
    prof = load_profile(user_id)
    for sym in s["symbols"]:
        if len(todays) + len(new) >= s["max_signals_per_day"]:
            break
        if sym in done:
            continue
        try:
            series = bars_source.get_session_rows(user_id, sym, "5m", now=now)
            ev = entry_mod.evaluate(series.rows, now, symbol=sym, setup=s["setup"],
                                    or_minutes=s["or_minutes"], level=s.get("level"),
                                    no_entries_before=prof.no_entries_before,
                                    no_entries_after=prof.no_entries_after,
                                    source=series.source)
        except (entry_mod.EntryError, bars_mod.BarsError) as exc:
            logger.info("strategy %s skipped %s: %s", s.get("id"), sym, exc)
            continue
        if ev["status"] != "triggered" or ev.get("minutes_since", 99) > FRESH_MINUTES:
            continue
        base = {"symbol": sym, "source": series.source, "trigger": ev["trigger"]}
        try:
            plan = desk_routes.create_plan(desk_routes.PlanBody(
                symbol=sym, setup=s["setup"], level=s.get("level"), or_minutes=s["or_minutes"],
                stop_rule=s["stop_rule"], stop_param=s.get("stop_param"), target_rule="r",
                target_param=s["target_r"], notes=f"strategy {s['name']}"), user_id)
            tk = desk_routes.create_ticket(desk_routes.SizeBody(
                plan_id=plan["id"], order_type=s["order_type"]), user_id)
        except HTTPException as exc:
            new.append(_signal(s, now, status="blocked", reason=str(exc.detail), **base))
            continue
        if tk["status"] != "ready":
            reasons = (tk.get("verdict") or {}).get("reasons") or [tk.get("status")]
            new.append(_signal(s, now, status="blocked", ticket_id=tk["id"],
                               reason="; ".join(map(str, reasons)), **base))
            continue
        if s["mode"] == "paper":
            try:
                tk = desk_routes.confirm_ticket(tk["id"], desk_routes.ConfirmBody(mode="paper"),
                                                user_id)
                new.append(_signal(s, now, status="paper", ticket_id=tk["id"],
                                   paper_trade_id=tk.get("paper_trade_id"), **base))
            except HTTPException as exc:
                new.append(_signal(s, now, status="blocked", ticket_id=tk["id"],
                                   reason=str(exc.detail), **base))
        else:
            new.append(_signal(s, now, status="await_confirm", ticket_id=tk["id"],
                               order=tk["order"],
                               expires_at=(now + timedelta(seconds=s["confirm_ttl_s"])).isoformat(),
                               **base))
    if new or changed:
        store.put_strategy(user_id, s)
    return new


def sweep(now: datetime) -> int:
    """Background pass over every user with an active strategy."""
    store = get_kite_store()
    users = store.users_with_active_strategies()
    for uid in users:
        for s in store.list_strategies(uid):
            try:
                scan(uid, s, now)
            except Exception:  # noqa: BLE001 — one bad strategy must not stop the rest
                logger.exception("strategy scan failed for %s/%s", uid, s.get("id"))
    return len(users)
