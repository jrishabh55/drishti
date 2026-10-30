"""Saved strategy backtests: replay a strategy over past sessions with the
SAME blocks the runtime uses — entry.evaluate (completed bars, profile entry
window, min_volume_x), exit_plan.build_plan, sizing.size_trade and the paper
fill/exit model (paper.new_trade + paper.advance, including exit policies,
charges and slippage). Distinct from the Time Machine (Monte Carlo of one
plan) and from AI analysis batches; it never calls a model.

No future data: a day is evaluated only on its own completed bars, the
order is placed at the trigger bar's close and fills only on bars that start
at/after it. Sessions with missing bars are reported as ``incomplete`` and
not traded; days without bars are ``no_data``. Nothing is invented.

Account replay: signals across all symbols are processed in time order
against ONE virtual portfolio account, rebuilt at each signal's time the way
routes.desk._account builds the live one (pending/open orders reserve cash,
fills become holdings, closed trades release cash and realize P&L, today's
non-cancelled entries count for trades/day and daily loss). Each signal is
sized with sizing.size_trade on that cash and checked with the pure
risk.check at the signal's time, so every per-trade, position, order, cash,
trades/day, orders/day, daily-loss and window limit applies as at runtime.
Past NSE exchange facts (circuit bands, ASM/GSM, F&O ban, results) aren't
available: those checks are skipped and disclosed exactly as the live risk
check discloses unavailable NSE data — today's facts are never used.

Scanner strategies replay their scanner as of each date over a FROZEN
universe the user lists, on daily bars strictly before that date. Conditions
with no history (fundamentals, sector/index membership, intraday timeframes)
are refused, never evaluated with today's values.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

import pandas as pd

from apps.api.desk import bars as bars_mod, bars_source, entry as entry_mod, exit_plan, \
    paper, risk, sizing
from apps.api.desk.bars import session_bars
from apps.api.desk.market.session import parse_clock
from apps.api.kite import strategies

MAX_SYMBOLS = 20
MAX_DAYS = 60
DISCLAIMER = ("Historical simulation on past bars with estimated charges and modelled fills. "
              "It is not a prediction, and a profitable test does not mean the strategy will "
              "make money.")
ASSUMPTIONS = [
    "Fills: bar-based paper model (stop before target inside a bar; MARKET adds the profile's "
    "slippage ticks; LIMIT fills at the limit or a better open).",
    "Charges: estimated from your broker plan, per exit leg.",
    "Account: one virtual portfolio account replayed in time order across symbols; pending "
    "and open orders reserve cash, and every desk risk limit is checked at the signal's time.",
    "NSE exchange facts for past dates (circuit bands, ASM/GSM, F&O ban, results) are "
    "unavailable: those checks are skipped and disclosed, never filled with today's facts.",
    "Fills are known only bar by bar: a fill or exit counts for the account once its bar has "
    "completed, as the paper ledger sees it.",
]
SCANNER_ASSUMPTION = (
    "Scanner: replayed as of each date on daily bars before that date over the frozen universe "
    "you listed. The universe is today's list, so symbols since delisted or renamed are missing "
    "(survivorship bias), and the bar source of each symbol is shown in sources.")


class BacktestError(ValueError):
    pass


class BacktestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: str = Field(min_length=1, max_length=64)
    start_date: date
    end_date: date
    split_date: Optional[date] = None
    symbols: Optional[List[str]] = Field(None, min_length=1, max_length=MAX_SYMBOLS)

    @field_validator("symbols", mode="before")
    @classmethod
    def _syms(cls, v):
        if v is None:
            return v
        if len(v) > MAX_SYMBOLS:
            raise ValueError(f"at most {MAX_SYMBOLS} symbols")
        try:
            return sorted({bars_mod.nse_symbol(s) for s in v})
        except bars_mod.BarsError as exc:
            raise ValueError(str(exc)) from exc

    @model_validator(mode="after")
    def _dates(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date is before start_date")
        if (self.end_date - self.start_date).days + 1 > MAX_DAYS:
            raise ValueError(f"at most {MAX_DAYS} calendar days per test")
        if self.split_date is not None and not self.start_date < self.split_date <= self.end_date:
            raise ValueError("split_date must be after start_date and no later than end_date")
        return self


def _snapshot(s: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in s.items() if k not in ("signals", "last_scan", "auto_opt_in")} | {
        "version": strategies.version_of(s)}


def unreplayable(definition) -> List[str]:
    """Conditions a historical replay can't evaluate truthfully."""
    from apps.api.scanner.schema import Condition, Group, Operand

    out: List[str] = []

    def op(o):
        if o is None or isinstance(o, str):
            return
        if isinstance(o, Operand):
            if o.fundamental:
                out.append(f"fundamental {o.fundamental!r} has no historical values here")
            if o.meta:
                out.append(f"{o.meta} membership has no history here")
            op(o.of)
            for a in o.args or []:
                op(a)
            if o.cond is not None:
                node(o.cond)

    def node(n):
        if isinstance(n, Group):
            for c in n.children:
                node(c)
        elif isinstance(n, Condition):
            if n.timeframe != "1d":
                out.append(f"{n.timeframe} conditions aren't replayed (daily conditions only)")
            op(n.left)
            op(n.right)

    node(definition)
    return sorted(set(out))


