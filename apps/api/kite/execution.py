"""Owner-only, user-confirmed Kite execution with broker-side stop/target OCO.

Nothing automatic: an order is sent only when the user confirms a ready desk
ticket (or a confirm-mode strategy signal) and EVERY gate passes. The gates
run before any network call:

1. ``KITE_ORDERS_ENABLED=1`` on the server (default off — deploys never place
   orders by accident).
2. The user id is listed in ``AUTOTRADE_USER_IDS`` as ``user:ZERODHA_ID`` and
   today's Kite login is that Zerodha client id (Kite's retail-algo rules
   allow orders only on the API owner's own account). An entry without a
   bound client id refuses — checked against the stored session, no network.
3. Today's Kite session is active (the daily 06:00 IST expiry is the natural
   daily arm prerequisite).
4. The user armed TODAY with a typed acknowledgement, after logging in.
5. Not killed today. A kill holds until the next trading day.
6. A trading day, before the profile's flat-by.

Trade state machine (one row per trade, every transition in ``events``)::

    entry_sent → protected → exit_sent → closed
               ↘ entry_rejected | entry_cancelled  (only on proof: Kite refused,
                                                     cancelled, or never listed it)
    any → needs_attention   (net short, or an exit Kite refused: a human decides)
    needs_attention → closed_outside  (Kite flat for this trade, every order finished:
                                       sold elsewhere; P&L unknown, entries blocked today)

Kite is the source of truth. Every decision is made from a fresh read of
``GET /orders`` (legs found by tag), under the user's lock, and:

* the held quantity is entry fills minus all sell fills; the stop (SL-M)
  and target (LIMIT) are sized — and resized on partial fills — to it;
* a request Kite didn't clearly answer leaves its leg UNKNOWN, then ABSENT.
  Absence is not proof: the trade is ``needs_attention`` at once, its tags
  are looked up on every pass and adopted whenever they appear, and it
  resumes by itself; nothing is re-sent or market-exited on a guess, and an
  ambiguous trade never closes. Only once the session's DAY orders have
  expired is an absent leg NOT_PLACED;
* an exit first cancels every other working order and waits for Kite to
  confirm each cancel; any exit, and the final close, also require Kite's
  ``/portfolio/positions`` (net of what the account held before the trade)
  to agree with the ledger, so the user's other positions are never sold.

Guarantees this CANNOT give, and what happens instead:

* No OCO for MIS at Kite: if the stop and target fill before the engine
  sees the first fill (or between a partial target fill and the stop's
  resize), the account is oversold. Detected from the order book (held < 0)
  and raised as needs_attention; never "fixed" with another order.
* A protective order Kite never acknowledges leaves the position
  unprotected until Kite lists it or the user acts: the engine flags this
  immediately ("may be UNPROTECTED") but will not place a second stop or a
  market exit while the first may exist (either could oversell).
* Positions can lag fills by moments; a mismatch is acted on only after
  POSITIONS_SETTLE_S, so the engine may wait that long before an exit.
* Execution runs in ONE process (``engine_lease``); a second worker or
  replica refuses orders rather than racing the first.
"""
from __future__ import annotations

import logging
import math
import os
import threading
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from apps.api.desk import paper
from apps.api.desk.market import ticks
from apps.api.desk.market.session import NSE, parse_clock
from apps.api.desk.profile import DeskProfile
from apps.api.kite import auth, rest, sessions
from apps.api.kite.store import OPEN_STATES, get_kite_store

logger = logging.getLogger(__name__)

ORDERS_ENABLED_ENV = "KITE_ORDERS_ENABLED"
OWNER_ENV = "AUTOTRADE_USER_IDS"
ENTRY_TIMEOUT_S = 60
LEGS = {"entry": "E", "stop": "S", "target": "T", "exit": "X", "exit2": "Y", "exit3": "Z"}


