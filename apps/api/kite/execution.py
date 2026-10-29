"""Owner-only, user-confirmed Kite execution with broker-side stop/target OCO.

Nothing automatic: an order is sent only when the user confirms a ready desk
ticket (or a confirm-mode strategy signal) and EVERY gate passes. The gates
run before any network call:

1. ``KITE_ORDERS_ENABLED=1`` on the server (default off — deploys never place
   orders by accident).
2. The user id is listed in ``AUTOTRADE_USER_IDS`` (Kite's retail-algo rules
   allow orders only on the API owner's own account).
3. Today's Kite session is active (the daily 06:00 IST expiry is the natural
   daily arm prerequisite).
4. The user armed TODAY with a typed acknowledgement, after logging in.
5. Not killed today. A kill holds until the next trading day.
6. A trading day, before the profile's flat-by.

Trade state machine (one row per trade, every transition in ``events``)::

    entry_sent → protected → closed            (target or stop; other leg cancelled)
               ↘ entry_rejected | entry_cancelled (timeout, nothing filled)
    protected/entry_sent → exit_sent → closed  (flat-by, kill, protection failure, manual)
    any → needs_attention                      (broker state we can't safely resolve)

The stop (SL-M) and target (LIMIT) both live at Zerodha right after the
entry fills, so a server crash never leaves a position unprotected. The
engine is the OCO: when one leg completes it cancels the other. Kite is the
source of truth — ``reconcile`` re-reads ``GET /orders`` (by our tags) on
every pass, postback and restart. Tags are derived from trade id + leg, and
a leg is never placed while an order with its tag exists, so a retry never
double-sends.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from apps.api.desk import paper
from apps.api.desk.market import ticks
from apps.api.desk.market.session import NSE, parse_clock
from apps.api.desk.profile import DeskProfile
from apps.api.kite import auth, rest
from apps.api.kite.store import OPEN_STATES, get_kite_store

logger = logging.getLogger(__name__)

ORDERS_ENABLED_ENV = "KITE_ORDERS_ENABLED"
OWNER_ENV = "AUTOTRADE_USER_IDS"
ENTRY_TIMEOUT_S = 60
LEGS = {"entry": "E", "stop": "S", "target": "T", "exit": "X"}
DONE = {"COMPLETE", "CANCELLED", "REJECTED"}


class ExecRefused(RuntimeError):
    def __init__(self, reasons: List[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def _local(now: datetime) -> datetime:
    return now.astimezone(NSE.tz)


def _today(now: datetime) -> str:
    return _local(now).date().isoformat()


def is_owner(user_id: str) -> bool:
    ids = {s.strip() for s in os.environ.get(OWNER_ENV, "").split(",") if s.strip()}
    return user_id in ids


def orders_enabled() -> bool:
    return os.environ.get(ORDERS_ENABLED_ENV, "0") == "1"


# ---------------------------------------------------------------- control

def control(user_id: str, now: datetime) -> Dict[str, Any]:
    doc = get_kite_store().get_control(user_id)
    today = _today(now)
    return {"armed": doc.get("armed_on") == today, "armed_at": doc.get("armed_at"),
            "killed": doc.get("killed_on") == today, "killed_at": doc.get("killed_at"),
            "kill_summary": doc.get("kill_summary")}


def gate_reasons(user_id: str, now: datetime, profile: DeskProfile, *,
                 for_arming: bool = False) -> List[str]:
    """Why a live order can't go out right now. Pure: no network."""
    out = []
    if not orders_enabled():
        out.append(f"live orders are disabled on this server ({ORDERS_ENABLED_ENV} is not 1)")
    if not is_owner(user_id):
        out.append(f"order placement is owner-only: your user id is not in {OWNER_ENV}")
    sess = auth.load_session(user_id, now)
    if sess is None:
        out.append("no Kite session today: log in to Zerodha first")
    ctl = control(user_id, now)
    if ctl["killed"]:
        out.append("the kill switch was used today; orders resume next trading day")
    if not for_arming:
        if not ctl["armed"]:
            out.append("not armed for today: arm after logging in")
        elif sess and ctl["armed_at"] and ctl["armed_at"] < sess["login_at"]:
            out.append("armed before today's login: arm again")
    local = _local(now)
    if not NSE.is_trading_day(local.date()):
        out.append("NSE is closed today")
    elif local.hour * 60 + local.minute >= parse_clock(profile.flat_by):
        out.append(f"past your flat-by time {profile.flat_by}")
    return out


