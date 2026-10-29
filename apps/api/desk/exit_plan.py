# Ported from day-trader-skill (https://github.com/Jakeschincariol/day-trader-skill,
# commit 3d0f4ef), MIT License, Copyright (c) 2026 Jake Schincariol.
# Adapted for NSE (tick sizes, IST flat-by vs broker square-off) by QuberTech.
"""Exit planner: set the stop and the target BEFORE entering. Long only.

Stops (round to the NSE tick AWAY from the entry — down for a long):
  or_mid     opening-range midpoint (the orb default)
  or_low     opening-range low
  price      your price
  pct        X % under the entry
  atr        K × ATR(14) of today's bars up to the entry bar (all of them if
             fewer than 14 — and it says so)
Targets (round to the tick TOWARD the entry, so the limit fills no later):
  r          entry + R × risk
  price      your price

Refused with the reason: a stop at/above the entry, a target at/below it, a
risk under 0.05 % of the price (inside the spread's noise), an entry at or
after flat-by. ``min_reward_risk`` only warns here; the risk check enforces.
A stop is a plan, not a guarantee: a gap or a fast market fills past it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from apps.api.desk.bars import Bar
from apps.api.desk.market import ticks
from apps.api.desk.market.session import NSE, SessionSpec, clock24, parse_clock

ATR_BARS = 14
MIN_RISK_PCT = 0.05
STOP_RULES = ("or_mid", "or_low", "price", "pct", "atr")


class PlanError(ValueError):
    pass


def atr_of(bars: Sequence[Bar]) -> tuple[float, int]:
    """Simple average true range over the last ≤14 bars (upstream atr_of)."""
    trs = []
    for i, b in enumerate(bars):
        if i == 0:
            trs.append(b["h"] - b["l"])
        else:
            pc = bars[i - 1]["c"]
            trs.append(max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc)))
    tail = trs[-ATR_BARS:]
    return sum(tail) / len(tail), len(tail)


def compute(entry: float, *, stop_rule: str, stop_param: Optional[float] = None,
            target_rule: str = "r", target_param: float = 2.0,
            or_high: Optional[float] = None, or_low: Optional[float] = None,
            atr: Optional[float] = None, atr_bars: Optional[int] = None) -> Dict[str, Any]:
    if entry <= 0:
        raise PlanError("the entry price must be above ₹0")
    detail = ""
    if stop_rule in ("or_mid", "or_low"):
        if or_high is None or or_low is None:
            raise PlanError(f"an {stop_rule} stop needs the opening range (high and low)")
        if or_high <= or_low:
            raise PlanError(f"the opening-range high {or_high} must be above its low {or_low}")
        if stop_rule == "or_mid":
            raw, label = (or_high + or_low) / 2, "opening-range midpoint"
            detail = f"({or_high:.2f} + {or_low:.2f}) / 2 = {raw:.4f}"
        else:
            raw, label = or_low, "opening-range low"
    elif stop_rule == "price":
        if not stop_param or stop_param <= 0:
            raise PlanError("a price stop needs a price above ₹0")
        raw, label = stop_param, "your price"
    elif stop_rule == "pct":
        if not stop_param or not 0 < stop_param < 100:
            raise PlanError("a pct stop needs a percent between 0 and 100")
        raw, label = entry * (1 - stop_param / 100), f"{stop_param:g}% under the entry"
        detail = f"{entry:.2f} × (1 − {stop_param:g}%) = {raw:.4f}"
    elif stop_rule == "atr":
        if not stop_param or stop_param <= 0:
            raise PlanError("an atr stop needs a multiple above 0 (e.g. 1.5)")
        if atr is None:
            raise PlanError("an atr stop needs today's bars up to the entry")
        raw = entry - stop_param * atr
        label = f"{stop_param:g} × ATR({atr_bars}) under the entry"
        detail = f"ATR {atr:.4f} over the {atr_bars} bars to the entry" + (
            " (fewer than 14 so far)" if (atr_bars or 0) < ATR_BARS else "")
    else:
        raise PlanError(f"stop rule must be one of {', '.join(STOP_RULES)}")
    tick = ticks.tick_size(entry)
    stop = ticks.round_away(raw, long=True, tick=tick)
    if abs(stop - raw) > 1e-9:
        detail = (detail + ", " if detail else f"{raw:.4f} ") + f"rounded down to the ₹{tick:g} tick"
    if stop >= entry:
        raise PlanError(f"the stop ₹{stop:.2f} ({label}) is at or above the entry ₹{entry:.2f}: a "
                        "long's stop goes under the entry")
    if stop <= 0:
        raise PlanError(f"the stop works out to ₹{stop:.2f}: pick a stop above ₹0")
    risk = round(entry - stop, 4)
    floor_risk = entry * MIN_RISK_PCT / 100
    if risk < floor_risk:
        raise PlanError(f"the stop is ₹{risk:.2f} under the entry, less than {MIN_RISK_PCT}% of the "
                        f"price (₹{floor_risk:.2f}): too tight to survive the spread. Widen it.")
    if target_rule == "r":
        if target_param <= 0:
            raise PlanError("the target R must be above 0, like 1R or 2R")
        target = ticks.round_toward(entry + target_param * risk, long=True, tick=tick)
        t_label = f"{target_param:g}R"
    elif target_rule == "price":
        target = ticks.round_to_tick(target_param, tick)
        t_label = "your price"
    else:
        raise PlanError("target rule is r or price")
    if target <= entry:
        raise PlanError(f"the target ₹{target:.2f} is at or below the entry ₹{entry:.2f}")
    rr = round((target - entry) / risk, 2)
    return {"entry": round(entry, 2), "stop": round(stop, 2), "stop_label": label,
            "stop_detail": detail, "target": round(target, 2), "target_label": t_label,
            "target_r": rr, "reward_risk": rr, "risk_per_share": round(risk, 2), "tick": tick}


def build_plan(*, symbol: str, date: str, entry: float, setup: str = "orb",
               entry_time: Optional[str] = None, or_high: Optional[float] = None,
               or_low: Optional[float] = None, or_minutes: int = 15,
               stop_rule: Optional[str] = None, stop_param: Optional[float] = None,
               target_rule: str = "r", target_param: float = 2.0,
               flat_by: str = "15:15", broker_square_off: str = "15:20",
               min_reward_risk: Optional[float] = 1.0,
               session_bars_to_entry: Optional[Sequence[Bar]] = None,
               spec: SessionSpec = NSE) -> Dict[str, Any]:
    """The plan document saved to desk_plans (upstream trader/plan.json)."""
    if stop_rule is None:
        if setup != "orb":
            raise PlanError(f"a {setup} setup has no opening-range stop to default to: pick a "
                            "price, pct or atr stop")
        stop_rule = "or_mid"
    atr = atr_n = None
    if stop_rule == "atr":
        bars = list(session_bars_to_entry or [])
        if len(bars) < 3:
            raise PlanError(f"an atr stop needs at least 3 closed bars by the entry; found {len(bars)}")
        atr, atr_n = atr_of(bars)
    c = compute(entry, stop_rule=stop_rule, stop_param=stop_param, target_rule=target_rule,
                target_param=target_param, or_high=or_high, or_low=or_low, atr=atr,
                atr_bars=atr_n)
    flat_min = parse_clock(flat_by)
    if flat_min >= parse_clock(broker_square_off):
        raise PlanError(f"flat-by {flat_by} must be before your broker's auto square-off "
                        f"{broker_square_off}")
    if flat_min > spec.close_min:
        raise PlanError("flat-by must be at or before the 15:30 close")
    entry_bar = None
    if entry_time:
        et = parse_clock(entry_time)
        if et >= flat_min:
            raise PlanError(f"the entry at {clock24(et)} is at or after your flat-by "
                            f"{clock24(flat_min)}: no time left to hold it")
        try:
            entry_bar = spec.bar_for_close(et)
        except ValueError as exc:
            raise PlanError(str(exc)) from None
        entry_time = clock24(et)
    warnings: List[str] = []
    if min_reward_risk is not None and c["reward_risk"] < min_reward_risk:
        warnings.append(f"Reward:risk {c['reward_risk']:.2f} is under your minimum "
                        f"{min_reward_risk:g}: the risk check will block it.")
    orr = ({"high": round(or_high, 2), "low": round(or_low, 2), "minutes": or_minutes}
           if or_high is not None and or_low is not None else None)
    plan = {"symbol": symbol, "side": "buy", "date": date, "setup": setup,
            "entry_time": entry_time, "entry_bar": entry_bar, "interval": "5m",
            "opening_range": orr, "or_minutes": or_minutes, "stop_rule": stop_rule,
            "stop_param": stop_param, "target_rule": target_rule, "target_param": target_param,
            "flat_by": clock24(flat_min), "warnings": warnings, "status": "draft", **c}
    if atr is not None:
        plan["atr"] = round(atr, 4)
        plan["atr_bars"] = atr_n
    return plan