class ExecRefused(RuntimeError):
    def __init__(self, reasons: List[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def _local(now: datetime) -> datetime:
    return now.astimezone(NSE.tz)


def _today(now: datetime) -> str:
    return _local(now).date().isoformat()


# ---------------------------------------------------------------- one process
#
# The per-user broker lock below is an in-process lock, so execution must run
# in exactly ONE process. That is enforced, not assumed: the first process to
# use the engine takes an exclusive flock on ``<kite db>.engine.lock`` and
# keeps it for its lifetime; any other process (a second uvicorn worker, a
# second replica on the same volume) refuses orders and skips reconciliation.

_lease: Dict[str, Any] = {}
_lease_guard = threading.Lock()


def engine_lease() -> bool:
    import fcntl

    path = str(get_kite_store().path) + ".engine.lock"
    with _lease_guard:
        if path in _lease:
            return _lease[path] is not None
        fh = open(path, "a+")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            logger.error("Kite execution is owned by another process (%s); this one refuses "
                         "orders", path)
            _lease[path] = None
            return False
        _lease[path] = fh          # held for the process lifetime
        return True


def owner_binding(user_id: str) -> Optional[str]:
    """The Zerodha client id this app user may trade ("" if listed without
    one), or None when the user isn't an owner. ``AUTOTRADE_USER_IDS`` is
    ``app_user:ZERODHA_ID,...``."""
    for entry in os.environ.get(OWNER_ENV, "").split(","):
        uid, _, kid = entry.strip().partition(":")
        if uid and uid == user_id:
            return kid.strip().upper()
    return None


def is_owner(user_id: str) -> bool:
    return owner_binding(user_id) is not None


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
    if not engine_lease():
        out.append("another server process owns Kite execution: live orders run in exactly "
                   "one process (one uvicorn worker, one replica)")
    bound = owner_binding(user_id)
    if bound is None:
        out.append(f"order placement is owner-only: your user id is not in {OWNER_ENV}")
    elif not bound:
        out.append(f"your Zerodha client id isn't bound: the admin must set {OWNER_ENV} to "
                   f"{user_id}:<your Zerodha client id>")
    sess = auth.load_session(user_id, now)
    if sess is None:
        out.append("no Kite session today: log in to Zerodha first")
    elif bound and str(sess.get("kite_user_id") or "").upper() != bound:
        out.append(f"today's Kite login is client {sess.get('kite_user_id')}, but live orders "
                   f"are bound to {bound}")
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


#: Guards every read-modify-write of a user's control doc. Held only for a
#: SQLite round trip — never across the network — so the kill switch can
#: record itself immediately, before it waits for the user's broker lock.
_control_lock = threading.Lock()


def _update_control(user_id: str, fn: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
    with _control_lock:
        store = get_kite_store()
        doc = store.get_control(user_id)
        fn(doc)
        store.put_control(user_id, doc)
        return doc


def arm(user_id: str, now: datetime, ack: str, profile: DeskProfile) -> Dict[str, Any]:
    want = f"ARM {_today(now)}"
    if ack.strip() != want:
        raise ExecRefused([f'type exactly "{want}" to arm live orders for today'])
    reasons = gate_reasons(user_id, now, profile, for_arming=True)
    if reasons:
        raise ExecRefused(reasons)

    def arm_doc(doc: Dict[str, Any]) -> None:
        if doc.get("killed_on") == _today(now):     # a kill that landed meanwhile wins
            raise ExecRefused(["the kill switch was used today; orders resume next trading day"])
        doc.update(armed_on=_today(now), armed_at=now.isoformat())

    _update_control(user_id, arm_doc)
    return control(user_id, now)


def disarm(user_id: str, now: datetime) -> Dict[str, Any]:
    _update_control(user_id, lambda d: d.update(armed_on=None, armed_at=None))
    return control(user_id, now)


# ---------------------------------------------------------------- orders
#
# Every order is a *leg* of a trade, tagged ``D<trade id><letter>``. Before a
# leg is sent, its intent (status PENDING) is saved, so a crash or timeout
# mid-request leaves a record. A request that fails without a definite answer
# (network error, timeout, 5xx) leaves the leg UNKNOWN; if Kite's order book
# still doesn't list it after ABSENT_AFTER_S (two reads) it is ABSENT.
#
# Absence is NOT proof: Kite's order book can lag exactly when requests time
# out. So an UNKNOWN/ABSENT leg is never re-sent, never replaced by a market
# exit, and its trade never closes; the trade is ``needs_attention`` at once
# (the user sees it is possibly unprotected), the tag keeps being looked up on
# every pass and adopted whenever it appears, and the trade resumes by itself
# when nothing is ambiguous any more. Only after the session — when every DAY
# order has expired at the exchange — does an ABSENT leg become NOT_PLACED.

UNKNOWN, PENDING, ABSENT, NOT_PLACED = "UNKNOWN", "PENDING", "ABSENT", "NOT_PLACED"
AMBIGUOUS = (UNKNOWN, PENDING, ABSENT)
TERMINAL = {"COMPLETE", "CANCELLED", "REJECTED", NOT_PLACED}
ABSENT_AFTER_S = 20
#: DAY orders are dead at the exchange after this (IST): absence is now proof.
ORDERS_EXPIRE_AT = "15:35"
CANCEL_RETRY_S = 10
#: Kite caps modifications per order (25); stay clear and hand over instead.
MAX_MODIFIES = 20
#: A position mismatch this long (fills reach positions a moment after the
#: order book) is a real discrepancy.
POSITIONS_SETTLE_S = 30
SELL_LEGS = ("stop", "target", "exit", "exit2", "exit3")
EXIT_LEGS = ("exit", "exit2", "exit3")


def _ev(t: Dict[str, Any], now: datetime, what: str) -> None:
    evs = t.setdefault("events", [])
    if not evs or evs[-1]["what"] != what:      # a waiting state isn't logged every pass
        evs.append({"at": _local(now).strftime("%H:%M:%S"), "what": what})


def tag_for(trade_id: str, leg: str) -> str:
    return f"D{trade_id[:12]}{LEGS[leg]}"   # alphanumeric, 14 ≤ Kite's 20


def _by_tag(client: rest.KiteClient) -> Dict[str, Dict[str, Any]]:
    return {o["tag"]: o for o in client.orders() if o.get("tag")}


def definite(exc: rest.KiteError) -> bool:
    """Kite answered and refused (4xx): the order provably does not exist.
    A transport failure or 5xx proves nothing."""
    return 400 <= exc.status < 500


def _unknown(rec: Optional[Dict[str, Any]]) -> bool:
    return bool(rec) and rec.get("status") in AMBIGUOUS


def _terminal(rec: Optional[Dict[str, Any]]) -> bool:
    return bool(rec) and rec.get("status") in TERMINAL


def _working(rec: Optional[Dict[str, Any]]) -> bool:
    return bool(rec) and not _terminal(rec)


def _filled(rec: Optional[Dict[str, Any]]) -> int:
    return int((rec or {}).get("filled") or 0)


class Ambiguous(RuntimeError):
    """Kite may or may not have the order; the leg is UNKNOWN."""


class ModifyCap(RuntimeError):
    """A leg is near Kite's modification limit: a human resizes it."""


def _place(client: rest.KiteClient, t: Dict[str, Any], leg: str, orders: Dict[str, Dict],
           *, now: Optional[datetime] = None, save: Optional[Callable[[], None]] = None,
           **params: Any) -> Dict[str, Any]:
    """Send one leg once. Raises KiteError (definite refusal, leg REJECTED)
    or Ambiguous (leg UNKNOWN)."""
    tag = tag_for(t["id"], leg)
    existing = orders.get(tag)
    legs = t.setdefault("legs", {})
    if existing is not None:   # idempotent: a previous attempt reached Kite
        rec = {"tag": tag, "order_id": existing["order_id"], "status": existing.get("status"),
               "qty": int(existing.get("quantity") or params.get("quantity") or 0)}
        legs[leg] = rec
        return rec
    if leg in legs and not _terminal(legs[leg]):
        raise Ambiguous(f"{leg} already has an unresolved order")   # never send twice
    rec = {"tag": tag, "order_id": None, "status": PENDING,
           "qty": int(params.get("quantity") or 0),
           "intent_at": (now or datetime.now(NSE.tz)).isoformat(), "absent_reads": 0}
    legs[leg] = rec
    if save:
        save()                 # the intent is durable before the request leaves
    try:
        oid = client.place_order("regular", exchange="NSE", tradingsymbol=t["symbol"],
                                 product=t["product"], validity="DAY", tag=tag, **params)
    except rest.KiteError as exc:
        if definite(exc):
            rec.update(status="REJECTED", message=str(exc))
            raise
        rec["status"] = UNKNOWN
        rec["message"] = str(exc)
        raise Ambiguous(f"Kite did not confirm the {leg} order ({exc})") from exc
    rec.update(order_id=oid, status="SENT")
    return rec


def orders_expired(t: Dict[str, Any], now: datetime) -> bool:
    """Every DAY order of this trade's session has expired at the exchange."""
    local = _local(now)
    return local.date().isoformat() > t["trade_date"] or \
        local.hour * 60 + local.minute >= parse_clock(ORDERS_EXPIRE_AT)


def _sync(t: Dict[str, Any], orders: Dict[str, Dict], now: datetime) -> None:
    """Fold Kite's order book into the legs. Any of our tags that appears —
    however late, whatever the leg's state — is adopted. A leg Kite never
    answered for becomes ABSENT (still tracked), and NOT_PLACED only once the
    session's DAY orders have expired."""
    legs = t.setdefault("legs", {})
    for leg in LEGS:
        tag = tag_for(t["id"], leg)
        o = orders.get(tag)
        rec = legs.get(leg)
        if o is None:
            if _unknown(rec):
                rec["absent_reads"] = int(rec.get("absent_reads") or 0) + 1
                age = (now - datetime.fromisoformat(rec["intent_at"])).total_seconds()
                if orders_expired(t, now):
                    rec.update(status=NOT_PLACED, filled=0)
                    _ev(t, now, f"{leg} order never appeared in Kite's order book and the "
                                "session's orders have expired: it was not placed")
                elif rec["status"] != ABSENT and age >= ABSENT_AFTER_S and rec["absent_reads"] >= 2:
                    rec["status"] = ABSENT
                    _ev(t, now, f"{leg} order still isn't in Kite's order book {int(age)} s "
                                "after it was sent; it may yet appear, so nothing is re-sent")
            continue
        if rec is None or rec.get("order_id") is None or rec.get("status") == NOT_PLACED:
            _ev(t, now, f"adopted {leg} order {o['order_id']} found at Kite")
            rec = legs[leg] = dict(rec or {}, tag=tag)
        if rec.get("resize_to") is not None and int(o.get("quantity") or 0) == rec["resize_to"]:
            rec.pop("resize_to", None)
            rec.pop("resize_at", None)
        rec.update(order_id=o["order_id"], status=o.get("status"),
                   qty=int(o.get("quantity") or rec.get("qty") or 0),
                   filled=int(o.get("filled_quantity") or 0),
                   avg=float(o.get("average_price") or 0) or None,
                   message=o.get("status_message"))


def _cancel(client: rest.KiteClient, t: Dict[str, Any], leg: str, now: datetime) -> bool:
    """Ask Kite to cancel a working leg. The leg counts as cancelled only
    when the order book says CANCELLED (or COMPLETE): never assumed."""
    rec = (t.get("legs") or {}).get(leg)
    if not _working(rec) or _unknown(rec) or not rec.get("order_id"):
        return False
    last = rec.get("cancel_requested_at")
    if last and (now - datetime.fromisoformat(last)).total_seconds() < CANCEL_RETRY_S:
        return False
    rec["cancel_requested_at"] = now.isoformat()
    try:
        client.cancel_order(rec["order_id"])
    except rest.KiteError as exc:
        _ev(t, now, f"cancel {leg} failed: {exc}; re-reading Kite")
    return True


def _modify_qty(client: rest.KiteClient, t: Dict[str, Any], leg: str, total: int,
                now: datetime) -> bool:
    """Resize a working leg to ``total``. Never PUTs the same size twice in a
    row (an unconfirmed resize is not repeated until Kite shows a different
    size), and hands over before Kite's per-order modification cap."""
    rec = t["legs"][leg]
    asked = rec.get("resize_at")
    if rec.get("resize_to") == total and asked and \
            (now - datetime.fromisoformat(asked)).total_seconds() < CANCEL_RETRY_S:
        return False                      # already asked for exactly this; wait for Kite
    if int(rec.get("modifies") or 0) >= MAX_MODIFIES:
        raise ModifyCap(f"{leg} has been resized {rec['modifies']} times (Kite allows 25 "
                        f"changes per order); set its quantity to {total} in the Kite app")
    rec["modifies"] = int(rec.get("modifies") or 0) + 1
    rec["resize_to"], rec["resize_at"] = total, now.isoformat()
    try:
        client.modify_order(rec["order_id"], quantity=total)
    except rest.KiteError as exc:
        if definite(exc):
            rec.pop("resize_to", None)
            raise
        _ev(t, now, f"resize {leg} unconfirmed ({exc}); re-reading Kite")
        return True
    rec["qty"] = total
    _ev(t, now, f"{leg} resized to {total}")
    return True


def _settle(t: Dict[str, Any], profile: DeskProfile, now: datetime) -> None:
    legs = t["legs"]
    sells = [(_filled(legs[x]), legs[x].get("avg") or 0.0) for x in SELL_LEGS
             if x in legs and _filled(legs[x])]
    q = sum(n for n, _ in sells)
    exit_avg = round(sum(n * p for n, p in sells) / q, 2)
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
            "filled_qty": 0, "held_qty": 0, "legs": {}, "events": [],
            **{k: o[k] for k in paper.ATTRIBUTION if o.get(k) is not None}}

def daily_loss_reasons(user_id: str, now: datetime, profile: DeskProfile,
                       new_max_loss: float) -> List[str]:
    """The desk risk check counts paper P&L; live losses and live open risk
    (at the stops) count against the same max daily loss here."""
    today = [t for t in get_kite_store().list_trades(user_id, limit=500)
             if t["trade_date"] == _today(now)]
    outside = [t["symbol"] for t in today if t["status"] == CLOSED_OUTSIDE]
    if outside:
        # Its loss is unknown: counting it as 0 could let today's real loss
        # run past the cap. Blocked for the day instead.
        return [f"a live {', '.join(sorted(set(outside)))} trade was closed outside the engine "
                "today; its P&L is unknown, so no new live entries until the next trading day"]
    realized = sum(t.get("pnl_net") or 0.0 for t in today if t["status"] == "closed")
    open_risk = sum(float(t.get("max_loss") or 0) for t in today if t["status"] in OPEN_STATES)
    used = max(0.0, -realized) + open_risk + float(new_max_loss or 0)
    if used > profile.max_daily_loss_inr:
        return [f"live loss today ₹{max(0.0, -realized):,.0f} + open risk ₹{open_risk:,.0f} + this "
                f"trade's ₹{float(new_max_loss or 0):,.0f} exceeds your max daily loss "
                f"₹{profile.max_daily_loss_inr:,.0f}"]
    return []


# ---------------------------------------------------------------- per-user serialisation

_locks: Dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def user_lock(user_id: str) -> threading.RLock:
    """One re-entrant lock per user, held by every path that reads broker
    state and acts on it: send, reconcile (loop, postback, refresh), manual
    exit, kill and strategy writes. The API runs one process (one uvicorn
    worker), so a process lock serialises them all."""
    with _locks_guard:
        return _locks.setdefault(user_id, threading.RLock())


def send_entry(user_id: str, ticket: Dict[str, Any], now: datetime, profile: DeskProfile,
               client_factory, *, source: str = "ticket") -> Dict[str, Any]:
    """Gates → trade row (with a durable entry intent) → entry order.
    ``client_factory()`` is only called after every gate passed, so a
    refused user never touches the network."""
    with user_lock(user_id):
        doc = new_trade(user_id, ticket, now, profile, source=source)
        reasons = gate_reasons(user_id, now, profile) or daily_loss_reasons(
            user_id, now, profile, (ticket["order"].get("plan") or {}).get("max_loss") or 0)
        reasons += sessions.timing_reasons(doc["symbol"], now, doc["flat_by"],
                                           profile.broker_square_off)
        if reasons:
            raise ExecRefused(reasons)
        store = get_kite_store()
        if any(t["symbol"] == doc["symbol"] for t in store.list_trades(user_id, open_only=True)):
            raise ExecRefused([f"{doc['symbol']} already has a live trade in flight"])
        client = client_factory()
        verify_ticks(client, doc, now, user_id)     # Kite's own tick, before anything is sent
        try:
            # What the account already holds in this symbol (manual trades):
            # every later exit/close is checked against Kite's position net of it.
            doc["baseline_qty"] = _net_position(client, doc["symbol"])
        except rest.KiteError as exc:
            if exc.token_error:
                auth.mark_revoked(user_id)
            raise ExecRefused([f"couldn't read your positions from Kite ({exc}); nothing was "
                               "sent"]) from exc
        check_funds(client, doc, profile, user_id)
        # The kill switch records itself without this lock (so it is never stuck
        # behind a slow send): re-read it after the broker reads above, as the
        # last thing before anything is persisted or sent.
        if control(user_id, now)["killed"]:
            raise ExecRefused(["the kill switch was used while this order was being checked; "
                               "nothing was sent"])
        t = store.create_trade(user_id, doc)
        params = {"transaction_type": "BUY", "quantity": t["qty"]}
        if t["order_type"] == "LIMIT":
            params.update(order_type="LIMIT", price=t["limit"])
        else:
            params.update(order_type="MARKET", market_protection=-1)
        try:
            _place(client, t, "entry", {}, now=now, save=lambda: store.update_trade(user_id, t),
                   **params)
            _ev(t, now, f"entry sent: buy {t['qty']} {t['symbol']} {t['order_type']}")
        except Ambiguous as exc:
            _ev(t, now, f"{exc}: checking Kite's order book before doing anything else")
            try:
                _sync(t, _by_tag(client), now)
            except rest.KiteError:
                pass
            if _unknown(t["legs"].get("entry")):
                _attention(t, now, _ambiguity(t, ["entry"], 0), "ambiguous")
        except rest.KiteError as exc:
            t["status"] = "entry_rejected"
            t["rejected_by_broker"] = str(exc)
            _ev(t, now, f"Kite refused the entry: {exc}")
            if exc.token_error:
                auth.mark_revoked(user_id)
        store.update_trade(user_id, t)
        return t


def check_funds(client: rest.KiteClient, doc: Dict[str, Any], profile: DeskProfile,
                user_id: str) -> None:
    """Real broker funds, read fresh before any entry is placed: the equity
    segment must be enabled and its finite current balance must cover the
    full notional (no MIS leverage assumed) plus the profile's cash buffer.
    Paper capital never stands in; the approved quantity is never resized."""
    try:
        m = client.margins_equity()
    except rest.KiteError as exc:
        if exc.token_error:
            auth.mark_revoked(user_id)
        raise ExecRefused([f"couldn't read your Kite funds ({exc}); nothing was sent"]) from exc
    try:
        live = float((m.get("available") or {}).get("live_balance"))
        net = float(m.get("net"))
    except (TypeError, ValueError):
        live = net = float("nan")
    if m.get("enabled") is not True or not (math.isfinite(live) and math.isfinite(net)):
        raise ExecRefused(["Kite didn't report usable equity funds (segment disabled or balance "
                           "missing); nothing was sent"])
    balance = min(live, net)
    price = max(float(doc.get("limit") or 0), float(doc.get("planned_entry") or 0))
    need = doc["qty"] * price
    usable = balance * (1 - profile.cash_buffer_pct / 100)
    if price <= 0 or need > usable:
        raise ExecRefused([f"insufficient Kite funds: this order needs ₹{need:,.0f} in full, "
                           f"₹{usable:,.0f} is available after your {profile.cash_buffer_pct:g}% "
                           "cash buffer; nothing was sent"])
    doc["funds_checked"] = {"live_balance": live, "net": net, "required": round(need, 2)}


def _on_tick(price: float, tick: float) -> bool:
    from decimal import Decimal

    p, k = Decimal(str(price)), Decimal(str(tick))
    return k > 0 and p % k == 0


def verify_ticks(client: rest.KiteClient, doc: Dict[str, Any], now: datetime,
                 user_id: str) -> None:
    """Every price we would send must sit on the tick Kite's instrument master
    assigns this symbol — not our price-band estimate. Refuses (nothing sent)
    rather than nudging a price the user approved."""
    try:
        table = rest.nse_instruments(client, _local(now).date())
    except rest.KiteError as exc:
        if exc.token_error:
            auth.mark_revoked(user_id)
        raise ExecRefused([f"couldn't read Kite's instrument list to check the tick size "
                           f"({exc}); nothing was sent"]) from exc
    inst = table.get(doc["symbol"])
    if inst is None:
        raise ExecRefused([f"{doc['symbol']} isn't an NSE equity on Kite; nothing was sent"])
    tick = float(inst["tick_size"])
    prices = [("stop", doc["stop"]), ("target", doc["target"])]
    if doc["order_type"] == "LIMIT":
        prices.insert(0, ("limit price", doc["limit"]))
    bad = [f"{label} ₹{px} isn't a multiple of {doc['symbol']}'s tick ₹{tick:g} on Kite"
           for label, px in prices if px is None or not _on_tick(float(px), tick)]
    if bad:
        raise ExecRefused(bad + ["re-plan the trade so its prices sit on the broker's tick; "
                                 "nothing was sent"])
    doc["tick_size"] = tick


def _attention(t: Dict[str, Any], now: datetime, why: str, kind: str = "manual") -> None:
    """``kind="ambiguous"`` clears itself once Kite shows every leg; any other
    kind waits for a human."""
    t["status"], t["attention"], t["attention_kind"] = "needs_attention", why, kind
    _ev(t, now, why)


def _ambiguity(t: Dict[str, Any], amb: List[str], held: int) -> str:
    absent = [k for k in amb if t["legs"][k]["status"] == ABSENT]
    tags = ", ".join(t["legs"][k]["tag"] for k in amb)
    sells = [k for k in amb if k != "entry"]
    if absent:
        head = (f"Kite hasn't listed the {', '.join(absent)} order (tag {tags}) since it was "
                "sent. It may still appear, so nothing is re-sent automatically.")
    else:
        head = (f"Kite didn't confirm the {', '.join(amb)} order (tag {tags}); checking its "
                "order book.")
    if held > 0 and any(k in ("stop", "target") for k in sells):
        tail = (f" {held} {t['symbol']} may be UNPROTECTED: if the order isn't in the Kite app, "
                "place a stop or exit there now.")
    elif held > 0:
        tail = f" {held} {t['symbol']} is still held: check the Kite app."
    else:
        tail = " If you see this order in the Kite app, the engine will adopt it."
    return head + tail


def _net_position(client: rest.KiteClient, symbol: str) -> int:
    """Net NSE MIS quantity Kite reports for ``symbol`` (all of the user's)."""
    for p in (client.positions() or {}).get("net") or []:
        if p.get("tradingsymbol") == symbol and p.get("exchange", "NSE") == "NSE" \
                and p.get("product", "MIS") == "MIS":
            return int(p.get("quantity") or 0)
    return 0


def _positions_agree(t: Dict[str, Any], client: rest.KiteClient, now: datetime,
                     expected: int, *, at_least: bool) -> Optional[bool]:
    """Kite's own position for this trade (net minus what was there before the
    trade) vs the ledger. True: agrees. False: has disagreed for
    POSITIONS_SETTLE_S (a real discrepancy). None: wait (unread, or settling)."""
    try:
        ours = _net_position(client, t["symbol"]) - int(t.get("baseline_qty") or 0)
    except rest.KiteError as exc:
        _ev(t, now, f"couldn't read positions from Kite ({exc}); waiting")
        return None
    ok = ours >= expected if at_least else ours == expected
    if ok:
        t.pop("pos_mismatch_since", None)
        return True
    t["broker_qty"] = ours
    since = t.setdefault("pos_mismatch_since", now.isoformat())
    if (now - datetime.fromisoformat(since)).total_seconds() < POSITIONS_SETTLE_S:
        _ev(t, now, f"Kite's position ({ours}) doesn't match the trade ({expected}) yet; "
                    "re-checking")
        return None
    return False


#: A trade the user flattened elsewhere (e.g. in the Kite app). No exit price
#: or P&L is known, and none is made up.
CLOSED_OUTSIDE = "closed_outside"


def _closed_outside(t: Dict[str, Any], client: rest.KiteClient, now: datetime,
                    held: int) -> None:
    """Recognise, read-only, a trade the user closed outside the engine.

    Only on conclusive evidence: the ledger still holds some, every one of the
    trade's orders is finished at Kite (nothing working, nothing ambiguous —
    ``_step`` returns before this while any leg is), and, on the trade's own
    day, Kite's position net of the pre-trade baseline has been 0 for
    POSITIONS_SETTLE_S on fresh reads.
    Never sends anything.
    """
    legs = t.get("legs") or {}
    # Same day only: the baseline is that day's position, and today's positions
    # say nothing about an earlier day's trade.
    if held <= 0 or t["trade_date"] != _today(now) or any(not _terminal(r) for r in legs.values()):
        t.pop("outside_flat_since", None)
        return
    try:
        ours = _net_position(client, t["symbol"]) - int(t.get("baseline_qty") or 0)
    except rest.KiteError:
        return
    if ours != 0:
        t.pop("outside_flat_since", None)
        return
    since = t.setdefault("outside_flat_since", now.isoformat())
    if (now - datetime.fromisoformat(since)).total_seconds() < POSITIONS_SETTLE_S:
        return
    t["status"], t["attention"], t["attention_kind"] = CLOSED_OUTSIDE, None, None
    t["exit_reason"] = t.get("exit_reason") or "outside_engine"
    t["closed_note"] = (f"Closed outside the engine: Kite shows no {t['symbol']} for this trade "
                        f"and all its orders are finished, so {held} was sold elsewhere (e.g. "
                        "the Kite app). Exit price and P&L are unknown (see Kite's tradebook); "
                        "new live entries are blocked for the rest of today.")
    _ev(t, now, t["closed_note"])


def _step(t: Dict[str, Any], client: rest.KiteClient, orders: Dict[str, Dict],
          now: datetime, profile: DeskProfile, save: Callable[[], None]) -> bool:
    """One decision from a fresh order book. Returns True when it sent
    anything (place / cancel / modify), so the caller re-reads Kite and
    steps again. Never acts while any leg's broker state is ambiguous."""
    if t["status"] not in OPEN_STATES:
        return False
    if t["trade_date"] < _today(now):
        # MIS and DAY-order state belongs to its original session. Today's
        # position can be the user's new trade: never protect, cancel or sell it
        # to reconcile yesterday's ledger. Archive without claiming a known fill.
        t["status"], t["exit_reason"] = "session_ended", "prior_session_ended"
        t["closed_note"] = (f"The MIS session for {t['trade_date']} ended. Drishti will send no "
                            "further orders for this trade. Verify its fills and any remaining "
                            "positions in Kite; its final exit and P&L are unconfirmed.")
        t["attention"] = t["attention_kind"] = None
        for key in ("pnl_net", "pnl_gross", "exit_fill", "exit_avg"):
            t[key] = None
        _ev(t, now, "prior MIS session ended; verify the broker history (P&L unknown)")
        return False
    _sync(t, orders, now)
    legs = t["legs"]
    e = legs.get("entry")
    entry_filled = _filled(e)
    held = entry_filled - sum(_filled(legs.get(x)) for x in SELL_LEGS)
    t["filled_qty"], t["held_qty"] = entry_filled, held
    if e and e.get("avg"):
        t["entry_avg"] = e["avg"]
    amb = [k for k, r in legs.items() if _unknown(r)]
    if amb:
        # Possibly unprotected or possibly double-selling: say so now, keep
        # looking the tags up every pass, act on nothing until Kite shows them.
        _attention(t, now, _ambiguity(t, amb, held), "ambiguous")
        return False
    if t["status"] == "needs_attention":
        if t.get("attention_kind") != "ambiguous":
            _closed_outside(t, client, now, held)
            return False           # a human decides; the legs are still kept current
        t["attention"] = t["attention_kind"] = None
        t["status"] = "exit_sent" if t.get("exit_reason") else "entry_sent"
        _ev(t, now, "Kite now shows every order; resuming")
    if held < 0:
        _attention(t, now, f"more was sold than bought (stop/target OCO race): you may be "
                           f"short {-held} {t['symbol']}; check positions in Kite")
        return False
    local = _local(now)
    now_min = local.hour * 60 + local.minute
    cut = sessions.cutoffs(t["symbol"], local.date(), t["flat_by"], profile.broker_square_off)
    past_flat = now_min >= cut.flatten_by
    can_place = now_min < cut.no_orders_after
    acted = False

    if not t.get("exit_reason"):
        stop, target = legs.get("stop"), legs.get("target")
        if past_flat and (held > 0 or _working(e)):
            t["exit_reason"] = "flat_by"
            if cut.flatten_by < parse_clock(t["flat_by"]):
                _ev(t, now, f"flattening at {_hhmm(cut.flatten_by)}, before Zerodha's "
                            f"{cut.cas.broker_square_off if cut.cas else profile.broker_square_off}"
                            f" square-off" + (" (closing auction session)" if cut.cas else ""))
        elif _filled(stop):
            t["exit_reason"] = "stop"
        elif target and target.get("status") == "COMPLETE":
            t["exit_reason"] = "target"
        elif held > 0 and any(r and r.get("status") in ("CANCELLED", "REJECTED", NOT_PLACED)
                              for r in (stop, target)):
            t["exit_reason"] = "protection_lost"
            _ev(t, now, "a protective order was cancelled or rejected at the broker")

    if t.get("exit_reason"):
        return _step_exit(t, client, orders, now, profile, save, held, can_place)

    # ------------------------------------------------ entry timeout
    if _working(e) and (now - datetime.fromisoformat(t["sent_at"])).total_seconds() > ENTRY_TIMEOUT_S:
        if _cancel(client, t, "entry", now):
            _ev(t, now, "entry not filled in time: cancel sent")
            acted = True
    if _terminal(e) and entry_filled == 0:
        status = (e or {}).get("status")
        t["status"] = "entry_rejected" if status in ("REJECTED", NOT_PLACED) else "entry_cancelled"
        _ev(t, now, f"entry {str(status).lower()}: {(e or {}).get('message') or 'no fill'}")
        return acted
    if held == 0:
        t["status"] = "entry_sent"
        return acted

    # ------------------------------------------------ protection sized to what is held
    if not can_place and any(legs.get(x) is None for x in ("stop", "target")):
        _attention(t, now, _too_late(t, held))
        return acted
    try:
        for leg, params in (("stop", dict(order_type="SL-M", trigger_price=t["stop"],
                                          market_protection=-1)),
                            ("target", dict(order_type="LIMIT", price=t["target"]))):
            rec = legs.get(leg)
            if rec is None:
                _place(client, t, leg, orders, now=now, save=save, transaction_type="SELL",
                       quantity=held, **params)
                acted = True
            elif _working(rec) and not rec.get("cancel_requested_at"):
                want = _filled(rec) + held
                if int(rec.get("qty") or 0) != want:
                    acted = _modify_qty(client, t, leg, want, now) or acted
    except Ambiguous as exc:
        _ev(t, now, f"{exc}: checking Kite's order book before doing anything else")
        return True
    except ModifyCap as exc:
        _attention(t, now, str(exc))
        return acted
    except rest.KiteError as exc:
        _ev(t, now, f"protection failed: {exc}")
        t["protection_failed"] = True
        t["exit_reason"] = "protection_failed"
        return True
    if not _working(e):
        t["status"] = "protected"
        _ev(t, now, f"protected at the broker: stop {t['stop']} (SL-M), target {t['target']} "
                    f"for {held}")
    else:
        t["status"] = "entry_sent"
        _ev(t, now, f"{held} filled so far and protected; the entry is still working")
    return acted


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _too_late(t: Dict[str, Any], held: int) -> str:
    return (f"past the broker's MIS square-off for {t['symbol']}: no order is sent now (the "
            f"closing auction accepts no stop-loss orders). {held} may still be held — check "
            f"positions in the Kite app")


def _step_exit(t, client, orders, now, profile, save, held, can_place=True) -> bool:
    """Two-phase exit: cancel every other working order and wait for Kite to
    confirm each cancel, then — only if Kite's own position agrees — sell
    what the trade still holds. Never sells the user's other positions."""
    legs = t["legs"]
    t["status"] = "exit_sent"
    acted = False
    for leg in ("entry", "stop", "target"):
        acted = _cancel(client, t, leg, now) or acted
    waiting = [leg for leg in ("entry", "stop", "target") if _working(legs.get(leg))]
    if waiting:
        _ev(t, now, f"exit ({t['exit_reason']}): waiting for Kite to confirm the cancel of "
                    f"{', '.join(waiting)}")
        return acted
    exits = [x for x in EXIT_LEGS if x in legs]
    if exits and _working(legs[exits[-1]]):
        return acted
    if held > 0:
        if exits and legs[exits[-1]].get("status") == "REJECTED":
            _attention(t, now, f"exit rejected ({legs[exits[-1]].get('message')}); close {held} "
                               f"{t['symbol']} in the Kite app")
            return acted
        if not can_place:
            _attention(t, now, _too_late(t, held))
            return acted
        nxt = next((x for x in EXIT_LEGS if x not in legs), None)
        if nxt is None:
            _attention(t, now, f"three exit orders did not flatten {t['symbol']}; close {held} "
                               "in the Kite app")
            return acted
        agree = _positions_agree(t, client, now, held, at_least=True)
        if agree is None:
            return acted
        if agree is False:
            _attention(t, now, f"Kite shows {t.get('broker_qty')} {t['symbol']} for this trade "
                               f"but the trade holds {held}: not selling (it could sell your "
                               "other positions or go short); check the Kite app")
            return acted
        try:
            _place(client, t, nxt, orders, now=now, save=save, transaction_type="SELL",
                   order_type="MARKET", quantity=held, market_protection=-1)
            _ev(t, now, f"exit sent: sell {held} at market ({t['exit_reason']})")
        except Ambiguous as exc:
            _ev(t, now, f"{exc}: checking Kite's order book before doing anything else")
        except rest.KiteError as exc:
            _attention(t, now, f"exit order failed ({exc}); close {held} {t['symbol']} in the "
                               "Kite app")
        return True
    # flat by the ledger, nothing working: Kite's position must agree before closing
    agree = _positions_agree(t, client, now, 0, at_least=False)
    if agree is None:
        return acted
    if agree is False:
        _attention(t, now, f"the trade's orders are all done but Kite shows "
                           f"{t.get('broker_qty')} {t['symbol']} still open for it; check the "
                           "Kite app")
        return acted
    if t["filled_qty"] == 0:
        t["status"] = "closed"
        _ev(t, now, f"closed ({t['exit_reason']}); nothing was filled")
        return acted
    _settle(t, profile, now)
    _ev(t, now, f"closed ({t['exit_reason']}) @ {t['exit_avg']}")
    return acted


def _drive(user_id: str, trades: List[Dict[str, Any]], client: rest.KiteClient,
           now: datetime, profile: DeskProfile, passes: int = 4) -> None:
    """Step every trade from a fresh order book until nothing more is sent."""
    store = get_kite_store()
    for _ in range(passes):
        orders = _by_tag(client)
        killed = control(user_id, now)["killed"]
        acted = False
        for t in trades:
            if killed and _killable(t):
                # the kill flag is authoritative, whoever wrote it and whenever
                t["exit_reason"] = "kill"
                _ev(t, now, "kill switch")
            try:
                acted = _step(t, client, orders, now, profile,
                              lambda t=t: store.update_trade(user_id, t)) or acted
            except rest.KiteError as exc:
                _ev(t, now, f"reconcile error: {exc}")
                if exc.token_error:
                    auth.mark_revoked(user_id)
            store.update_trade(user_id, t)
        if not acted:
            return


def _killable(t: Dict[str, Any]) -> bool:
    return t["status"] in OPEN_STATES and not t.get("exit_reason") and (
        t["status"] != "needs_attention" or t.get("attention_kind") == "ambiguous")


def reconcile_user(user_id: str, now: datetime, profile: DeskProfile,
                   transport: Optional[rest.Transport] = None) -> List[Dict[str, Any]]:
    if not engine_lease():
        return []                  # another process owns execution
    with user_lock(user_id):
        store = get_kite_store()
        open_trades = store.list_trades(user_id, open_only=True)
        if not open_trades:
            return []
        try:
            client = auth.client_for(user_id, now, transport)
            _drive(user_id, open_trades, client, now, profile)
        except (auth.KiteAuthError, rest.KiteError) as exc:
            if isinstance(exc, rest.KiteError) and exc.token_error:
                auth.mark_revoked(user_id)
            logger.warning("Kite reconcile skipped for %s: %s", user_id, exc)
        return [store.get_trade(t["id"], user_id) for t in open_trades]


def exit_trade(user_id: str, tid: str, now: datetime, profile: DeskProfile,
               transport: Optional[rest.Transport] = None) -> Dict[str, Any]:
    """The user's manual 'get me out' for one trade (no arming needed)."""
    with user_lock(user_id):
        store = get_kite_store()
        t = store.get_trade(tid, user_id)
        if t is None:
            raise KeyError(tid)
        if t["status"] not in ("entry_sent", "protected", "exit_sent", "needs_attention"):
            raise ExecRefused([f"trade is {t['status']}"])
        client = auth.client_for(user_id, now, transport)
        _sync(t, _by_tag(client), now)
        if t["status"] == "needs_attention":
            legs = t.get("legs") or {}
            held = _filled(legs.get("entry")) - sum(_filled(legs.get(x)) for x in SELL_LEGS)
            if any(_unknown(r) for r in legs.values()) or held <= 0:
                raise ExecRefused([f"broker state needs a human: {t.get('attention')}"])
            t["status"], t["attention"], t["attention_kind"] = "exit_sent", None, None
        if not t.get("exit_reason"):
            t["exit_reason"] = "manual"
        store.update_trade(user_id, t)
        _drive(user_id, [t], client, now, profile)
        return store.get_trade(tid, user_id)


def kill(user_id: str, now: datetime, transport: Optional[rest.Transport] = None
         ) -> Dict[str, Any]:
    """Disarm, stop strategies, then exit every engine position through the
    same two-phase exit. If Kite can't be reached the trades stay in exit
    and the loop finishes them as soon as it can; nothing is assumed.

    The kill is recorded FIRST, without waiting for the user's broker lock
    (which a slow order send or strategy scan may hold): from that moment
    every send re-checks the gates under the lock and refuses, strategy scans
    stop acting, and any reconcile pass — in whichever request — exits."""
    _update_control(user_id, lambda d: d.update(
        armed_on=None, armed_at=None, killed_on=_today(now), killed_at=now.isoformat()))
    with user_lock(user_id):
        store = get_kite_store()
        for s in store.list_strategies(user_id):
            if s.get("mode") != "off" or s.get("auto_opt_in"):
                store.put_strategy(user_id, dict(s, mode="off", paused_reason="kill switch",
                                                 auto_opt_in=None))
        trades = store.list_trades(user_id, open_only=True)
        for t in trades:
            if _killable(t):
                t["exit_reason"] = "kill"
            _ev(t, now, "kill switch")
            store.update_trade(user_id, t)
        summary = {"trades": len(trades), "exits_sent": 0, "pending": 0, "needs_attention": 0,
                   "error": None}
        if trades and engine_lease():
            try:
                _drive(user_id, trades, auth.client_for(user_id, now, transport), now,
                       load_profile_safe(user_id))
            except (auth.KiteAuthError, rest.KiteError) as exc:
                summary["error"] = (f"{exc}. The exits stay pending and are retried while the "
                                    "session lasts; close positions in the Kite app now.")
            trades = [store.get_trade(t["id"], user_id) for t in trades]
        for t in trades:
            legs = t.get("legs") or {}
            summary["exits_sent"] += any(x in legs for x in EXIT_LEGS)
            summary["pending"] += t["status"] == "exit_sent" and not any(x in legs for x in EXIT_LEGS)
            summary["needs_attention"] += t["status"] == "needs_attention"
        _update_control(user_id, lambda d: d.update(kill_summary=summary))
        return dict(control(user_id, now), summary=summary)


def load_profile_safe(user_id: str) -> DeskProfile:
    from apps.api.desk.profile import load_profile

    return load_profile(user_id)

def performance(user_id: str) -> Dict[str, Any]:
    closed = [t for t in get_kite_store().list_trades(user_id, limit=1000)
              if t["status"] == "closed" and t.get("pnl_net") is not None]
    return paper.stats(closed)


def on_postback(user_id: str, data: Dict[str, Any]) -> None:
    """Order-update text message from the user's ticker: reconcile now.

    Blocking (Kite REST + the user's lock): live.py runs it on a per-hub
    worker thread, coalesced, never on the event loop. Only our tagged orders
    matter; the polling loop is the fallback."""
    if not str(data.get("tag") or "").startswith("D"):
        return
    from apps.api.desk.profile import load_profile

    reconcile_user(user_id, datetime.now(NSE.tz), load_profile(user_id))