def arm(user_id: str, now: datetime, ack: str, profile: DeskProfile) -> Dict[str, Any]:
    want = f"ARM {_today(now)}"
    if ack.strip() != want:
        raise ExecRefused([f'type exactly "{want}" to arm live orders for today'])
    reasons = gate_reasons(user_id, now, profile, for_arming=True)
    if reasons:
        raise ExecRefused(reasons)
    store = get_kite_store()
    doc = store.get_control(user_id)
    doc.update(armed_on=_today(now), armed_at=now.isoformat())
    store.put_control(user_id, doc)
    return control(user_id, now)


def disarm(user_id: str, now: datetime) -> Dict[str, Any]:
    store = get_kite_store()
    doc = store.get_control(user_id)
    doc.update(armed_on=None, armed_at=None)
    store.put_control(user_id, doc)
    return control(user_id, now)


# ---------------------------------------------------------------- orders

def _ev(t: Dict[str, Any], now: datetime, what: str) -> None:
    t.setdefault("events", []).append({"at": _local(now).strftime("%H:%M:%S"), "what": what})


def tag_for(trade_id: str, leg: str) -> str:
    return f"D{trade_id[:12]}{LEGS[leg]}"   # alphanumeric, 14 ≤ Kite's 20


def _by_tag(client: rest.KiteClient) -> Dict[str, Dict[str, Any]]:
    return {o["tag"]: o for o in client.orders() if o.get("tag")}


def _place(client: rest.KiteClient, t: Dict[str, Any], leg: str, orders: Dict[str, Dict],
           **params: Any) -> Dict[str, Any]:
    tag = tag_for(t["id"], leg)
    existing = orders.get(tag)
    if existing is not None:   # idempotent: a previous attempt reached Kite
        rec = {"tag": tag, "order_id": existing["order_id"], "status": existing.get("status")}
    else:
        oid = client.place_order("regular", exchange="NSE", tradingsymbol=t["symbol"],
                                 product=t["product"], validity="DAY", tag=tag, **params)
        rec = {"tag": tag, "order_id": oid, "status": "SENT"}
    t.setdefault("legs", {})[leg] = rec
    return rec


def _sync(t: Dict[str, Any], orders: Dict[str, Dict]) -> None:
    for leg, rec in (t.get("legs") or {}).items():
        o = orders.get(rec["tag"])
        if o:
            rec.update(order_id=o["order_id"], status=o.get("status"),
                       filled=int(o.get("filled_quantity") or 0),
                       avg=float(o.get("average_price") or 0) or None,
                       message=o.get("status_message"))


def _cancel(client: rest.KiteClient, t: Dict[str, Any], leg: str, now: datetime) -> None:
    rec = (t.get("legs") or {}).get(leg)
    if rec and rec.get("status") not in DONE:
        try:
            client.cancel_order(rec["order_id"])
            rec["status"] = "CANCEL_SENT"
        except rest.KiteError as exc:
            _ev(t, now, f"cancel {leg} failed: {exc}")


def _exit_market(client: rest.KiteClient, t: Dict[str, Any], orders, now: datetime,
                 reason: str) -> None:
    for leg in ("stop", "target"):
        _cancel(client, t, leg, now)
    qty = t.get("filled_qty") or 0
    if qty <= 0:
        t["status"], t["exit_reason"] = "closed", reason
        _ev(t, now, f"closed ({reason}); nothing was filled")
        return
    try:
        _place(client, t, "exit", orders, transaction_type="SELL", order_type="MARKET",
               quantity=qty, market_protection=-1)
        t["status"], t["exit_reason"] = "exit_sent", reason
        _ev(t, now, f"exit sent: sell {qty} at market ({reason})")
    except rest.KiteError as exc:
        t["status"] = "needs_attention"
        t["attention"] = f"exit order failed ({exc}); close {qty} {t['symbol']} in the Kite app"
        _ev(t, now, t["attention"])


def _protect(client, t, orders, now) -> None:
    qty = t["filled_qty"]
    try:
        _place(client, t, "stop", orders, transaction_type="SELL", order_type="SL-M",
               quantity=qty, trigger_price=t["stop"], market_protection=-1)
        _place(client, t, "target", orders, transaction_type="SELL", order_type="LIMIT",
               quantity=qty, price=t["target"])
    except rest.KiteError as exc:
        _ev(t, now, f"protection failed: {exc}")
        t["protection_failed"] = True
        _exit_market(client, t, orders, now, "protection_failed")
        return
    t["status"] = "protected"
    _ev(t, now, f"protected at the broker: stop {t['stop']} (SL-M), target {t['target']}")


