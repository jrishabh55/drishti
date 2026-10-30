"""REST endpoints for the Trade Desk (day-trader-skill port).

Routes (all under /api, all Clerk-authed; per-user rows 404 when not yours):
  GET  /desk/profile                 risk limits + session times (defaults if unset)
  PUT  /desk/profile
  GET  /desk/watchlist  POST /desk/watchlist  DELETE /desk/watchlist/{symbol}
  POST /desk/timemachine             intraday or swing replays (+ history, verdict)
  GET  /desk/orb                     opening-range breakouts so far today
  GET  /desk/entry/{symbol}          waiting | triggered | expired
  POST /desk/exit-plan               stop/target/flat-by for an entry (no save)
  GET|POST /desk/plans  GET|PUT|DELETE /desk/plans/{id}
  POST /desk/size                    position size + charges for a plan
  POST /desk/risk                    PASS / BLOCK per order (no save)
  GET|POST /desk/tickets  POST /desk/tickets/{id}/confirm  POST /desk/tickets/{id}/cancel
  GET  /desk/paper                   paper ledger + stats   POST /desk/paper/{id}/close
  GET|POST /desk/portfolios  PATCH /desk/portfolios/{id}   named paper portfolios
  GET|POST /desk/alerts  PUT|DELETE /desk/alerts/{id}  GET /desk/alerts/events

Sizing, risk, tickets and the ledger take an optional ``portfolio_id``
(omitted = ``default``, the original ledger). A ticket is bound to its
portfolio for life; confirming re-checks risk against that portfolio only.

Every number a desk endpoint returns is arithmetic over public, delayed
bars — not advice. Responses carry ``source`` / ``data_as_of`` so the UI can
say "DELAYED" honestly.
"""
from __future__ import annotations

import math
from dataclasses import replace

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from apps.api.auth import current_user_id
from apps.api.desk import bars as bars_mod, bars_source, portfolios as pf_mod, timemachine as tm
from apps.api.desk.market.session import NSE as NSE_SPEC
from apps.api.desk.profile import DeskProfile, load_profile
from apps.api.desk.store import get_desk_store


