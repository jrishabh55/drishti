"""REST endpoints for the Zerodha Kite connection, live data and guarded execution.

Routes (all under /api, all Clerk-authed, all per user):
  GET    /kite/status              connection + session + live freshness + order gates
  PUT    /kite/app                 save api_key/api_secret (encrypted; never returned)
  DELETE /kite/app                 log out and forget the app credentials
  POST   /kite/login               a Kite login URL carrying a 5-minute state nonce
  POST   /kite/callback            {request_token, state} from the redirect → session
  POST   /kite/disconnect          Kite logout + drop today's session + stop streaming
  POST   /kite/live/start|stop     the user's private tick stream (1m/5m bars)
  GET    /kite/quote?symbols=      uncached quotes from own/directly granted source
  GET    /kite/sharing            own grants, incoming grants and data selection
  POST|DELETE /kite/sharing/grants owner-managed market-data access
  PUT    /kite/sharing/source     explicitly select an incoming connection
  POST   /kite/arm  /kite/disarm  /kite/kill
  GET    /kite/trades  POST /kite/trades (confirm a ready desk ticket live)
  POST   /kite/trades/{id}/refresh  /kite/trades/{id}/exit
  GET    /kite/performance
  GET|POST /kite/strategies  PUT|DELETE /kite/strategies/{id}
  POST   /kite/strategies/{id}/scan
  POST   /kite/strategies/{id}/pause|resume
  POST|DELETE /kite/strategies/{id}/auto-opt-in   (typed "AUTO <name>")
  POST   /kite/strategies/{id}/signals/{sig}/confirm|skip

Kite's redirect URL must point at the web app's ``/desk/connection`` page
(still signed in), which forwards ``request_token`` + ``state`` here; this
keeps the callback behind normal auth and binds it to the same user.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, ValidationError

from apps.api.auth import current_user_id
from apps.api.desk import bars as bars_mod
from apps.api.desk.profile import load_profile
from apps.api.desk.store import get_desk_store
from apps.api.kite import auth, execution, live, rest, sessions, sharing, strategies
from apps.api.kite.store import get_kite_store

router = APIRouter()

REDIRECT_PATH = "/desk/connection"
#: Test seams: a fake Kite transport / websocket connect; never set in production.
_transport: Optional[rest.Transport] = None
_connect = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _auth_http(exc: auth.KiteAuthError) -> HTTPException:
    code = 503 if exc.code == "encryption_unconfigured" else \
        409 if exc.code in ("app_missing", "not_connected", "sharing_changed") else 400
    return HTTPException(status_code=code, detail={"code": exc.code, "message": str(exc)})


def _refused(exc: execution.ExecRefused) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": "refused", "message": str(exc),
                                                  "reasons": exc.reasons})


def _kite_http(exc: rest.KiteError, user_id: str) -> HTTPException:
    if exc.token_error:
        auth.mark_revoked(user_id)
        return HTTPException(status_code=409, detail={
            "code": "session_expired", "message": "Kite rejected today's session; log in again"})
    return HTTPException(status_code=502, detail={"code": "kite_error", "message": str(exc)})


# ---------------------------------------------------------------- connection

@router.get("/kite/status")
def status(user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    now = _now()
    prof = load_profile(user_id)
    return {
        **auth.session_status(user_id, now),
        "redirect_path": REDIRECT_PATH,
        "live": live.freshness(user_id, now),
        "data_access": sharing.data_status(user_id, now),
        "control": execution.control(user_id, now),
        "cas": sessions.status(now, prof.flat_by),
        "orders": {"server_enabled": execution.orders_enabled(),
                   "owner": execution.is_owner(user_id),
                   "owner_client_id": execution.owner_binding(user_id) or None,
                   "blocked_by": execution.gate_reasons(user_id, now, prof)},
    }


class AppBody(BaseModel):
    api_key: str = Field(min_length=6, max_length=64)
    api_secret: str = Field(min_length=6, max_length=64)


@router.put("/kite/app")
def save_app(body: AppBody, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    try:
        auth.save_app(user_id, body.api_key, body.api_secret)
        get_kite_store().revoke_all_data_access(user_id)
    except auth.KiteAuthError as exc:
        raise _auth_http(exc) from exc
    return auth.session_status(user_id, _now())


@router.delete("/kite/app")
async def forget_app(user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    await live.stop(user_id)
    get_kite_store().revoke_all_data_access(user_id)
    return auth.disconnect(user_id, _now(), forget_app=True, transport=_transport)


@router.post("/kite/login")
def login(user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    try:
        return {"login_url": auth.begin_login(user_id, _now())}
    except auth.KiteAuthError as exc:
        raise _auth_http(exc) from exc


class CallbackBody(BaseModel):
    request_token: str = Field(min_length=1, max_length=128)
    state: str = Field(min_length=1, max_length=128)
    status: Optional[str] = None


@router.post("/kite/callback")
def callback(body: CallbackBody, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    if body.status and body.status != "success":
        raise HTTPException(status_code=400, detail={"code": "login_failed",
                                                      "message": f"Kite login status {body.status}"})
    try:
        return auth.complete_login(user_id, body.request_token, body.state, _now(), _transport)
    except auth.KiteAuthError as exc:
        raise _auth_http(exc) from exc


@router.post("/kite/disconnect")
async def disconnect(user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    await live.stop(user_id)
    return auth.disconnect(user_id, _now(), transport=_transport)


# ---------------------------------------------------------------- live data

class DataGrantBody(BaseModel):
    recipient_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    label: str = Field(default="", max_length=60)


class DataSourceBody(BaseModel):
    owner_id: Optional[str] = Field(default=None, min_length=1, max_length=128,
                                    pattern=r"^[A-Za-z0-9_-]+$")


@router.get("/kite/sharing")
def data_sharing(user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    return sharing.summary(user_id, _now())


@router.post("/kite/sharing/grants", status_code=201)
def grant_data(body: DataGrantBody, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    if not sharing.individual(user_id):
        raise HTTPException(409, detail="Sharing requires separate signed-in accounts.")
    if body.recipient_id == user_id or not sharing.individual(body.recipient_id):
        raise HTTPException(422, detail="Choose another signed-in Drishti account.")
    if auth.load_app(user_id) is None:
        raise HTTPException(409, detail="Save your own Kite app before sharing its data.")
    try:
        get_kite_store().grant_data_access(user_id, body.recipient_id, body.label.strip())
    except ValueError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    return sharing.summary(user_id, _now())


@router.delete("/kite/sharing/grants/{recipient_id}", status_code=204)
def revoke_data(recipient_id: str, user_id: str = Depends(current_user_id)) -> Response:
    if not get_kite_store().revoke_data_access(user_id, recipient_id):
        raise HTTPException(404, detail="Sharing grant not found.")
    return Response(status_code=204)


@router.put("/kite/sharing/source")
def select_data(body: DataSourceBody, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    if not sharing.individual(user_id):
        raise HTTPException(409, detail="Sharing requires separate signed-in accounts.")
    owner_id = None if body.owner_id == user_id else body.owner_id
    if not get_kite_store().select_data_source(user_id, owner_id):
        raise HTTPException(404, detail="Shared connection not found.")
    return sharing.summary(user_id, _now())


def _own_stream(user_id: str) -> None:
    if sharing.owner_for(user_id) != user_id:
        raise HTTPException(409, detail="The connection owner controls the shared stream. "
                                        "Select your own connection to manage your stream.")

class LiveBody(BaseModel):
    symbols: List[str] = Field(default_factory=list, max_length=live.MAX_SYMBOLS)
    universe: Optional[Literal['fno', 'nifty500']] = None


@router.post("/kite/live/start")
async def live_start(body: LiveBody, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    _own_stream(user_id)
    syms = body.symbols or get_desk_store().watchlist(user_id)
    notes = []
    if body.universe:
        if body.symbols:
            raise HTTPException(422, detail="Choose symbols or a universe, not both.")
        from apps.api.desk.orb import pick_universe
        from apps.api.scanner.store import get_scanner_store
        syms, notes = pick_universe(get_scanner_store().instruments_df(), body.universe)
    try:
        await live.start(user_id, syms, transport=_transport, connect=_connect, now=_now())
    except auth.KiteAuthError as exc:
        raise _auth_http(exc) from exc
    except rest.KiteError as exc:
        raise _kite_http(exc, user_id) from exc
    except (ValueError, bars_mod.BarsError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {**live.freshness(user_id, _now()), 'selection_notes': notes}


@router.post("/kite/live/stop")
async def live_stop(user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    # Always allow the caller to stop their OWN hub, even while reading a
    # shared feed. The selected owner's hub is never the stop target.
    await live.stop(user_id)
    return live.freshness(user_id, _now())


@router.get("/kite/quote")
def quote(symbols: str, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    owner_id = sharing.owner_for(user_id)
    try:
        syms = sorted({bars_mod.nse_symbol(s) for s in symbols.split(",") if s.strip()})[:50]
        owner_id, client = sharing.data_client(user_id, _now(), _transport)
        data = client.quote(syms)
        sharing.require_access(user_id, owner_id, _now())
    except bars_mod.BarsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except auth.KiteAuthError as exc:
        raise _auth_http(exc) from exc
    except rest.KiteError as exc:
        if owner_id != user_id:
            if exc.token_error:
                auth.mark_revoked(owner_id)
            raise HTTPException(409 if exc.token_error else 502, detail={
                "code": "shared_feed_unavailable", "message": "Shared Kite data is unavailable; "
                "the connection owner can check their session."}) from exc
        raise _kite_http(exc, owner_id) from exc
    return {"source": live.SOURCE, "quotes": {
        s: {"last_price": q.get("last_price"), "volume": q.get("volume"),
            "ohlc": q.get("ohlc"), "timestamp": q.get("timestamp"),
            "last_trade_time": q.get("last_trade_time")} for s, q in data.items()}}


# ---------------------------------------------------------------- control

class ArmBody(BaseModel):
    ack: str = Field(max_length=40)


@router.post("/kite/arm")
def arm(body: ArmBody, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    try:
        return execution.arm(user_id, _now(), body.ack, load_profile(user_id))
    except execution.ExecRefused as exc:
        raise _refused(exc) from exc


@router.post("/kite/disarm")
def disarm(user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    return execution.disarm(user_id, _now())


@router.post("/kite/kill")
def kill(user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    return execution.kill(user_id, _now(), _transport)


# ---------------------------------------------------------------- trades

class TradeBody(BaseModel):
    ticket_id: str
    confirm_text: str = Field(max_length=80)


def _confirm_words(order: Dict[str, Any]) -> str:
    return f"BUY {order['qty']} {order['symbol']}"


def _send_ticket(user_id: str, tk: Dict[str, Any], confirm_text: str, source: str
                 ) -> Dict[str, Any]:
    """Re-check desk risk now, then every live gate, then send — all under the
    user's lock, with the ticket claimed (ready → sending) first, so a double
    click or a retry can't send it twice. Marks the ticket ``sent`` after."""
    from apps.api.routes import desk as desk_routes

    with execution.user_lock(user_id):
        store = get_desk_store()
        tk = store.get_ticket(tk["id"], user_id) or tk          # fresh, under the lock
        if tk["status"] != "ready":
            raise HTTPException(status_code=409, detail=f"ticket is {tk['status']}")
        want = _confirm_words(tk["order"])
        if confirm_text.strip().upper() != want:
            raise HTTPException(status_code=422, detail=f'type exactly "{want}" to send this order')
        now = _now()
        prof = load_profile(user_id)
        reasons = execution.gate_reasons(user_id, now, prof)
        if reasons:
            raise _refused(execution.ExecRefused(reasons))
        verdict = desk_routes._risk_for([tk["order"]], user_id)["results"][0]
        if verdict["fingerprint"] != tk["fingerprint"]:
            raise HTTPException(status_code=409, detail="the order changed since it was checked: "
                                                        "make a new ticket")
        if verdict["verdict"] != "PASS":
            raise _refused(execution.ExecRefused(verdict["reasons"]))
        store.update_ticket(tk["id"], user_id, dict(tk, status="sending"))
        try:
            trade = execution.send_entry(user_id, tk, now, prof,
                                         lambda: auth.client_for(user_id, now, _transport),
                                         source=source)
        except (execution.ExecRefused, auth.KiteAuthError) as exc:
            store.update_ticket(tk["id"], user_id, tk)      # nothing left the server: ready again
            if isinstance(exc, execution.ExecRefused):
                raise _refused(exc) from exc
            raise _auth_http(exc) from exc
        if trade.get("rejected_by_broker"):
            # Kite definitively refused it (4xx): nothing is live, and it isn't
            # an entry today; the ticket says so instead of "sent".
            tk.update(status="rejected", broker="kite", kite_trade_id=trade["id"],
                      verdict=verdict, rejected_reason=trade["rejected_by_broker"],
                      confirmed_at=now.isoformat())
        else:
            tk.update(status="sent", broker="kite", kite_trade_id=trade["id"], verdict=verdict,
                      sent_at=now.astimezone(execution.NSE.tz).isoformat(),
                      confirmed_at=now.isoformat())
        store.update_ticket(tk["id"], user_id, tk)
        return trade