def _settle(t: Dict[str, Any], exit_avg: float, profile: DeskProfile, now: datetime) -> None:
    shaped = {"entry_fill": {"price": t["entry_avg"]}, "exit_fill": {"price": exit_avg},
              "qty": t["filled_qty"], "stop": t["stop"], "planned_entry": t.get("planned_entry"),
              "product": t["product"]}
    paper._settle(shaped, profile)
    t["exit_avg"] = exit_avg
    t["exit_fill"] = {"price": exit_avg, "time": _local(now).strftime("%H:%M:%S")}
    for k in ("pnl_gross", "charges", "pnl_net", "r_gross", "r_net", "slippage_vs_plan"):
        t[k] = shaped[k]
    t["status"] = "closed"


def new_trade(user_id: str, ticket: Dict[str, Any], now: datetime,
              profile: DeskProfile, *, source: str = "ticket") -> Dict[str, Any]:
    o = ticket["order"]
    if o.get("side") != "buy" or o.get("product") != "MIS":
        raise ExecRefused(["live execution supports intraday (MIS) buys only"])
    if not o.get("stop") or not o.get("target"):
        raise ExecRefused(["a live order needs both a stop and a target"])
    plan = o.get("plan") or {}
    return {"ticket_id": ticket.get("id"), "source": source, "symbol": o["symbol"],
            "qty": int(o["qty"]), "order_type": o["order_type"], "limit": o.get("price"),
            "stop": ticks.round_to_tick(float(o["stop"])),
            "target": ticks.round_to_tick(float(o["target"])),
            "planned_entry": plan.get("entry"), "max_loss": plan.get("max_loss"),
            "flat_by": plan.get("flat_by") or profile.flat_by, "product": "MIS",
            "trade_date": _today(now), "sent_at": now.isoformat(), "status": "entry_sent",
            "filled_qty": 0, "legs": {}, "events": []}


def daily_loss_reasons(user_id: str, now: datetime, profile: DeskProfile,
                       new_max_loss: float) -> List[str]:
    """The desk risk check counts paper P&L; live losses and live open risk
    (at the stops) count against the same max daily loss here."""
    today = [t for t in get_kite_store().list_trades(user_id, limit=500)
             if t["trade_date"] == _today(now)]
    realized = sum(t.get("pnl_net") or 0.0 for t in today if t["status"] == "closed")
    open_risk = sum(float(t.get("max_loss") or 0) for t in today if t["status"] in OPEN_STATES)
    used = max(0.0, -realized) + open_risk + float(new_max_loss or 0)
    if used > profile.max_daily_loss_inr:
        return [f"live loss today ₹{max(0.0, -realized):,.0f} + open risk ₹{open_risk:,.0f} + this "
                f"trade's ₹{float(new_max_loss or 0):,.0f} exceeds your max daily loss "
                f"₹{profile.max_daily_loss_inr:,.0f}"]
    return []


def send_entry(user_id: str, ticket: Dict[str, Any], now: datetime, profile: DeskProfile,
               client_factory, *, source: str = "ticket") -> Dict[str, Any]:
    """Gates → trade row → entry order. ``client_factory()`` is only called
    after every gate passed, so a refused user never touches the network."""
    reasons = gate_reasons(user_id, now, profile) or daily_loss_reasons(
        user_id, now, profile, (ticket["order"].get("plan") or {}).get("max_loss") or 0)
    if reasons:
        raise ExecRefused(reasons)
    store = get_kite_store()
    if any(t["symbol"] == ticket["order"]["symbol"] for t in
           store.list_trades(user_id, open_only=True)):
        raise ExecRefused([f"{ticket['order']['symbol']} already has a live trade in flight"])
    t = store.create_trade(user_id, new_trade(user_id, ticket, now, profile, source=source))
    client = client_factory()
    params = {"transaction_type": "BUY", "quantity": t["qty"]}
    if t["order_type"] == "LIMIT":
        params.update(order_type="LIMIT", price=t["limit"])
    else:
        params.update(order_type="MARKET", market_protection=-1)
    try:
        _place(client, t, "entry", {}, **params)
        _ev(t, now, f"entry sent: buy {t['qty']} {t['symbol']} {t['order_type']}")
    except rest.KiteError as exc:
        t["status"] = "entry_rejected"
        _ev(t, now, f"Kite refused the entry: {exc}")
        if exc.token_error:
            auth.mark_revoked(user_id)
    store.update_trade(user_id, t)
    return t