def _daily_panel(rows_by: Dict[str, list], spec):
    from apps.api.scanner.indicators import Panel

    frames = {}
    for i, fld in enumerate(("open", "high", "low", "close", "volume"), start=1):
        frames[fld] = pd.DataFrame({
            sym: pd.Series({pd.Timestamp(spec.to_local(r[0]).date()): r[i] for r in rows},
                           dtype=float)
            for sym, rows in rows_by.items()}).sort_index()
    return Panel(fundamentals=pd.DataFrame(), meta=pd.DataFrame(), **frames)


def _scanner_matches(definition, panel, universe: List[str], d: date) -> List[str]:
    """Matches as of the session OPEN of ``d``: only daily bars before it."""
    from apps.api.scanner.engine import ScanEngine
    from apps.api.scanner.indicators import Panel

    cut = panel.close.index < pd.Timestamp(d)
    past = Panel(fundamentals=panel.fundamentals, meta=panel.meta,
                 **{f: getattr(panel, f)[cut] for f in ("open", "high", "low", "close", "volume")})
    if past.close.empty:
        return []
    # ponytail: the engine's pure evaluator over a per-date panel; no store is read.
    mask, _ = ScanEngine(None)._group(definition, {"1d": past})  # type: ignore[arg-type]
    return [sym for sym in universe if bool(mask.get(sym, False))]


def _done(fill: Dict[str, Any], at_min: int, bar_min: int) -> bool:
    """An exit (leg) is known once its bar completed; flat-by/session-end are
    stamped at the bar's close already."""
    m = parse_clock(fill["time"])
    return (m if fill["reason"] in ("flat_by", "session_end") else m + bar_min) <= at_min


def _state_at(t: Dict[str, Any], at_min: int, bar_min: int) -> str:
    """What the paper ledger would show for ``t`` at ``at_min``: fills and
    exits count once the bar they happened in has completed."""
    ex = t.get("exit_fill")
    if ex and _done(ex, at_min, bar_min):
        return "closed"
    fill = t.get("entry_fill")
    if fill and parse_clock(fill["time"]) + bar_min <= at_min:
        return "open"
    if t["status"] == "cancelled" and parse_clock(t["events"][-1]["at"]) <= at_min:
        return "cancelled"
    return "pending"