class SafeErrorsRoute(APIRoute):
    """422s that never echo the rejected input.

    FastAPI's default body repeats each bad value, and a NaN/Infinity cannot be
    JSON-encoded, so refusing one would surface as a 500 instead of a 422.
    """
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def route(request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                return JSONResponse(status_code=422, content={"detail": [
                    {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
                    for e in exc.errors()]})
        return route


router = APIRouter(route_class=SafeErrorsRoute)


def _now() -> datetime:
    """Seam for tests."""
    return datetime.now(timezone.utc)


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


def _invalid(exc: ValidationError) -> HTTPException:
    """Pydantic errors without their (unserialisable) exception objects."""
    return HTTPException(status_code=422, detail=[
        {"loc": list(e.get("loc", ())), "msg": e.get("msg", "")} for e in exc.errors()[:5]])


# ---------------------------------------------------------------- profile

@router.get("/desk/profile", response_model=DeskProfile)
def get_profile(user_id: str = Depends(current_user_id)) -> DeskProfile:
    return load_profile(user_id)


@router.put("/desk/profile", response_model=DeskProfile)
def put_profile(body: Dict[str, Any], user_id: str = Depends(current_user_id)) -> DeskProfile:
    try:
        prof = DeskProfile(**body)
    except ValidationError as exc:
        raise _invalid(exc) from exc
    get_desk_store().put_profile(user_id, prof.model_dump())
    return prof


# ---------------------------------------------------------------- watchlist

class WatchBody(BaseModel):
    symbol: str


@router.get("/desk/watchlist")
def get_watchlist(user_id: str = Depends(current_user_id)) -> List[str]:
    return get_desk_store().watchlist(user_id)


@router.post("/desk/watchlist", status_code=201)
def add_watch(body: WatchBody, user_id: str = Depends(current_user_id)) -> List[str]:
    try:
        sym = bars_mod.nse_symbol(body.symbol)
    except bars_mod.BarsError as exc:
        raise _unprocessable(exc) from exc
    store = get_desk_store()
    if len(store.watchlist(user_id)) >= 200:
        raise HTTPException(status_code=422, detail="the watchlist holds at most 200 symbols")
    store.add_watch(user_id, sym)
    return store.watchlist(user_id)


@router.delete("/desk/watchlist/{symbol}", status_code=204)
def remove_watch(symbol: str, user_id: str = Depends(current_user_id)) -> Response:
    try:
        sym = bars_mod.nse_symbol(symbol)
    except bars_mod.BarsError as exc:
        raise _unprocessable(exc) from exc
    get_desk_store().remove_watch(user_id, sym)
    return Response(status_code=204)


# ---------------------------------------------------------------- time machine

class TimeMachineBody(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    mode: Literal["intraday", "swing"] = "intraday"
    interval: Literal["1m", "5m"] = "5m"
    symbol: str
    date: str
    side: Literal["buy", "sell"] = "buy"
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    target: float = Field(gt=0)
    # intraday
    entry_time: Optional[str] = None
    entry_bar: Optional[int] = None
    setup: str = "orb"
    or_minutes: int = 15
    stop_rule: str = "auto"
    until: Optional[str] = None
    window: int = Field(2, ge=0, le=10)
    # swing
    horizon: int = Field(10, ge=1, le=60)
    # both
    replays: int = Field(10_000, ge=100, le=50_000)
    seed: int = 19
    circuit_pct: Optional[float] = Field(None, gt=0, le=20)


@router.post("/desk/timemachine")
def run_timemachine(body: TimeMachineBody, user_id: str = Depends(current_user_id)
                    ) -> Dict[str, Any]:
    """Replay a plan 10,000 times + its real history. Cached per data snapshot."""
    store = get_desk_store()
    now = _now()
    try:
        if body.mode == "intraday":
            spec = replace(NSE_SPEC, bar_min=1 if body.interval == '1m' else 5)
            plan = tm.normalize_intraday_plan(body.model_dump(), spec=spec)
            plan['interval'] = body.interval
            plan["symbol"] = bars_mod.nse_symbol(plan["symbol"])
            hist = bars_source.get_history_rows(user_id, plan["symbol"], body.interval, now=now)
            today = bars_source.get_session_rows(user_id, plan["symbol"], body.interval)
            until = body.until or load_profile(user_id).flat_by
            key = tm.plan_key("intraday", plan, hist.data_as_of, today=today.data_as_of,
                              until=until, replays=body.replays, seed=body.seed,
                              window=body.window, circuit=body.circuit_pct)
            cached = store.get_tm(user_id, key)
            if cached:
                return cached
            out = tm.run_intraday(hist.rows + today.rows, plan, now=now, spec=spec,
                                  replays=body.replays, seed=body.seed, window=body.window,
                                  until=until, circuit_pct=body.circuit_pct,
                                  source=hist.source)
        else:
            hist = bars_source.get_history_rows(user_id, body.symbol, "1d", now=now, yahoo=True)
            raw = body.model_dump()
            raw["symbol"] = hist.symbol
            key = tm.plan_key("swing", raw, hist.data_as_of)
            cached = store.get_tm(user_id, key)
            if cached:
                return cached
            out = tm.run_swing(hist.rows, raw, horizon=body.horizon, now=now,
                               replays=body.replays, seed=body.seed,
                               circuit_pct=body.circuit_pct, source=hist.source)
    except (tm.TMError, bars_mod.BarsError) as exc:
        raise _unprocessable(exc) from exc
    out["key"] = key
    out["data_as_of"] = hist.data_as_of
    store.put_tm(user_id, key, out)
    return out


# ---------------------------------------------------------------- ORB scan

@router.get("/desk/orb")
def orb_scan(or_minutes: Optional[int] = None, at: Optional[str] = None,
             date: Optional[str] = None, user_id: str = Depends(current_user_id)
             ) -> Dict[str, Any]:
    """Opening-range breakouts so far today (or a replay of ``date``)."""
    from datetime import date as _date

    from apps.api.desk import orb
    from apps.api.scanner.store import get_scanner_store

    prof = load_profile(user_id)
    scan_store = get_scanner_store()
    inst = scan_store.instruments_df()
    symbols, notes = orb.pick_universe(inst, prof.universe)
    if prof.universe == "fno":
        from apps.api.scanner.universe import fo_membership_source
        membership = fo_membership_source(scan_store)
        if membership and membership.get("stale"):
            notes.append(f"F&O universe uses the last available list from {membership.get('as_of', 'unknown date')}; the live list is unavailable.")
    try:
        day = _date.fromisoformat(date) if date else None
    except ValueError as exc:
        raise _unprocessable(ValueError("date wants YYYY-MM-DD")) from exc
    from apps.api.kite import live, sharing
    now = _now()
    owner = sharing.owner_for(user_id)
    feed = bars_source.get_session_universe(user_id, "5m", day=day or now.astimezone(NSE_SPEC.tz).date(), symbols=symbols, now=now)
    daily = scan_store.load_bars("1d", limit=21)
    names = inst["name"].to_dict() if not inst.empty else {}
    try:
        docs = []
        for source in ("live:kite", "delayed:yahoo"):
            selected = [s for s in symbols if feed.sources.get(s, "delayed:yahoo") == source]
            if not selected:
                continue
            docs.append(orb.scan({s: feed.rows[s] for s in selected if s in feed.rows}, now,
                                 or_minutes=or_minutes or prof.or_minutes,
                                 no_entries_after=prof.no_entries_after, at=at, day=day,
                                 symbols=selected, min_price=prof.min_price,
                                 min_avg_value_cr=prof.min_avg_value_cr,
                                 avg_value_cr=orb._avg_value_cr(daily, symbols), names=names, source=source))
        if not docs:
            docs = [orb.scan({}, now, symbols=[], at=at, day=day,
                             or_minutes=or_minutes or prof.or_minutes)]
        doc = docs[0]
        for part in docs[1:]:
            doc["counts"] = {k: v + part["counts"][k] for k, v in doc["counts"].items()}
            doc["hits"] += part["hits"]
            doc["notes"] = list(dict.fromkeys(doc["notes"] + part["notes"]))
            times = {}
            for row in doc["coverage"]["missing_times"] + part["coverage"]["missing_times"]:
                times[row["start"]] = times.get(row["start"], 0) + row["charts"]
            doc["coverage"] = {"complete": doc["coverage"]["complete"] + part["coverage"]["complete"],
                               "incomplete": doc["coverage"]["incomplete"] + part["coverage"]["incomplete"],
                               "missing_times": [{"start": k, "charts": v} for k, v in sorted(times.items())]}
            doc["data_as_of"] = max(filter(None, [doc["data_as_of"], part["data_as_of"]]), default=None)
        doc["hits"].sort(key=lambda h: (h["at"], h["symbol"]))
        for hit in doc["hits"]:
            hit["source"] = feed.sources.get(hit["symbol"], "delayed:yahoo")
        if feed.coverage['live'] and not live.access_valid(user_id, owner, _now()):
            raise HTTPException(409, detail="Market data access changed during scan; refresh and retry.")
        doc.update(source=feed.source, source_coverage=feed.coverage, sources=feed.sources)
        doc["notes"].append("Source coverage is per symbol. Liquidity uses public closed daily history. Last evaluated is a completed candle, not a current quote.")
    except (orb.ORBError, ValueError) as exc:
        raise _unprocessable(exc) from exc
    doc["notes"] = notes + doc["notes"]
    doc["universe"] = prof.universe
    return doc


# ---------------------------------------------------------------- entry timer

@router.get("/desk/entry/{symbol}")
def entry_status(symbol: str, interval: Literal["1m", "5m"] = "5m", setup: Literal["orb", "level"] = "orb",
                 level: Optional[float] = Query(None, gt=0, allow_inf_nan=False),
                 or_minutes: Optional[int] = None,
                 min_volume_x: Optional[float] = Query(None, gt=0, le=100, allow_inf_nan=False),
                 user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    from apps.api.desk import entry as entry_mod

    prof = load_profile(user_id)
    try:
        spec = replace(NSE_SPEC, bar_min=1 if interval == '1m' else 5)
        series = bars_source.get_session_rows(user_id, symbol, interval, now=_now())
        out = entry_mod.evaluate(series.rows, _now(), symbol=series.symbol, setup=setup,
                                 or_minutes=or_minutes or prof.or_minutes, level=level,
                                 min_volume_x=min_volume_x,
                                 no_entries_before=prof.no_entries_before,
                                 no_entries_after=prof.no_entries_after, source=series.source, spec=spec)
    except (entry_mod.EntryError, bars_mod.BarsError) as exc:
        raise _unprocessable(exc) from exc
    out["data_as_of"] = series.data_as_of
    out["notes"] = series.notes
    out["interval"] = interval
    if out["status"] == "no-data" and series.notes:
        out["reason"] = " ".join(series.notes)
    return out


# ---------------------------------------------------------------- exit planner + plans

class PlanBody(BaseModel):
    """Everything optional except the symbol: missing entry facts are filled
    from the entry timer (it must have triggered) — upstream's
    ``exit.py plan`` reading trader/entry.json."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    symbol: str
    interval: Literal["1m", "5m"] = "5m"
    date: Optional[str] = None
    setup: Literal["orb", "level"] = "orb"
    level: Optional[float] = None
    entry: Optional[float] = Field(None, gt=0)
    entry_time: Optional[str] = None
    or_high: Optional[float] = None
    or_low: Optional[float] = None
    or_minutes: Optional[int] = None
    stop_rule: Optional[Literal["or_mid", "or_low", "price", "pct", "atr"]] = None
    stop_param: Optional[float] = None
    target_rule: Literal["r", "price"] = "r"
    target_param: float = 2.0
    flat_by: Optional[str] = None
    notes: str = ""


def _build_plan(body: PlanBody, user_id: str) -> Dict[str, Any]:
    from apps.api.desk import entry as entry_mod, exit_plan
    from apps.api.desk.bars import session_bars

    spec = replace(NSE_SPEC, bar_min=1 if body.interval == '1m' else 5)
    prof = load_profile(user_id)
    now = _now()
    sym = bars_mod.nse_symbol(body.symbol)
    or_minutes = body.or_minutes or prof.or_minutes
    entry, entry_time = body.entry, body.entry_time
    or_high, or_low = body.or_high, body.or_low
    day = body.date or bars_mod.today_ist(now).isoformat()
    series = None
    need_bars = entry is None or body.stop_rule == "atr" or \
        (body.setup == "orb" and (or_high is None or or_low is None))
    source = None
    if need_bars:
        series = bars_source.get_session_rows(user_id, sym, body.interval)
        source = series.source
        ev = entry_mod.evaluate(series.rows, now, symbol=sym, setup=body.setup,
                                or_minutes=or_minutes, level=body.level,
                                no_entries_before=prof.no_entries_before,
                                no_entries_after=prof.no_entries_after, source=series.source, spec=spec)
        orr = ev.get("opening_range") or {}
        if or_high is None and orr:
            or_high, or_low = orr["high"], orr["low"]
        if entry is None:
            if ev["status"] != "triggered":
                raise exit_plan.PlanError(
                    f"{sym} is {ev['status']} ({ev['reason']}): plan once it triggers, or give "
                    "an entry price to plan ahead")
            entry, entry_time = ev["trigger"]["price"], ev["trigger"]["time"]
            day = ev["date"]
    bars_to_entry = None
    if body.stop_rule == "atr":
        bars = [b for b in session_bars(series.rows if series else [], spec, now)[0]
                if b["date"] == day]
        if entry_time:
            from apps.api.desk.market.session import parse_clock

            k = spec.bar_for_close(parse_clock(entry_time))
            bars = [b for b in bars if b["slot"] <= k]
        bars_to_entry = bars
    plan = exit_plan.build_plan(
        symbol=sym, date=day, entry=float(entry), setup=body.setup, entry_time=entry_time,
        or_high=or_high, or_low=or_low, or_minutes=or_minutes, stop_rule=body.stop_rule,
        stop_param=body.stop_param, target_rule=body.target_rule,
        target_param=body.target_param, flat_by=body.flat_by or prof.flat_by,
        broker_square_off=prof.broker_square_off, min_reward_risk=prof.min_reward_risk,
        session_bars_to_entry=bars_to_entry, spec=spec)
    plan["interval"] = body.interval
    plan["notes"] = body.notes
    plan["level"] = body.level
    plan["source"] = source
    return plan


def _plan_or_422(body: PlanBody, user_id: str) -> Dict[str, Any]:
    from apps.api.desk import entry as entry_mod, exit_plan

    try:
        return _build_plan(body, user_id)
    except (exit_plan.PlanError, entry_mod.EntryError, bars_mod.BarsError, ValueError) as exc:
        raise _unprocessable(exc) from exc


@router.post("/desk/exit-plan")
def preview_exit_plan(body: PlanBody, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    """Compute a plan without saving it (the plan editor's live preview)."""
    return _plan_or_422(body, user_id)


@router.get("/desk/plans")
def list_plans(user_id: str = Depends(current_user_id)) -> List[Dict[str, Any]]:
    return get_desk_store().list_plans(user_id)


@router.post("/desk/plans", status_code=201)
def create_plan(body: PlanBody, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    return _create_plan(body, user_id)


def _create_plan(body: PlanBody, user_id: str,
                 stamp: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """``stamp``: server-side strategy attribution (never accepted from a request)."""
    plan = _plan_or_422(body, user_id)
    plan["request"] = body.model_dump()
    plan.update(stamp or {})
    return get_desk_store().create_plan(user_id, plan)


def _own_plan(pid: str, user_id: str) -> Dict[str, Any]:
    plan = get_desk_store().get_plan(pid, user_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")
    return plan


@router.get("/desk/plans/{pid}")
def get_plan(pid: str, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    return _own_plan(pid, user_id)


@router.put("/desk/plans/{pid}")
def update_plan(pid: str, body: PlanBody, user_id: str = Depends(current_user_id)
                ) -> Dict[str, Any]:
    old = _own_plan(pid, user_id)
    if old.get("status") not in (None, "draft"):
        raise HTTPException(status_code=409,
                            detail=f"plan is {old['status']}; only draft plans can be edited")
    plan = _plan_or_422(body, user_id)
    plan["request"] = body.model_dump()
    plan["tm_key"] = None
    get_desk_store().update_plan(pid, user_id, plan)
    return _own_plan(pid, user_id)


@router.delete("/desk/plans/{pid}", status_code=204)
def delete_plan(pid: str, user_id: str = Depends(current_user_id)) -> Response:
    _own_plan(pid, user_id)
    get_desk_store().delete_plan(pid, user_id)
    return Response(status_code=204)


# ---------------------------------------------------------------- account state

def _today_ist() -> str:
    return bars_mod.today_ist(_now()).isoformat()


def _portfolio(user_id: str, portfolio_id: str) -> Dict[str, Any]:
    try:
        return pf_mod.require(user_id, portfolio_id)
    except pf_mod.PortfolioError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


PortfolioId = Field(pf_mod.DEFAULT, pattern=pf_mod.ID_PATTERN)


def _account(user_id: str, portfolio_id: str = pf_mod.DEFAULT) -> Dict[str, Any]:
    """Cash, holdings and today's entries from one portfolio's paper ledger
    (+ broker-mode tickets for ``default``, the only broker-facing book) — the
    state the risk check simulates against."""
    store = get_desk_store()
    _portfolio(user_id, portfolio_id)
    prof = pf_mod.profile_for(user_id, portfolio_id)
    today = _today_ist()
    trades = store.list_paper(user_id, limit=1000, portfolio_id=portfolio_id)
    holdings: Dict[str, float] = {}
    values: Dict[str, float] = {}
    realized = 0.0
    from apps.api.desk import paper

    for t in trades:
        # Partial (scale-out) exits release their cash and holdings here.
        acc = paper.accounting(t, prof)
        realized += acc["realized"]
        if acc["open_qty"]:
            holdings[t["symbol"]] = holdings.get(t["symbol"], 0) + acc["open_qty"]
        if acc["committed"]:
            values[t["symbol"]] = values.get(t["symbol"], 0.0) + acc["committed"]
    entries = [{"symbol": t["symbol"], "side": "buy",
                "max_loss": (t.get("risk_per_share") or 0) * t["qty"]}
               for t in trades if t["trade_date"] == today and t["status"] != "cancelled"]
    broker_today = [tk for tk in store.list_tickets(user_id, limit=500,
                                                    portfolio_id=pf_mod.DEFAULT)
                    if tk.get("status") == "sent" and (tk.get("sent_at") or "")[:10] == today
                    ] if portfolio_id == pf_mod.DEFAULT else []
    for tk in broker_today:
        o = tk["order"]
        entries.append({"symbol": o["symbol"], "side": o["side"],
                        "max_loss": (o.get("plan") or {}).get("max_loss") or 0})
    cash = prof.capital + realized - sum(values.values())
    return {"profile": prof, "cash": round(cash, 2), "holdings": holdings, "values": values,
            "entries_today": entries, "orders_today": len(entries), "realized": round(realized, 2)}


def _fresh_price(user_id: str, symbol: str) -> Optional[float]:
    """Last completed 5m close today, if any (delayed) — upstream's fresh price."""
    try:
        series = bars_source.get_session_rows(user_id, symbol, "5m", now=_now())
    except bars_mod.BarsError:
        return None
    bars, _ = bars_source.session_bars_for(series, _now())
    today = _today_ist()
    todays = [b for b in bars if b["date"] == today]
    return todays[-1]["c"] if todays else None


def _market_facts(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    from apps.api.desk import feeds

    return feeds.market_facts(symbols)


def _risk_for(orders: List[Dict[str, Any]], user_id: str,
              portfolio_id: str = pf_mod.DEFAULT) -> Dict[str, Any]:
    """Risk-check ``orders`` against ONE portfolio's account. An order bound
    to a different portfolio always BLOCKs: callers that don't name one (the
    live broker path) check ``default`` and so can never pass a named
    portfolio's paper ticket."""
    from apps.api.desk import risk

    acct = _account(user_id, portfolio_id)
    syms = sorted({str(o.get("symbol", "")).upper() for o in orders if o.get("symbol")})
    prices = {s: p for s in syms if (p := _fresh_price(user_id, s)) is not None}
    out = risk.check(orders, acct["profile"], now=_now(), cash=acct["cash"],
                     holdings=acct["holdings"], holding_values=acct["values"],
                     today_entries=acct["entries_today"], today_orders=acct["orders_today"],
                     prices=prices, market=_market_facts(syms))
    for o, res in zip(orders, out["results"]):
        bound = o.get("portfolio_id") or pf_mod.DEFAULT
        if bound != portfolio_id:
            res["verdict"] = "BLOCK"
            res["reasons"] = list(res["reasons"]) + [
                f"portfolio: this order is bound to paper portfolio {bound!r}, not {portfolio_id!r}"]
    out["pass"] = sum(r["verdict"] == "PASS" for r in out["results"])
    out["block"] = sum(r["verdict"] == "BLOCK" for r in out["results"])
    return out


def _risk_bound(orders: List[Dict[str, Any]], user_id: str, portfolio_id: str) -> Dict[str, Any]:
    # ``default`` keeps the legacy two-argument call that existing callers patch.
    if portfolio_id == pf_mod.DEFAULT:
        return _risk_for(orders, user_id)
    return _risk_for(orders, user_id, portfolio_id)


# ---------------------------------------------------------------- sizing, risk, tickets

class SizeBody(BaseModel):
    plan_id: str
    order_type: Literal["LIMIT", "MARKET"] = "LIMIT"
    portfolio_id: str = PortfolioId


def _size(plan: Dict[str, Any], body: SizeBody, user_id: str) -> Dict[str, Any]:
    from apps.api.desk import sizing

    acct = _account(user_id, body.portfolio_id)
    price = _fresh_price(user_id, plan["symbol"]) if body.order_type == "MARKET" else None
    try:
        return sizing.size_trade(plan, acct["profile"], cash=acct["cash"], price=price,
                                 order_type=body.order_type, today=_today_ist())
    except sizing.SizeError as exc:
        raise _unprocessable(exc) from exc


@router.post("/desk/size")
def size_plan(body: SizeBody, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    plan = _own_plan(body.plan_id, user_id)
    return dict(_size(plan, body, user_id), portfolio_id=body.portfolio_id)


class RiskBody(BaseModel):
    orders: List[Dict[str, Any]] = Field(min_length=1, max_length=20)
    portfolio_id: str = PortfolioId

    @field_validator("orders")
    @classmethod
    def _finite(cls, orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        for o in orders:
            for k, v in o.items():
                if isinstance(v, float) and not math.isfinite(v):
                    raise ValueError(f"order {k} must be a finite number")
        return orders


@router.post("/desk/risk")
def check_risk(body: RiskBody, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    return _risk_bound(body.orders, user_id, body.portfolio_id)


@router.get("/desk/tickets")
def list_tickets(portfolio_id: Optional[str] = Query(None, pattern=pf_mod.ID_PATTERN),
                 user_id: str = Depends(current_user_id)) -> List[Dict[str, Any]]:
    """Every ticket (each carries its bound ``portfolio_id``), or one portfolio's."""
    if portfolio_id is not None:
        _portfolio(user_id, portfolio_id)
    return get_desk_store().list_tickets(user_id, portfolio_id=portfolio_id)


@router.post("/desk/tickets", status_code=201)
def create_ticket(body: SizeBody, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    return _create_ticket(body, user_id)


def _create_ticket(body: SizeBody, user_id: str,
                   stamp: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """``stamp``: server-side strategy attribution/exit policy, copied onto the
    ticket and its order (outside the fingerprint) so paper and live trades
    carry it."""
    from apps.api.desk import sizing, ticket

    plan = _own_plan(body.plan_id, user_id)
    pid = body.portfolio_id
    s = _size(plan, body, user_id)
    order = sizing.order_from(s, plan_id=plan["id"])
    if order and pid != pf_mod.DEFAULT:
        order["portfolio_id"] = pid      # part of the fingerprint
    if order and stamp:
        order.update(stamp)
    verdict = _risk_bound([order], user_id, pid) if order else None
    doc = ticket.build(plan=plan, sizing=s, order=order, verdict=verdict)
    doc["portfolio_id"] = pid
    doc.update({k: v for k, v in (stamp or {}).items() if k != "exit_policy"})
    return get_desk_store().create_ticket(user_id, doc)


def _own_ticket(tid: str, user_id: str) -> Dict[str, Any]:
    tk = get_desk_store().get_ticket(tid, user_id)
    if tk is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    return tk


class ConfirmBody(BaseModel):
    mode: Literal["paper", "broker"]
    # The portfolio the user saw on the ticket; a mismatch refuses (never moves it).
    portfolio_id: Optional[str] = Field(None, pattern=pf_mod.ID_PATTERN)


def _user_lock(user_id: str):
    """The same per-user lock the live Kite engine holds while it claims and
    sends a ticket, so a paper confirm, a live send and a cancel of one ticket
    are serialised (and each paper risk check sees the previous one's cash)."""
    from apps.api.kite.execution import user_lock

    return user_lock(user_id)


@router.post("/desk/tickets/{tid}/confirm")
def confirm_ticket(tid: str, body: ConfirmBody, user_id: str = Depends(current_user_id)
                   ) -> Dict[str, Any]:
    """The user's explicit yes for THIS ticket. Re-checks risk right now."""
    from apps.api.desk import paper

    store = get_desk_store()
    with _user_lock(user_id):
        tk = _own_ticket(tid, user_id)
        if tk["status"] != "ready":
            raise HTTPException(status_code=409, detail=f"ticket is {tk['status']}; only a ready "
                                                        "ticket can be confirmed")
        pid = tk.get("portfolio_id") or pf_mod.DEFAULT
        if body.portfolio_id is not None and body.portfolio_id != pid:
            raise HTTPException(status_code=409, detail="this ticket belongs to another paper "
                                                        "portfolio: review it there")
        if body.mode == "broker" and pid != pf_mod.DEFAULT:
            raise HTTPException(status_code=409, detail="named paper portfolios are paper only")
        verdict = _risk_bound([tk["order"]], user_id, pid)
        res = verdict["results"][0]
        if res["fingerprint"] != tk["fingerprint"]:
            raise HTTPException(status_code=409, detail="the order changed since it was checked: "
                                                        "make a new ticket")
        now = _now()
        if res["verdict"] != "PASS":
            store.transition_ticket(tid, user_id, ("ready",), dict(
                tk, status="blocked", verdict=res, verdict_warnings=verdict["warnings"]))
            raise HTTPException(status_code=409, detail={"message": "blocked on re-check",
                                                         "reasons": res["reasons"]})
        tk["verdict"] = res
        if body.mode == "paper":
            trade = paper.new_trade(tk["order"], ticket_id=tid, placed_at=now,
                                    profile=pf_mod.profile_for(user_id, pid))
            trade["portfolio_id"] = pid
            done = store.confirm_paper(user_id, tid, dict(tk, confirmed_at=now.isoformat()), trade)
        else:
            done = store.transition_ticket(tid, user_id, ("ready",), dict(
                tk, status="sent", sent_at=now.astimezone(NSE_SPEC.tz).isoformat(),
                confirmed_at=now.isoformat()))
        if not done:   # cancelled / sent / confirmed by another request meanwhile
            raise HTTPException(status_code=409, detail="ticket is no longer ready")
        plan = store.get_plan(tk["plan_id"], user_id) if tk.get("plan_id") else None
        if plan:
            plan["status"] = "ticketed"
            store.update_plan(plan["id"], user_id, plan)
        return _own_ticket(tid, user_id)


@router.post("/desk/tickets/{tid}/cancel")
def cancel_ticket(tid: str, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    store = get_desk_store()
    cancellable = ("ready", "blocked", "refused")
    with _user_lock(user_id):
        tk = _own_ticket(tid, user_id)
        if tk["status"] not in cancellable or \
                not store.transition_ticket(tid, user_id, cancellable, dict(tk, status="cancelled")):
            raise HTTPException(status_code=409, detail=f"ticket is {_own_ticket(tid, user_id)['status']}")
        return _own_ticket(tid, user_id)


# ---------------------------------------------------------------- paper ledger

def refresh_paper(user_id: str, portfolio_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Advance the user's pending/open paper trades (one portfolio, or all of
    them for the background loop) over the latest bars of each trade's own
    interval (legacy trades: 5m). Bars are read with the real user ID. Bars
    are read outside the lock; each trade is re-read and written under it, so
    a concurrent close or confirm is never overwritten with a stale copy."""
    from apps.api.desk import paper

    store = get_desk_store()
    prof = load_profile(user_id)
    now = _now()
    out = []
    for t in store.list_paper(user_id, limit=1000, portfolio_id=portfolio_id):
        if t["status"] in ("pending", "open"):
            interval = paper.interval_of(t)
            recovery_error = None
            past_day = t["trade_date"] < now.astimezone(NSE_SPEC.tz).date().isoformat()
            try:
                # The compact current-session cache can prune a missed trade
                # day, especially at 1m. Recover it from real deep history.
                series = (bars_source.get_history_rows(user_id, t["symbol"], interval, now=now)
                          if past_day else bars_source.get_session_rows(user_id, t["symbol"], interval))
                bars, _ = bars_source.session_bars_for(series, now)
                if past_day:
                    from apps.api.desk.market.session import parse_clock
                    spec = paper.spec_of(t)
                    if t.get("entry_fill"):
                        first_min = parse_clock(t["entry_fill"]["time"]) + spec.bar_min
                    else:
                        placed = datetime.fromisoformat(t["placed_at"]).astimezone(spec.tz)
                        minute = placed.hour * 60 + placed.minute + bool(placed.second or placed.microsecond)
                        first_min = spec.open_min + ((minute-spec.open_min+spec.bar_min-1)//spec.bar_min)*spec.bar_min
                    last_min = parse_clock(t["flat_by"]) - spec.bar_min
                    present = {spec.bar_start_min(b["slot"]) for b in bars if b["date"] == t["trade_date"]}
                    if any(m not in present for m in range(first_min, last_min+1, spec.bar_min)):
                        recovery_error = f"Historical {interval} bars for {t['trade_date']} are incomplete through {t['flat_by']}; settlement needs complete history or a manual close."
            except bars_mod.BarsError:
                bars = []
                if past_day:
                    recovery_error = f"Historical {interval} bars for {t['trade_date']} are unavailable; settlement needs history or a manual close."
            with _user_lock(user_id):
                cur = store.get_paper(t["id"], user_id) or t
                if cur["status"] in ("pending", "open"):
                    nt = (dict(cur, data_warning=recovery_error) if recovery_error
                          else paper.advance(cur, bars, now, prof))
                    if not recovery_error:
                        nt.pop("data_warning", None)
                    if nt != cur:
                        store.update_paper(t["id"], user_id, nt)
                t = store.get_paper(t["id"], user_id) or cur
        out.append(t)
    return out


@router.get("/desk/paper")
def get_paper(portfolio_id: str = Query(pf_mod.DEFAULT, pattern=pf_mod.ID_PATTERN),
              user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    from apps.api.desk import paper

    pf = _portfolio(user_id, portfolio_id)
    trades = refresh_paper(user_id, portfolio_id)
    acct = _account(user_id, portfolio_id)
    return {"trades": trades, "stats": paper.stats(trades), "cash": acct["cash"],
            "realized": acct["realized"], "capital": acct["profile"].capital,
            "portfolio_id": portfolio_id, "portfolio_name": pf["name"]}


# ---------------------------------------------------------------- paper portfolios

@router.get("/desk/portfolios")
def list_portfolios(user_id: str = Depends(current_user_id)) -> List[Dict[str, Any]]:
    return pf_mod.list_for(user_id)


@router.post("/desk/portfolios", status_code=201)
def create_portfolio(body: pf_mod.PortfolioCreate, user_id: str = Depends(current_user_id)
                     ) -> Dict[str, Any]:
    with _user_lock(user_id):     # name uniqueness and the cap hold under concurrent creates
        try:
            return pf_mod.create(user_id, body)
        except pf_mod.PortfolioError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.patch("/desk/portfolios/{portfolio_id}")
def update_portfolio(portfolio_id: str, body: pf_mod.PortfolioPatch,
                     user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    with _user_lock(user_id):     # no paper confirm between the history check and the write
        try:
            return pf_mod.update(user_id, portfolio_id, body)
        except pf_mod.PortfolioError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


class CloseBody(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    price: Optional[float] = Field(None, gt=0)


@router.post("/desk/paper/{pid}/close")
def close_paper(pid: str, body: CloseBody, user_id: str = Depends(current_user_id)
                ) -> Dict[str, Any]:
    from apps.api.desk import paper

    store = get_desk_store()
    t = store.get_paper(pid, user_id)
    if t is None:
        raise HTTPException(status_code=404, detail="paper trade not found")
    price = body.price or _fresh_price(user_id, t["symbol"])
    with _user_lock(user_id):
        # A refresh may have advanced or closed this trade while the quote was
        # fetched. Share its write lock and use the current row, never a stale copy.
        t = store.get_paper(pid, user_id)
        if t is None:
            raise HTTPException(status_code=404, detail="paper trade not found")
        if t["status"] in ("closed", "cancelled"):
            return t
        if price is None and t["status"] == "open":
            raise _unprocessable(ValueError("no recent price for this symbol: give a price"))
        nt = paper.close_now(t, price or 0, _now(), load_profile(user_id))
        store.update_paper(pid, user_id, nt)
        return store.get_paper(pid, user_id) or nt


@router.get("/desk/config")
def desk_config(user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    """Client config: whether the Kite Publisher button can be shown."""
    import os

    key = os.environ.get("KITE_PUBLISHER_API_KEY", "").strip()
    return {"kite_publisher_api_key": key or None, "today": _today_ist()}


# ---------------------------------------------------------------- alerts

def _last_bars(user_id: str, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """{SYM: {"bar": newest completed bar today, "prev_close": float}}."""
    now = _now()
    today = _today_ist()
    out: Dict[str, Dict[str, Any]] = {}
    for sym in symbols:
        try:
            series = bars_source.get_session_rows(user_id, sym, "5m")
        except bars_mod.BarsError:
            continue
        bars, _ = bars_source.session_bars_for(series, now)
        todays = [b for b in bars if b["date"] == today]
        prev = [b for b in bars if b["date"] < today]
        out[sym] = {"bar": todays[-1] if todays else None,
                    "prev_close": prev[-1]["c"] if prev else None}
    return out


def evaluate_alerts(user_id: str) -> List[Dict[str, Any]]:
    """Ring what hit, once. Returns the new events."""
    from apps.api.desk import alerts as alerts_mod

    store = get_desk_store()
    rules = [a for a in store.list_alerts(user_id) if a.get("active", True)]
    syms = sorted({a["symbol"] for a in rules if a.get("symbol") and
                   a["type"] not in ("time", "opening_bell")})
    last = _last_bars(user_id, syms)
    events = []
    now = _now()
    for a in rules:
        info = last.get(a.get("symbol") or "", {})
        msg, updated = alerts_mod.evaluate(a, now=now, last_bar=info.get("bar"),
                                           prev_close=info.get("prev_close"))
        if updated != a:
            store.update_alert(a["id"], user_id, updated)
        if msg:
            events.append(store.add_alert_event(user_id, a["id"], {
                "symbol": a.get("symbol"), "type": a["type"], "message": msg}))
    from apps.api.desk.notifications import queue_delivery
    queue_delivery(user_id)
    return events


class AlertBody(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    type: Literal["above", "below", "target", "stop", "pct_move", "time", "opening_bell"]
    symbol: Optional[str] = None
    price: Optional[float] = None
    pct: Optional[float] = None
    direction: Optional[Literal["up", "down"]] = None
    at: Optional[str] = None
    date: Optional[str] = None
    label: Optional[str] = None
    note: Optional[str] = None
    rearm: bool = False
    active: bool = True


def _valid_alert(body: Dict[str, Any]) -> Dict[str, Any]:
    from apps.api.desk import alerts as alerts_mod

    try:
        r = alerts_mod.validate({k: v for k, v in body.items() if v is not None})
        if r.get("symbol"):
            r["symbol"] = bars_mod.nse_symbol(r["symbol"])
        return r
    except (alerts_mod.AlertError, bars_mod.BarsError) as exc:
        raise _unprocessable(exc) from exc


@router.get("/desk/alerts")
def list_alerts(user_id: str = Depends(current_user_id)) -> List[Dict[str, Any]]:
    return get_desk_store().list_alerts(user_id)


@router.post("/desk/alerts", status_code=201)
def create_alert(body: AlertBody, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    store = get_desk_store()
    if len(store.list_alerts(user_id)) >= 200:
        raise HTTPException(status_code=422, detail="at most 200 alerts")
    return store.create_alert(user_id, _valid_alert(body.model_dump()))


@router.post("/desk/alerts/from-plan/{pid}", status_code=201)
def alerts_from_plan(pid: str, user_id: str = Depends(current_user_id)) -> List[Dict[str, Any]]:
    from apps.api.desk import alerts as alerts_mod

    plan = _own_plan(pid, user_id)
    store = get_desk_store()
    return [store.create_alert(user_id, r) for r in alerts_mod.from_plan(plan)]


@router.put("/desk/alerts/{aid}")
def update_alert(aid: str, body: AlertBody, user_id: str = Depends(current_user_id)
                 ) -> Dict[str, Any]:
    store = get_desk_store()
    if store.get_alert(aid, user_id) is None:
        raise HTTPException(status_code=404, detail="alert not found")
    store.update_alert(aid, user_id, _valid_alert(body.model_dump()))
    return store.get_alert(aid, user_id)  # type: ignore[return-value]


@router.delete("/desk/alerts/{aid}", status_code=204)
def delete_alert(aid: str, user_id: str = Depends(current_user_id)) -> Response:
    if not get_desk_store().delete_alert(aid, user_id):
        raise HTTPException(status_code=404, detail="alert not found")
    return Response(status_code=204)


@router.get("/desk/alerts/events")
def alert_events(check: bool = True, unseen_only: bool = False,
                 user_id: str = Depends(current_user_id)) -> List[Dict[str, Any]]:
    """Newest first. ``check`` evaluates the rules first (the background loop
    does this too, but a page open between loop ticks shouldn't wait)."""
    if check:
        evaluate_alerts(user_id)
    return get_desk_store().list_alert_events(user_id, unseen_only=unseen_only)


@router.post("/desk/alerts/events/seen", status_code=204)
def alert_events_seen(user_id: str = Depends(current_user_id)) -> Response:
    get_desk_store().mark_alert_events_seen(user_id)
    return Response(status_code=204)