def reconcile(user_id: str, t: Dict[str, Any], client: rest.KiteClient, now: datetime,
              profile: DeskProfile, orders: Optional[Dict[str, Dict]] = None) -> Dict[str, Any]:
    """Advance one trade from Kite's order book. Safe to call repeatedly."""
    if t["status"] not in OPEN_STATES:
        return t
    orders = orders if orders is not None else _by_tag(client)
    for leg in LEGS:   # crash recovery: an order reached Kite but its id was never saved
        tag = tag_for(t["id"], leg)
        if leg not in (t.get("legs") or {}) and tag in orders:
            t.setdefault("legs", {})[leg] = {"tag": tag, "order_id": orders[tag]["order_id"]}
            _ev(t, now, f"adopted {leg} order {orders[tag]['order_id']} found at Kite")
    _sync(t, orders)
    legs = t.get("legs") or {}
    local = _local(now)
    past_flat = local.hour * 60 + local.minute >= parse_clock(t["flat_by"])

    if t["status"] == "entry_sent":
        e = legs.get("entry") or {}
        if e.get("status") in ("REJECTED", "CANCELLED") and not e.get("filled"):
            t["status"] = "entry_rejected" if e["status"] == "REJECTED" else "entry_cancelled"
            _ev(t, now, f"entry {e['status'].lower()}: {e.get('message') or 'no fill'}")
        elif e.get("status") == "COMPLETE" or (e.get("status") == "CANCELLED" and e.get("filled")):
            t["filled_qty"], t["entry_avg"] = e["filled"], e["avg"]
            _ev(t, now, f"entry filled: {e['filled']} @ {e['avg']}")
            if past_flat:
                _exit_market(client, t, orders, now, "flat_by")
            else:
                _protect(client, t, orders, now)
        elif past_flat or (now - datetime.fromisoformat(t["sent_at"])).total_seconds() \
                > ENTRY_TIMEOUT_S:
            if not e:
                t["status"] = "entry_cancelled"
                _ev(t, now, "the entry never reached Kite")
            elif not t.get("entry_cancel_sent"):
                _cancel(client, t, "entry", now)
                t["entry_cancel_sent"] = True
                _ev(t, now, "entry not filled in time: cancel sent")
    elif t["status"] == "protected":
        s, g = legs.get("stop") or {}, legs.get("target") or {}
        if s.get("status") == "COMPLETE" and g.get("status") == "COMPLETE":
            t["status"] = "needs_attention"
            t["attention"] = (f"stop and target BOTH filled (OCO race): you may be short "
                              f"{t['filled_qty']} {t['symbol']}; check positions in Kite")
            _ev(t, now, t["attention"])
        elif s.get("status") == "COMPLETE":
            _cancel(client, t, "target", now)
            t["exit_reason"] = "stop"
            _settle(t, s["avg"], profile, now)
            _ev(t, now, f"stopped out @ {s['avg']}; target cancelled")
        elif g.get("status") == "COMPLETE":
            _cancel(client, t, "stop", now)
            t["exit_reason"] = "target"
            _settle(t, g["avg"], profile, now)
            _ev(t, now, f"target hit @ {g['avg']}; stop cancelled")
        elif past_flat:
            _exit_market(client, t, orders, now, "flat_by")
        elif s.get("status") in ("CANCELLED", "REJECTED") or g.get("status") in ("CANCELLED",
                                                                                 "REJECTED"):
            # A protective leg vanished (cancelled in the Kite app, or rejected):
            # never sit unprotected — flatten.
            _ev(t, now, "a protective order was cancelled or rejected at the broker")
            _exit_market(client, t, orders, now, "protection_lost")
    elif t["status"] == "exit_sent":
        x = legs.get("exit") or {}
        if x.get("status") == "COMPLETE":
            _settle(t, x["avg"], profile, now)
            _ev(t, now, f"exited @ {x['avg']} ({t.get('exit_reason')})")
        elif x.get("status") == "REJECTED":
            t["status"] = "needs_attention"
            t["attention"] = (f"exit rejected ({x.get('message')}); close {t['filled_qty']} "
                              f"{t['symbol']} in the Kite app")
            _ev(t, now, t["attention"])
    return t