def _account(capital: float, realized: float, day_trades: List[Dict[str, Any]], at_min: int,
             bar_min: int, profile=None) -> Dict[str, Any]:
    """routes.desk._account, replayed: each trade is projected to ``at_min``
    (only completed fills and exit legs) and booked with paper.accounting."""
    from apps.api.desk.profile import DeskProfile

    profile = profile or DeskProfile()
    holdings: Dict[str, float] = {}
    values: Dict[str, float] = {}
    entries = []
    for t in day_trades:
        st = _state_at(t, at_min, bar_min)
        if st == "cancelled":
            continue
        entries.append({"symbol": t["symbol"], "side": "buy",
                        "max_loss": (t.get("risk_per_share") or 0) * t["qty"]})
        if st == "open":
            legs = [x for x in t.get("exit_legs") or [] if _done(x, at_min, bar_min)]
            view = dict(t, status="open", exit_legs=legs,
                        open_qty=t["qty"] - sum(x["qty"] for x in legs))
        else:
            view = t if st == "closed" else dict(t, status="pending")
        acc = paper.accounting(view, profile)
        realized += acc["realized"]
        if acc["open_qty"]:
            holdings[t["symbol"]] = holdings.get(t["symbol"], 0) + acc["open_qty"]
        if acc["committed"]:
            values[t["symbol"]] = values.get(t["symbol"], 0.0) + acc["committed"]
    return {"cash": round(capital + realized - sum(values.values()), 2), "holdings": holdings,
            "values": values, "entries": entries}


