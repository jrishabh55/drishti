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

Not replayed: the desk's account-level risk check (max trades/day, daily
loss, market facts like circuit bands) — the per-strategy signal cap and the
sizing caps still apply. Results say so in ``assumptions``.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from apps.api.desk import bars as bars_mod, bars_source, entry as entry_mod, exit_plan, \
    paper, sizing
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
    "Sizing uses the strategy portfolio's capital plus realized backtest P&L; concurrent "
    "same-day trades don't reduce each other's cash.",
    "Account-level desk risk checks (max trades/day, daily loss, circuit bands) aren't replayed; "
    "the strategy's signals-per-day cap is.",
    "A scanner's matches are never replayed historically: only the strategy's own symbols "
    "(always candidates) are tested.",
]


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


def run(user_id: str, s: Dict[str, Any], body: BacktestBody, now: datetime, profile
        ) -> Dict[str, Any]:
    """Pure apart from reading bars through bars_source. Deterministic for
    the same bars."""
    interval = s.get("interval") or "5m"
    spec = bars_source.spec_for(interval)
    if body.end_date > now.astimezone(spec.tz).date():
        raise BacktestError("end_date is in the future")
    own = list(s.get("symbols") or [])
    syms = body.symbols or own
    if s.get("scanner_id"):
        # A scanner's matches can't be reconstructed as of past sessions, so
        # only the strategy's own symbols — always candidates — are replayed.
        if not own:
            raise BacktestError("this strategy only uses a scanner, whose past matches can't be "
                                "replayed: add symbols to the strategy to test them")
        extra = sorted(set(syms) - set(own))
        if extra:
            raise BacktestError(f"{', '.join(extra)} would only come from the scanner, whose past "
                                "matches can't be replayed: test the strategy's own symbols")
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

    trades: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    realized = 0.0
    split = body.split_date.isoformat() if body.split_date else None
    for d in days:
        day = d.isoformat()
        end = spec.local(d, spec.close_min) + timedelta(minutes=1)
        found = []
        for sym in syms:
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
                skipped.append({"date": day, "symbol": sym, "reason": str(exc)})
                continue
            if ev["status"] == "triggered":
                found.append((ev["trigger"]["time"], sym, ev, rows))
        for t_time, sym, ev, rows in sorted(found)[:s.get("max_signals_per_day") or 1]:
            t = _trade(s, sym, ev, rows, d, end, profile, spec, interval, stamp,
                       cash=profile.capital + realized)
            if "reason" in t:
                skipped.append(t)
                continue
            t["split"] = None if split is None else ("train" if day < split else "test")
            realized += t.get("pnl_net") or 0.0
            trades.append(t)

    out = {
        "strategy_id": s["id"], **stamp,
        "strategy_snapshot": _snapshot(s),
        "config": body.model_dump(mode="json") | {"symbols": syms, "interval": interval},
        "profile_snapshot": {k: getattr(profile, k) for k in (
            "capital", "slippage_ticks", "flat_by", "no_entries_before", "no_entries_after",
            "max_loss_per_trade_inr", "product")},
        "scanner_candidates": "not_replayed" if s.get("scanner_id") else None,
        "sources": sources, "coverage": coverage, "trades": trades, "skipped": skipped,
        "summary": {"all": paper.stats(trades)},
        "costs": {"charges": round(sum(t.get("charges") or 0 for t in trades), 2),
                  "estimated": True},
        "generated_at": now.isoformat(), "disclaimer": DISCLAIMER, "assumptions": ASSUMPTIONS,
    }
    if split:
        out["summary"]["train"] = paper.stats([t for t in trades if t["split"] == "train"])
        out["summary"]["test"] = paper.stats([t for t in trades if t["split"] == "test"])
    return out


def _trade(s, sym, ev, rows, d, end, profile, spec, interval, stamp, *, cash) -> Dict[str, Any]:
    day = d.isoformat()
    trig = ev["trigger"]
    orr = ev.get("opening_range") or {}
    skip = {"date": day, "symbol": sym, "signal_time": trig["time"]}
    bars = [b for b in session_bars(rows, spec, end)[0] if b["date"] == day]
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
        sz = sizing.size_trade(plan, profile, cash=cash, order_type=s["order_type"],
                               price=float(trig["price"]) if s["order_type"] == "MARKET" else None,
                               today=day)
    except (exit_plan.PlanError, sizing.SizeError, ValueError) as exc:
        return dict(skip, reason=str(exc))
    order = sizing.order_from(sz)
    if order is None:
        return dict(skip, reason=sz.get("refusal") or "sized to 0 shares")
    order.update(stamp)
    if s.get("exit_policy"):
        order["exit_policy"] = s["exit_policy"]
    placed = spec.local(d, parse_clock(trig["time"]))
    t = paper.new_trade(order, ticket_id=f"bt-{day}-{sym}", placed_at=placed, profile=profile,
                        spec=spec)
    t = paper.advance(t, bars, end, profile, spec)
    t.update(signal_time=trig["time"], source=ev.get("source"))
    return t