def reconcile_user(user_id: str, now: datetime, profile: DeskProfile,
                   transport: Optional[rest.Transport] = None) -> List[Dict[str, Any]]:
    store = get_kite_store()
    open_trades = [t for t in store.list_trades(user_id, open_only=True)
                   if t["status"] != "needs_attention"]
    if not open_trades:
        return []
    try:
        client = auth.client_for(user_id, now, transport)
        orders = _by_tag(client)
    except (auth.KiteAuthError, rest.KiteError) as exc:
        if isinstance(exc, rest.KiteError) and exc.token_error:
            auth.mark_revoked(user_id)
        logger.warning("Kite reconcile skipped for %s: %s", user_id, exc)
        return open_trades
    out = []
    for t in open_trades:
        try:
            t = reconcile(user_id, t, client, now, profile, orders)
        except rest.KiteError as exc:
            _ev(t, now, f"reconcile error: {exc}")
        store.update_trade(user_id, t)
        out.append(t)
    return out


def exit_trade(user_id: str, tid: str, now: datetime, profile: DeskProfile,
               transport: Optional[rest.Transport] = None) -> Dict[str, Any]:
    """The user's manual 'get me out' for one trade (no arming needed)."""
    store = get_kite_store()
    t = store.get_trade(tid, user_id)
    if t is None:
        raise KeyError(tid)
    if t["status"] not in ("entry_sent", "protected"):
        raise ExecRefused([f"trade is {t['status']}"])
    client = auth.client_for(user_id, now, transport)
    orders = _by_tag(client)
    _sync(t, orders)
    if t["status"] == "entry_sent":
        e = t["legs"].get("entry") or {}
        _cancel(client, t, "entry", now)
        t["filled_qty"] = e.get("filled") or 0
        t["entry_avg"] = e.get("avg")
    _exit_market(client, t, orders, now, "manual")
    store.update_trade(user_id, t)
    return t


def kill(user_id: str, now: datetime, transport: Optional[rest.Transport] = None
         ) -> Dict[str, Any]:
    """Disarm, stop strategies, cancel every working order and exit every
    engine position. Holds for the rest of the day."""
    store = get_kite_store()
    doc = store.get_control(user_id)
    doc.update(armed_on=None, armed_at=None, killed_on=_today(now), killed_at=now.isoformat())
    for s in store.list_strategies(user_id):
        if s.get("mode") != "off":
            store.put_strategy(user_id, dict(s, mode="off", paused_reason="kill switch"))
    trades = store.list_trades(user_id, open_only=True)
    summary = {"trades": len(trades), "exits_sent": 0, "needs_attention": 0, "error": None}
    client = None
    orders: Dict[str, Dict] = {}
    if trades:
        try:
            client = auth.client_for(user_id, now, transport)
            orders = _by_tag(client)
        except (auth.KiteAuthError, rest.KiteError) as exc:
            summary["error"] = f"{exc}; close positions in the Kite app"
    for t in trades:
        if client is None:
            t["status"] = "needs_attention"
            t["attention"] = "kill switch could not reach Kite; close this in the Kite app"
        else:
            _sync(t, orders)
            if t["status"] == "entry_sent":
                e = t["legs"].get("entry") or {}
                _cancel(client, t, "entry", now)
                t["filled_qty"], t["entry_avg"] = e.get("filled") or 0, e.get("avg")
            if t["status"] in ("entry_sent", "protected"):
                _exit_market(client, t, orders, now, "kill")
        _ev(t, now, "kill switch")
        summary["exits_sent"] += t["status"] == "exit_sent"
        summary["needs_attention"] += t["status"] == "needs_attention"
        store.update_trade(user_id, t)
    doc["kill_summary"] = summary
    store.put_control(user_id, doc)
    return dict(control(user_id, now), summary=summary)


def performance(user_id: str) -> Dict[str, Any]:
    closed = [t for t in get_kite_store().list_trades(user_id, limit=1000)
              if t["status"] == "closed" and t.get("pnl_net") is not None]
    return paper.stats(closed)


def on_postback(user_id: str, data: Dict[str, Any]) -> None:
    """Order-update text message from the user's ticker: reconcile soon.

    Only our tagged orders matter; the polling loop is the fallback."""
    if not str(data.get("tag") or "").startswith("D"):
        return
    import asyncio

    from apps.api.desk.profile import load_profile

    def run():
        now = datetime.now(NSE.tz)
        reconcile_user(user_id, now, load_profile(user_id))

    try:
        asyncio.get_running_loop().create_task(asyncio.to_thread(run))
    except RuntimeError:
        run()