@router.get("/kite/trades")
def list_trades(user_id: str = Depends(current_user_id)) -> List[Dict[str, Any]]:
    return get_kite_store().list_trades(user_id)


@router.post("/kite/trades", status_code=201)
def send_trade(body: TradeBody, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    tk = get_desk_store().get_ticket(body.ticket_id, user_id)
    if tk is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    return _send_ticket(user_id, tk, body.confirm_text, "ticket")


def _own_trade(tid: str, user_id: str) -> Dict[str, Any]:
    t = get_kite_store().get_trade(tid, user_id)
    if t is None:
        raise HTTPException(status_code=404, detail="trade not found")
    return t


@router.post("/kite/trades/{tid}/refresh")
def refresh_trade(tid: str, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    _own_trade(tid, user_id)
    execution.reconcile_user(user_id, _now(), load_profile(user_id), _transport)
    return _own_trade(tid, user_id)


@router.post("/kite/trades/{tid}/exit")
def exit_trade(tid: str, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    _own_trade(tid, user_id)
    try:
        return execution.exit_trade(user_id, tid, _now(), load_profile(user_id), _transport)
    except execution.ExecRefused as exc:
        raise _refused(exc) from exc
    except auth.KiteAuthError as exc:
        raise _auth_http(exc) from exc
    except rest.KiteError as exc:
        raise _kite_http(exc, user_id) from exc


@router.get("/kite/performance")
def performance(user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    return execution.performance(user_id)


# ---------------------------------------------------------------- strategies

def _valid(body: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    from apps.api.desk import portfolios as pf_mod

    try:
        doc = strategies.Strategy.model_validate(body).doc()
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=[
            {"loc": list(e.get("loc", ())), "msg": e.get("msg", "")} for e in exc.errors()[:5]]
        ) from exc
    if pf_mod.get(user_id, doc["portfolio_id"]) is None:
        raise HTTPException(status_code=422, detail="portfolio not found")
    if doc["scanner_id"]:
        from apps.api.scanner.store import get_scanner_store

        if not strategies.scanner_ok(get_scanner_store().get_scanner(doc["scanner_id"]), user_id):
            raise HTTPException(status_code=422, detail="scanner not found")
    return doc


def _killed_409(user_id: str) -> None:
    if execution.control(user_id, _now())["killed"]:
        raise HTTPException(status_code=409, detail="the kill switch was used today; "
                                                    "strategies stay off until tomorrow")


def _own_strategy(sid: str, user_id: str) -> Dict[str, Any]:
    s = get_kite_store().get_strategy(sid, user_id)
    if s is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    return s


@router.get("/kite/strategies")
def list_strategies(user_id: str = Depends(current_user_id)) -> List[Dict[str, Any]]:
    return get_kite_store().list_strategies(user_id)


@router.post("/kite/strategies", status_code=201)
def create_strategy(body: Dict[str, Any], user_id: str = Depends(current_user_id)
                    ) -> Dict[str, Any]:
    """Modes default to off; auto never sends until its separate opt-in."""
    doc = dict(_valid(body, user_id), signals=[], version=1)
    with execution.user_lock(user_id):
        if doc["mode"] != "off":
            _killed_409(user_id)
        return get_kite_store().put_strategy(user_id, doc)


@router.put("/kite/strategies/{sid}")
def update_strategy(sid: str, body: Dict[str, Any], user_id: str = Depends(current_user_id)
                    ) -> Dict[str, Any]:
    """Every edit is a new version; it drops any auto opt-in."""
    valid = _valid(body, user_id)
    with execution.user_lock(user_id):
        old = _own_strategy(sid, user_id)
        if valid["mode"] != "off":
            _killed_409(user_id)
        doc = dict(valid, id=sid, signals=old.get("signals") or [],
                   version=strategies.version_of(old) + 1, last_scan=old.get("last_scan"),
                   paused_from=old.get("paused_from") if valid["mode"] == "off" else None)
        return get_kite_store().put_strategy(user_id, doc)


@router.delete("/kite/strategies/{sid}", status_code=204)
def delete_strategy(sid: str, user_id: str = Depends(current_user_id)) -> Response:
    with execution.user_lock(user_id):
        gone = get_kite_store().delete_strategy(sid, user_id)
    if not gone:
        raise HTTPException(status_code=404, detail="strategy not found")
    return Response(status_code=204)


@router.post("/kite/strategies/{sid}/scan")
def scan_strategy(sid: str, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    s = _own_strategy(sid, user_id)
    new = strategies.scan(user_id, s, _now())
    return {"new": new, "strategy": _own_strategy(sid, user_id)}


@router.post("/kite/strategies/{sid}/pause")
def pause_strategy(sid: str, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    """mode → off, remembering ``paused_from`` (the Telegram contract); not a
    new version. A running scan sees the mode change and stops acting."""
    with execution.user_lock(user_id):
        s = _own_strategy(sid, user_id)
        if s.get("mode") == "off":
            return s
        return get_kite_store().put_strategy(user_id, dict(s, mode="off", paused_from=s["mode"]))


class ResumeBody(BaseModel):
    mode: Literal["paper", "confirm"] = "paper"


@router.post("/kite/strategies/{sid}/resume")
def resume_strategy(sid: str, body: Optional[ResumeBody] = None,
                    user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    """Resume to Paper (Confirm only when asked, Default only). Never Auto,
    never arms, never overrides today's kill, and clears any auto opt-in."""
    mode = (body or ResumeBody()).mode
    with execution.user_lock(user_id):
        s = _own_strategy(sid, user_id)
        if s.get("mode") != "off" or not s.get("paused_from"):
            raise HTTPException(status_code=409, detail="the strategy isn't paused")
        _killed_409(user_id)
        doc = dict(s, mode=mode, paused_from=None, auto_opt_in=None)
        _valid({k: doc.get(k) for k in strategies.Strategy.model_fields}, user_id)
        return get_kite_store().put_strategy(user_id, doc)


class AutoOptInBody(BaseModel):
    ack: str = Field(max_length=120)


@router.post("/kite/strategies/{sid}/auto-opt-in")
def auto_opt_in(sid: str, body: AutoOptInBody, user_id: str = Depends(current_user_id)
                ) -> Dict[str, Any]:
    """The separate, typed opt-in for unattended live sends of THIS version.
    It authorizes nothing else: every send still needs today's arm, the
    owner binding, the server's live-order switch and a passing risk check."""
    with execution.user_lock(user_id):
        s = _own_strategy(sid, user_id)
        if s.get("mode") != "auto":
            raise HTTPException(status_code=409, detail="switch the strategy to auto mode first")
        _killed_409(user_id)
        want = f"AUTO {s['name']}"
        if body.ack.strip() != want:
            raise HTTPException(status_code=422, detail=f'type exactly "{want}" to opt in')
        return get_kite_store().put_strategy(user_id, dict(s, auto_opt_in={
            "version": strategies.version_of(s), "at": _now().isoformat()}))


@router.delete("/kite/strategies/{sid}/auto-opt-in")
def auto_opt_out(sid: str, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    with execution.user_lock(user_id):
        s = _own_strategy(sid, user_id)
        return get_kite_store().put_strategy(user_id, dict(s, auto_opt_in=None))


def _own_signal(s: Dict[str, Any], sig_id: str) -> Dict[str, Any]:
    for g in s.get("signals") or []:
        if g["id"] == sig_id:
            return g
    raise HTTPException(status_code=404, detail="signal not found")


class SignalConfirmBody(BaseModel):
    confirm_text: str = Field(max_length=80)


@router.post("/kite/strategies/{sid}/signals/{sig_id}/confirm")
def confirm_signal(sid: str, sig_id: str, body: SignalConfirmBody,
                   user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    store = get_kite_store()
    with execution.user_lock(user_id):
        s = _own_strategy(sid, user_id)
        if strategies.expire_signals(user_id, s, _now()):
            store.put_strategy(user_id, s)
        sig = _own_signal(s, sig_id)
        if sig.get("status") != "await_confirm":
            raise HTTPException(status_code=409, detail=f"signal is {sig.get('status')}")
        tk = get_desk_store().get_ticket(sig["ticket_id"], user_id)
        if tk is None:
            raise HTTPException(status_code=404, detail="ticket not found")
        trade = _send_ticket(user_id, tk, body.confirm_text, f"strategy:{sid}")
        sig.update(status="sent", kite_trade_id=trade["id"])
        store.put_strategy(user_id, s)
        return trade


@router.post("/kite/strategies/{sid}/signals/{sig_id}/skip")
def skip_signal(sid: str, sig_id: str, user_id: str = Depends(current_user_id)
                ) -> Dict[str, Any]:
    from apps.api.routes import desk as desk_routes

    with execution.user_lock(user_id):
        s = _own_strategy(sid, user_id)
        sig = _own_signal(s, sig_id)
        if sig.get("status") != "await_confirm":
            raise HTTPException(status_code=409, detail=f"signal is {sig.get('status')}")
        sig["status"] = "skipped"
        try:
            desk_routes.cancel_ticket(sig["ticket_id"], user_id)
        except HTTPException:
            pass
        get_kite_store().put_strategy(user_id, s)
        return sig
