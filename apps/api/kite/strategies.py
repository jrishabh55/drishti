"""Bounded strategies built only from Trade Desk blocks: off | paper | confirm | auto.

A strategy watches up to 20 NSE symbols (its own list plus, optionally, the
matches of an owned or prebuilt scanner) for an ORB or level breakout (desk
entry.py) on the user's 1m or 5m bars — live Kite bars when their hub is
streaming, otherwise the delayed source, and every signal records which. On
a fresh trigger it builds a desk plan and a sized, risk-checked desk ticket
through the existing desk routes (same limits, same fingerprinted verdict)
in the strategy's portfolio, stamped with strategy_id/name/version, then:

* **paper** — confirms the ticket into that portfolio's paper ledger.
* **confirm** — leaves the ticket ready and records a signal awaiting the
  user's tap within ``confirm_ttl_s``; confirming goes through every live
  execution gate (execution.send_entry). Expired signals cancel their ticket.
* **auto** — sends the ticket through the SAME exact-ticket path as a manual
  confirm (routes.kite._send_ticket: daily arm, owner binding, orders-enabled
  env, risk re-check, fingerprint, kill), and only while the strategy's
  current version carries a separate ``auto_opt_in``. Creating an auto
  strategy alone sends nothing; any edit or the kill switch drops the opt-in.

Confirm/auto run on the Default portfolio only and need live (``live:``)
bars: delayed data never becomes a live signal. Every scan records
per-symbol diagnostics in ``last_scan``, separate from the signal list. A
scan acts only while the stored strategy still has the version, mode and
state it loaded, so an edit, pause (mode off + ``paused_from``, shared with
Telegram supervision) or kill since always wins.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from apps.api.desk import bars as bars_mod, bars_source, entry as entry_mod
from apps.api.desk.market.session import NSE
from apps.api.desk.portfolios import DEFAULT as DEFAULT_PORTFOLIO, ID_PATTERN
from apps.api.desk.profile import load_profile
from apps.api.kite import execution
from apps.api.kite.store import get_kite_store

logger = logging.getLogger(__name__)

#: A trigger older than this (minutes, per interval) is history, not a signal to act on.
FRESH_MINUTES = {"5m": 10, "1m": 3}
MAX_SIGNALS_KEPT = 50
MAX_SYMBOLS = 20
ACTIVE_MODES = ("paper", "confirm", "auto")
LIVE_MODES = ("confirm", "auto")


class ExitPolicy(BaseModel):
    """Advanced exits in R of the initial risk (desk/paper.py applies them)."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    breakeven_after_r: Optional[float] = Field(None, gt=0, le=20)
    trail_after_r: Optional[float] = Field(None, gt=0, le=20)
    trail_distance_r: Optional[float] = Field(None, gt=0, le=20)
    scale_out_at_r: Optional[float] = Field(None, gt=0, le=20)
    scale_out_pct: Optional[float] = Field(None, gt=0, lt=100)

    @model_validator(mode="after")
    def _pairs(self):
        if (self.trail_after_r is None) != (self.trail_distance_r is None):
            raise ValueError("a trailing stop needs both trail_after_r and trail_distance_r")
        if (self.scale_out_at_r is None) != (self.scale_out_pct is None):
            raise ValueError("a scale-out needs both scale_out_at_r and scale_out_pct")
        return self