def run(user_id: str, s: Dict[str, Any], body: BacktestBody, now: datetime, profile
        ) -> Dict[str, Any]:
    """Pure apart from reading bars through bars_source (and the strategy's
    scanner definition). Deterministic for the same bars."""
    interval = s.get("interval") or "5m"
    spec = bars_source.spec_for(interval)
    if body.end_date > now.astimezone(spec.tz).date():
        raise BacktestError("end_date is in the future")
    own = list(s.get("symbols") or [])
    definition, universe = None, []
    if s.get("scanner_id"):
        definition = _definition(user_id, s["scanner_id"])
        bad = unreplayable(definition)
        if bad:
            raise BacktestError("this scanner can't be replayed on historical data: " +
                                "; ".join(bad))
        universe = sorted(set(body.symbols or []) - set(own))
        if not universe and not own:
            raise BacktestError("list the frozen universe of symbols the scanner should be "
                                "replayed over (symbols)")
        syms = own + universe
    else:
        syms = body.symbols or own
    days = [body.start_date + timedelta(days=i)
            for i in range((body.end_date - body.start_date).days + 1)]
    days = [d for d in days if spec.is_trading_day(d)]
    stamp = strategies.attribution(s)
    sources: Dict[str, str] = {}
    coverage: Dict[str, Dict[str, Any]] = {}
    rows_by: Dict[str, Dict[str, list]] = {}
    for sym in syms:
        try:
            series = bars_source.get_history_rows(user_id, sym, interval, now=now)
        except bars_mod.BarsError as exc:
            coverage[sym] = {"status": "insufficient_history", "error": str(exc), "days": {}}
            continue
        sources[sym] = series.source
        bars, _ = session_bars(series.rows, spec, now)
        counts: Dict[str, int] = {}
        for b in bars:
            counts[b["date"]] = counts.get(b["date"], 0) + 1
        by_day: Dict[str, list] = {}
        for r in series.rows:
            by_day.setdefault(spec.to_local(r[0]).date().isoformat(), []).append(r)
        state = {}
        for d in days:
            n = counts.get(d.isoformat(), 0)
            state[d.isoformat()] = "no_data" if n == 0 else \
                "complete" if n >= spec.n_bars else "incomplete"
        complete = [d for d, v in state.items() if v == "complete"]
        coverage[sym] = {"status": "insufficient_history" if not complete else
                         "ok" if len(complete) == len(days) else "partial", "days": state}
        rows_by[sym] = {d: by_day.get(d, []) for d in complete}

    scanner_out = None
    panel = None
    if definition is not None:
        daily: Dict[str, list] = {}
        for sym in universe:
            try:
                ds = bars_source.get_history_rows(user_id, sym, "1d", now=now)
                daily[sym] = ds.rows
                sources[f"{sym}:1d"] = ds.source
            except bars_mod.BarsError as exc:
                daily[sym] = []
                sources[f"{sym}:1d"] = f"unavailable: {exc}"
        panel = _daily_panel(daily, spec) if universe else None
        scanner_out = {"id": s["scanner_id"], "universe": universe, "matches": {},
                       "timeframe": "1d", "as_of": "the session open (prior daily bars only)"}
        if not universe:
            scanner_out["note"] = ("no universe listed: the scanner wasn't replayed; only the "
                                   "strategy's own symbols were tested")

    trades: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    warnings: set = set()
    realized = 0.0
    split = body.split_date.isoformat() if body.split_date else None
    cap = s.get("max_signals_per_day") or 1
    for d in days:
        day = d.isoformat()
        end = spec.local(d, spec.close_min) + timedelta(minutes=1)
        # Scanner runs: own symbols + that date's matches; otherwise the
        # effective list (a body.symbols override replaces the own list).
        cands = list(own if definition is not None else syms)
        if panel is not None:
            matched = _scanner_matches(definition, panel, universe, d)
            scanner_out["matches"][day] = matched
            cands += [x for x in matched if x not in cands]
        cands = cands[:strategies.MAX_SYMBOLS]
        found = []
        for sym in cands:
            rows = rows_by.get(sym, {}).get(day)
            if not rows:
                continue
            try:
                ev = entry_mod.evaluate(rows, end, symbol=sym, setup=s["setup"],
                                        or_minutes=s["or_minutes"], level=s.get("level"),
                                        min_volume_x=s.get("min_volume_x"),
                                        no_entries_before=profile.no_entries_before,
                                        no_entries_after=profile.no_entries_after, spec=spec,
                                        source=sources[sym])
            except entry_mod.EntryError as exc:
                skipped.append({"date": day, "symbol": sym, "reason": str(exc),
                                "reasons": [str(exc)]})
                continue
            if ev["status"] == "triggered":
                found.append((parse_clock(ev["trigger"]["time"]), sym, ev, rows))
        day_trades: List[Dict[str, Any]] = []
        for n, (at_min, sym, ev, rows) in enumerate(sorted(found, key=lambda f: f[:2])):
            if n >= cap:
                why = f"strategy cap: {cap} signal(s) a day already used"
                skipped.append({"date": day, "symbol": sym, "signal_time": ev["trigger"]["time"],
                                "reason": why, "reasons": [why]})
                continue
            acct = _account(profile.capital, realized, day_trades, at_min, spec.bar_min, profile)
            t, skip = _trade(s, sym, ev, rows, d, end, profile, spec, interval, stamp, acct,
                             warnings)
            if skip:
                skipped.append(skip)
                continue
            t["split"] = None if split is None else ("train" if day < split else "test")
            day_trades.append(t)
        for t in day_trades:          # MIS: every trade is closed or cancelled by the end
            realized += t.get("pnl_net") or 0.0
        trades += day_trades

    out = {
        "strategy_id": s["id"], **stamp,
        "strategy_snapshot": _snapshot(s),
        "config": body.model_dump(mode="json") | {"symbols": syms, "interval": interval},
        "profile_snapshot": {k: getattr(profile, k) for k in (
            "capital", "slippage_ticks", "flat_by", "no_entries_before", "no_entries_after",
            "max_loss_per_trade_inr", "max_daily_loss_inr", "max_trades_per_day",
            "max_orders_per_day", "max_order_inr", "max_position_pct", "cash_buffer_pct",
            "min_reward_risk", "product")},
        "scanner": scanner_out,
        "scanner_candidates": ("replayed_as_of_date" if universe else "not_replayed")
        if scanner_out else None,
        "sources": sources, "coverage": coverage, "trades": trades, "skipped": skipped,
        "risk": {"engine": "desk risk.check", "market_facts": "unavailable",
                 "warnings": sorted(warnings)},
        "summary": {"all": paper.stats(trades, profile)},
        "costs": {"charges": round(sum(t.get("charges") or 0 for t in trades), 2),
                  "estimated": True},
        "generated_at": now.isoformat(), "disclaimer": DISCLAIMER,
        "assumptions": ASSUMPTIONS + ([SCANNER_ASSUMPTION] if scanner_out else []),
    }
    if split:
        out["summary"]["train"] = paper.stats([t for t in trades if t["split"] == "train"],
                                              profile)
        out["summary"]["test"] = paper.stats([t for t in trades if t["split"] == "test"], profile)
    return out


def _definition(user_id: str, scanner_id: str):
    from apps.api.scanner.schema import parse_definition
    from apps.api.scanner.store import get_scanner_store

    sc = get_scanner_store().get_scanner(scanner_id)
    if not strategies.scanner_ok(sc, user_id):
        raise BacktestError("the strategy's scanner was deleted or isn't yours")
    return parse_definition(sc["definition"])


def _trade(s, sym, ev, rows, d, end, profile, spec, interval, stamp, acct, warnings):
    """Plan → size on the replayed cash → risk.check at the signal time →
    paper fill/exit. Returns (trade, None) or (None, skipped)."""
    day = d.isoformat()
    trig = ev["trigger"]
    orr = ev.get("opening_range") or {}
    skip = {"date": day, "symbol": sym, "signal_time": trig["time"]}
    at = spec.local(d, parse_clock(trig["time"]))
    bars = [b for b in session_bars(rows, spec, end)[0] if b["date"] == day]
    # The trigger bar's close is the last completed price at the signal: the
    # historical twin of routes.desk._fresh_price.
    prices = {sym: float(trig["price"])}
    try:
        plan = exit_plan.build_plan(
            symbol=sym, date=day, entry=float(trig["price"]), setup=s["setup"],
            entry_time=trig["time"], or_high=orr.get("high"), or_low=orr.get("low"),
            or_minutes=s["or_minutes"], stop_rule=s["stop_rule"], stop_param=s.get("stop_param"),
            **strategies.target_of(s), flat_by=profile.flat_by,
            broker_square_off=profile.broker_square_off, min_reward_risk=profile.min_reward_risk,
            session_bars_to_entry=[b for b in bars if b["slot"] <= trig["bar_index"]]
            if s["stop_rule"] == "atr" else None, spec=spec)
        plan["interval"] = interval
        sz = sizing.size_trade(plan, profile, cash=acct["cash"], order_type=s["order_type"],
                               price=prices[sym] if s["order_type"] == "MARKET" else None,
                               today=day)
    except (exit_plan.PlanError, sizing.SizeError, ValueError) as exc:
        return None, dict(skip, reason=str(exc), reasons=[str(exc)])
    order = sizing.order_from(sz)
    if order is None:
        why = sz.get("refusal") or "sized to 0 shares"
        return None, dict(skip, reason=why, reasons=[why])
    verdict = risk.check([order], profile, now=at, cash=acct["cash"], holdings=acct["holdings"],
                         holding_values=acct["values"], today_entries=acct["entries"],
                         today_orders=len(acct["entries"]), prices=prices, market={}, spec=spec)
    warnings.update(verdict["warnings"])
    res = verdict["results"][0]
    if res["verdict"] != "PASS":
        return None, dict(skip, reason="; ".join(res["reasons"]), reasons=res["reasons"],
                          cash=acct["cash"])
    order.update(stamp)
    if s.get("exit_policy"):
        order["exit_policy"] = s["exit_policy"]
    t = paper.new_trade(order, ticket_id=f"bt-{day}-{sym}", placed_at=at, profile=profile,
                        spec=spec)
    t = paper.advance(t, bars, end, profile, spec)
    t.update(signal_time=trig["time"], source=ev.get("source"), sizing_bound_by=sz["bound_by"],
             cash_at_signal=acct["cash"], risk_warnings=res["warnings"])
    return t, None