class Strategy(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    name: str = Field(min_length=1, max_length=80)
    mode: Literal["off", "paper", "confirm", "auto"] = "off"
    symbols: List[str] = Field(default_factory=list, max_length=MAX_SYMBOLS)
    setup: Literal["orb", "level"] = "orb"
    level: Optional[float] = Field(None, gt=0)
    or_minutes: int = Field(15, ge=5, le=120)
    stop_rule: Literal["or_mid", "or_low", "pct", "atr", "price"] = "or_mid"
    stop_param: Optional[float] = None
    target_r: float = Field(2.0, gt=0, le=10)
    # "price": a fixed absolute target (research levels); "r": target_r × risk.
    target_rule: Literal["r", "price"] = "r"
    target_param: Optional[float] = Field(None, gt=0)
    order_type: Literal["LIMIT", "MARKET"] = "LIMIT"
    max_signals_per_day: int = Field(1, ge=1, le=5)
    confirm_ttl_s: int = Field(120, ge=30, le=900)
    portfolio_id: str = Field(DEFAULT_PORTFOLIO, pattern=ID_PATTERN)
    interval: Literal["1m", "5m"] = "5m"
    scanner_id: Optional[str] = Field(None, min_length=1, max_length=64)
    min_volume_x: Optional[float] = Field(None, gt=0, le=50)
    exit_policy: Optional[ExitPolicy] = None

    @field_validator("symbols", mode="before")
    @classmethod
    def _syms(cls, v):
        try:
            return sorted({bars_mod.nse_symbol(s) for s in (v or [])})
        except bars_mod.BarsError as exc:
            raise ValueError(str(exc)) from exc

    @model_validator(mode="after")
    def _rules(self):
        if self.stop_rule == "price" and not (self.stop_param and self.stop_param > 0):
            raise ValueError("a price stop needs a positive stop_param (the stop price)")
        if self.target_rule == "price" and self.target_param is None:
            raise ValueError("a fixed target needs target_param (the target price)")
        stop = self.stop_param if self.stop_rule == "price" else None
        target = self.target_param if self.target_rule == "price" else None
        if stop is not None and target is not None and stop >= target:
            raise ValueError("the stop price must be below the target price")
        if self.setup == "level" and self.level is not None:
            if stop is not None and stop >= self.level:
                raise ValueError("the stop price must be below the trigger level")
            if target is not None and target <= self.level:
                raise ValueError("the target price must be above the trigger level")
        if not self.symbols and not self.scanner_id:
            raise ValueError("pick at least one symbol or a scanner")
        if self.mode in LIVE_MODES and self.portfolio_id != DEFAULT_PORTFOLIO:
            raise ValueError("confirm and auto modes trade the real account: use the Default "
                             "portfolio (named portfolios are paper only)")
        if self.mode in LIVE_MODES and self.exit_policy is not None:
            raise ValueError("advanced exits run in paper and backtests only; live brackets keep "
                             "the fixed stop and target")
        return self

    def doc(self) -> Dict[str, Any]:
        d = self.model_dump()
        d["exit_policy"] = self.exit_policy.model_dump(exclude_none=True) \
            if self.exit_policy else None
        return d


def version_of(s: Dict[str, Any]) -> int:
    """Legacy strategies saved before versioning are version 1."""
    return int(s.get("version") or 1)


def attribution(s: Dict[str, Any]) -> Dict[str, Any]:
    return {"strategy_id": s.get("id"), "strategy_name": s.get("name"),
            "strategy_version": version_of(s)}


def target_of(s: Dict[str, Any]) -> Dict[str, Any]:
    """PlanBody/build_plan target fields; legacy strategies are R-multiple."""
    if s.get("target_rule") == "price":
        return {"target_rule": "price", "target_param": s["target_param"]}
    return {"target_rule": "r", "target_param": s["target_r"]}


def opted_in(s: Dict[str, Any]) -> bool:
    """The auto opt-in only covers the version it was given for."""
    opt = s.get("auto_opt_in") or {}
    return s.get("mode") == "auto" and opt.get("version") == version_of(s)


def _local(now: datetime) -> datetime:
    return now.astimezone(NSE.tz)


def _signal(strategy: Dict[str, Any], now: datetime, **kw: Any) -> Dict[str, Any]:
    sig = {"id": f"{_local(now):%H%M%S}-{uuid.uuid4().hex[:6]}",
           "date": _local(now).date().isoformat(), "at": now.isoformat(),
           **attribution(strategy), **kw}
    strategy["signals"] = ((strategy.get("signals") or []) + [sig])[-MAX_SIGNALS_KEPT:]
    return sig


def scanner_ok(scanner: Optional[Dict[str, Any]], user_id: str) -> bool:
    return bool(scanner) and (scanner.get("prebuilt") or scanner.get("user_id") == user_id)


def candidates(user_id: str, s: Dict[str, Any], *, now: Optional[datetime] = None) -> Tuple[List[str], Optional[Dict[str, Any]]]:
    """The strategy's own symbols, then its scanner's matches, at most 20."""
    syms = list(s.get("symbols") or [])
    if not s.get("scanner_id"):
        return syms[:MAX_SYMBOLS], None
    info: Dict[str, Any] = {"id": s["scanner_id"], "matches": 0, "error": None}
    try:
        from apps.api.scanner.engine import run_for_user
        from apps.api.scanner.schema import parse_definition
        from apps.api.scanner.store import get_scanner_store

        sc = get_scanner_store().get_scanner(s["scanner_id"])
        if not scanner_ok(sc, user_id):
            raise ValueError("the scanner was deleted or isn't yours")
        res = run_for_user(user_id, parse_definition(sc["definition"]), now=now)
        info["data_as_of"] = res.get("data_as_of")
        info["source"] = res.get("source")
        info["coverage"] = res.get("coverage")
        found = [m["symbol"] for m in res.get("matches") or []]
        info["matches"] = len(found)
        for sym in found:
            sym = bars_mod.nse_symbol(sym)
            if sym not in syms:
                syms.append(sym)
    except Exception as exc:  # noqa: BLE001 — a broken scanner leaves the own list working
        info["error"] = str(exc)[:200]
    if len(syms) > MAX_SYMBOLS:
        info["truncated"] = len(syms) - MAX_SYMBOLS
    return syms[:MAX_SYMBOLS], info


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


def confirmed_rows(rows: list, source: str, now: datetime, spec) -> Tuple[list, Optional[str]]:
    """A delayed feed's newest cached row for today may be a partial OHLC
    snapshot that only LOOKS complete by the clock (as in the ORB scan): hold
    it back until a later row confirms it. Live rows come off the bar grid
    and are complete once their interval has passed."""
    if not source.startswith("delayed:") or not rows:
        return rows, None
    newest = max(rows, key=lambda r: r[0])
    if spec.to_local(newest[0]).date() != now.astimezone(spec.tz).date():
        return rows, None
    return [r for r in rows if r is not newest], newest[0].isoformat()


def _outcome(status: str, reason: str = "", **kw: Any) -> Dict[str, Any]:
    return {"status": status, "reason": reason, **kw}


def scan(user_id: str, s: Dict[str, Any], now: datetime) -> List[Dict[str, Any]]:
    """One pass of one strategy. Returns the new signals (and saves them)."""
    store = get_kite_store()
    changed = expire_signals(user_id, s, now)
    new: List[Dict[str, Any]] = []
    mode = s.get("mode")
    if mode not in ACTIVE_MODES:
        _save(user_id, s, new, changed)
        return new
    interval = s.get("interval") or "5m"
    spec = bars_source.spec_for(interval)
    today = _local(now).date().isoformat()
    todays = [g for g in s.get("signals") or [] if g["date"] == today]
    done = {g["symbol"] for g in todays}
    prof = load_profile(user_id)
    syms, scanner_info = candidates(user_id, s, now=now)
    outcomes: Dict[str, Dict[str, Any]] = {}
    sources = set()
    for sym in syms:
        if len(todays) + len(new) >= s["max_signals_per_day"]:
            outcomes[sym] = _outcome("limit_reached", f"{s['max_signals_per_day']} signal(s) "
                                                      "a day already used")
            continue
        if sym in done:
            outcomes[sym] = _outcome("limit_reached", "already signalled today")
            continue
        try:
            series = bars_source.get_session_rows(user_id, sym, interval, now=now)
            sources.add(series.source)
            rows, held_back = confirmed_rows(series.rows, series.source, now, spec)
            ev = entry_mod.evaluate(rows, now, symbol=sym, setup=s["setup"],
                                    or_minutes=s["or_minutes"], level=s.get("level"),
                                    min_volume_x=s.get("min_volume_x"),
                                    no_entries_before=prof.no_entries_before,
                                    no_entries_after=prof.no_entries_after,
                                    spec=spec, source=series.source)
        except (entry_mod.EntryError, bars_mod.BarsError) as exc:
            logger.info("strategy %s skipped %s: %s", s.get("id"), sym, exc)
            outcomes[sym] = _outcome("no_data", str(exc))
            continue
        src = {"source": series.source, "data_as_of": series.data_as_of}
        if held_back:
            src["held_back"] = held_back
        if ev["status"] == "no-data":
            outcomes[sym] = _outcome("no_data", ev["reason"], **src)
            continue
        if ev["status"] != "triggered":
            outcomes[sym] = _outcome("waiting", ev["reason"], **src)
            continue
        if ev.get("minutes_since", 99) > FRESH_MINUTES[interval]:
            outcomes[sym] = _outcome("stale", f"triggered at {ev['trigger']['time']}, "
                                              f"{ev['minutes_since']} min ago: too old to act on",
                                     **src)
            continue
        if mode in LIVE_MODES and not series.source.startswith("live:"):
            outcomes[sym] = _outcome("stale", f"{series.source} is delayed data: it can't "
                                              "trigger a live order", **src)
            continue
        if mode == "auto" and not opted_in(s):
            outcomes[sym] = _outcome("blocked", f"auto mode needs its separate opt-in for "
                                                f"version {version_of(s)}; nothing was sent", **src)
            continue
        base = {"symbol": sym, "source": series.source, "trigger": ev["trigger"],
                "interval": interval, "portfolio_id": s.get("portfolio_id") or DEFAULT_PORTFOLIO}
        with execution.user_lock(user_id):
            # Acting happens under the user's lock and only while the stored
            # strategy is still this version, mode and pause state, and (auto)
            # still opted in: a kill, pause or edit since this scan loaded it wins.
            fresh = store.get_strategy(s["id"], user_id) if s.get("id") else s
            if not fresh or fresh.get("mode") != mode \
                    or version_of(fresh) != version_of(s) \
                    or (mode == "auto" and not opted_in(fresh)) \
                    or execution.control(user_id, now)["killed"]:
                outcomes[sym] = _outcome("blocked", "the strategy changed, paused or was killed "
                                                    "during this scan", **src)
                break
            sig = _act(user_id, s, sym, base, ev, now)
            new.append(sig)
            outcomes[sym] = _outcome(sig["status"], sig.get("reason", ""), **src)
    diag = {"at": now.isoformat(), "version": version_of(s), "mode": mode,
            "interval": interval, "source": ",".join(sorted(sources)) or None,
            "outcomes": outcomes, "scanner": scanner_info}
    _save(user_id, s, new, changed, diag)
    return new


def _act(user_id: str, s: Dict[str, Any], sym: str, base: Dict[str, Any],
         ev: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    from apps.api.routes import desk as desk_routes

    stamp = attribution(s)
    pid = s.get("portfolio_id") or DEFAULT_PORTFOLIO
    orr = ev.get("opening_range") or {}
    try:
        plan = desk_routes._create_plan(desk_routes.PlanBody(
            symbol=sym, interval=s.get("interval") or "5m", date=ev["date"], setup=s["setup"],
            level=s.get("level"), entry=ev["trigger"]["price"], entry_time=ev["trigger"]["time"],
            or_high=orr.get("high"), or_low=orr.get("low"), or_minutes=s["or_minutes"],
            stop_rule=s["stop_rule"], stop_param=s.get("stop_param"), **target_of(s), notes=f"strategy {s['name']} v{stamp['strategy_version']}"),
            user_id, stamp)
        tk = desk_routes._create_ticket(desk_routes.SizeBody(
            plan_id=plan["id"], order_type=s["order_type"], portfolio_id=pid), user_id,
            dict(stamp, exit_policy=s["exit_policy"]) if s.get("exit_policy") else stamp)
    except HTTPException as exc:
        return _signal(s, now, status="blocked", reason=str(exc.detail), **base)
    if tk["status"] != "ready":
        reasons = (tk.get("verdict") or {}).get("reasons") or [tk.get("status")]
        return _signal(s, now, status="blocked", ticket_id=tk["id"],
                       reason="; ".join(map(str, reasons)), **base)
    if s["mode"] == "paper":
        try:
            tk = desk_routes.confirm_ticket(tk["id"], desk_routes.ConfirmBody(
                mode="paper", portfolio_id=pid), user_id)
            return _signal(s, now, status="paper", ticket_id=tk["id"],
                           paper_trade_id=tk.get("paper_trade_id"), **base)
        except HTTPException as exc:
            return _signal(s, now, status="blocked", ticket_id=tk["id"],
                           reason=str(exc.detail), **base)
    if s["mode"] == "auto":
        return _auto_send(user_id, s, tk, base, now)
    return _signal(s, now, status="await_confirm", ticket_id=tk["id"], order=tk["order"],
                   expires_at=(now + timedelta(seconds=s["confirm_ttl_s"])).isoformat(),
                   **base)


def _auto_send(user_id: str, s: Dict[str, Any], tk: Dict[str, Any], base: Dict[str, Any],
               now: datetime) -> Dict[str, Any]:
    """The opted-in auto path: the exact-ticket live send a user's typed
    confirmation uses, so every gate (arm, binding, env switch, risk, kill)
    applies. A refusal cancels the ticket; nothing is retried."""
    from apps.api.routes import desk as desk_routes, kite as kite_routes

    try:
        trade = kite_routes._send_ticket(user_id, tk, kite_routes._confirm_words(tk["order"]),
                                         f"strategy:{s['id']}")
    except HTTPException as exc:
        d = exc.detail
        reason = "; ".join(map(str, d.get("reasons") or [d.get("message")])) \
            if isinstance(d, dict) else str(d)
        try:
            desk_routes.cancel_ticket(tk["id"], user_id)
        except HTTPException:
            pass
        return _signal(s, now, status="blocked", ticket_id=tk["id"], reason=reason, **base)
    if trade.get("rejected_by_broker"):
        return _signal(s, now, status="blocked", ticket_id=tk["id"], kite_trade_id=trade["id"],
                       reason=f"Kite refused the entry: {trade['rejected_by_broker']}", **base)
    return _signal(s, now, status="sent", ticket_id=tk["id"], kite_trade_id=trade["id"],
                   order=tk["order"], **base)


def _save(user_id: str, s: Dict[str, Any], new: List[Dict[str, Any]], changed: bool,
          diag: Optional[Dict[str, Any]] = None) -> None:
    """Write back only this scan's signal changes and diagnostics onto the
    CURRENT stored strategy: its mode/config (a kill, an edit) are never
    overwritten, and a signal another request moved on (sent, skipped) keeps
    that status."""
    if not (new or changed or diag) or not s.get("id"):
        return
    store = get_kite_store()
    with execution.user_lock(user_id):
        fresh = store.get_strategy(s["id"], user_id)
        if fresh is None:
            return
        theirs = {g["id"]: g for g in fresh.get("signals") or []}
        merged = []
        for g in s.get("signals") or []:
            cur = theirs.pop(g["id"], None)
            merged.append(cur if cur and cur.get("status") != "await_confirm" else g)
        merged += theirs.values()
        doc = dict(fresh, signals=merged[-MAX_SIGNALS_KEPT:])
        if diag:
            doc["last_scan"] = diag
        store.put_strategy(user_id, doc)


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
