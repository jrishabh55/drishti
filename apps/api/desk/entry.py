# Ported from day-trader-skill (https://github.com/Jakeschincariol/day-trader-skill,
# commit 3d0f4ef), MIT License, Copyright (c) 2026 Jake Schincariol.
# Adapted for NSE (IST session, ₹, profile entry window) by QuberTech.
"""Entry timer: the setup's status right now — waiting, triggered or expired.

Setups (long only, like upstream v2):
  orb    the first COMPLETED bar after the opening range that CLOSES above the
         range high. A wick above it is not a trigger.
  level  the first completed bar that closes above a price (e.g. a 20-day high).
Optional ``min_volume_x``: the trigger bar also needs at least X times the
average volume of the opening-range bars (the 09:15 bar carries the
pre-open auction, so 1.0 is already a high bar) — only those closed by the
candidate bar's close, so a level signal inside the range never reads later
bars. A trigger's ``opening_range`` is likewise the range as of the trigger.

A trigger's time is the bar's CLOSE time and its price is that close. Bars
count only once closed (now ≥ start + 5 min). Entries outside the profile's
window (``no_entries_before`` / ``no_entries_after``) don't trigger.
Statuses: triggered | waiting | expired | no-data. It never places an order.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from apps.api.desk.bars import Row, session_bars
from apps.api.desk.market.session import NSE, SessionSpec, clock12, clock24, parse_clock


class EntryError(ValueError):
    pass


def evaluate(rows: List[Row], now: datetime, *, symbol: str, setup: str = "orb",
             or_minutes: int = 15, level: Optional[float] = None,
             min_volume_x: Optional[float] = None, no_entries_before: Optional[str] = "09:30",
             no_entries_after: Optional[str] = "14:45", spec: SessionSpec = NSE,
             source: str = "delayed:yahoo") -> Dict[str, Any]:
    if setup not in ("orb", "level"):
        raise EntryError("setup is orb or level")
    if setup == "level" and (level is None or level <= 0):
        raise EntryError("a level setup needs a price to close above (for example a 20-day high)")
    if or_minutes < spec.bar_min or or_minutes % spec.bar_min or or_minutes > 120:
        raise EntryError("or_minutes must be a multiple of 5 from 5 to 120")
    n_or = or_minutes // spec.bar_min
    local = now.astimezone(spec.tz)
    day = local.date()
    now_min = local.hour * 60 + local.minute
    before = parse_clock(no_entries_before) if no_entries_before else spec.open_min
    cutoff = min(parse_clock(no_entries_after) if no_entries_after else spec.close_min,
                 spec.close_min)
    res: Dict[str, Any] = {
        "symbol": symbol, "setup": setup, "side": "buy", "date": day.isoformat(),
        "or_minutes": or_minutes, "now": local.strftime("%Y-%m-%d %H:%M:%S"), "status": "waiting",
        "phase": "", "reason": "", "opening_range": None, "level": None, "trigger": None,
        "last": None, "no_entries_before": clock24(before), "no_entries_after": clock24(cutoff),
        "source": source, "min_volume_x": min_volume_x,
    }
    if not spec.is_trading_day(day):
        res.update(status="no-data", phase="closed",
                   reason=f"NSE is closed today ({day.strftime('%A')} {day.isoformat()})")
        return res
    bars = [b for b in session_bars(rows, spec, now)[0] if b["date"] == day.isoformat()]
    res["bars_closed"] = len(bars)
    or_end = spec.open_min + or_minutes
    if now_min < spec.open_min:
        res.update(phase="pre-open", reason="NSE opens at 9:15" + (
            f" and the {or_minutes}-minute range is set at {clock12(or_end)}"
            if setup == "orb" else ""))
        return res
    or_bars = [b for b in bars if b["slot"] < n_or]

    def avg_vol_at(slot: int) -> Optional[float]:
        seen = [b["v"] for b in or_bars if b["slot"] <= slot]
        return sum(seen) / len(seen) if seen else None

    def opening_range(upto: List[Dict[str, Any]], at_min: int) -> Optional[Dict[str, Any]]:
        if not upto:
            return None
        return {"high": round(max(b["h"] for b in upto), 2),
                "low": round(min(b["l"] for b in upto), 2),
                "minutes": or_minutes, "bars": len(upto), "of": n_or,
                "complete": at_min >= or_end and len(upto) == n_or}

    res["opening_range"] = opening_range(or_bars, now_min)
    if setup == "orb":
        if now_min < or_end:
            res.update(phase="forming", reason=f"{len(or_bars)} of {n_or} range bars closed; the "
                                               f"range is set at {clock12(or_end)}")
            return res
        if not or_bars:
            res.update(status="no-data", phase="open",
                       reason="no bars for the opening range yet (delayed data may still be "
                              "catching up)")
            return res
        lvl = max(b["h"] for b in or_bars)
    else:
        lvl = float(level)  # type: ignore[arg-type]
    res["level"] = round(lvl, 2)
    first_slot = n_or if setup == "orb" else 0
    trig, thin = None, []
    for b in bars:
        close_min = spec.bar_close_min(b["slot"])
        if b["slot"] < first_slot or close_min < before or close_min > cutoff:
            continue
        if b["c"] > lvl:
            av = avg_vol_at(b["slot"])
            vx = b["v"] / av if av else None
            if min_volume_x and (vx is None or vx < min_volume_x):
                thin.append(b)
                continue
            trig = b
            break
    last = bars[-1] if bars else None
    if last:
        res["last"] = {"bar_start": last["start"], "close": round(last["c"], 2),
                       "vs_level_pct": round(100.0 * (last["c"] / lvl - 1), 2)}
    what = ("the range high" if setup == "orb" else "the level")
    if trig:
        close_min = spec.bar_close_min(trig["slot"])
        av = avg_vol_at(trig["slot"])
        vx = trig["v"] / av if av else None
        # The range as it stood at the signal: a level trigger inside the
        # range never sees (or derives a stop from) bars after it.
        res["opening_range"] = opening_range([b for b in or_bars if b["slot"] <= trig["slot"]],
                                             close_min)
        res.update(status="triggered", phase="open" if now_min < spec.close_min else "after-close",
                   reason=f"the {trig['start']} bar closed above {what}")
        res["trigger"] = {"time": clock24(close_min), "bar_start": trig["start"],
                          "bar_index": trig["slot"], "price": round(trig["c"], 2),
                          "volume_x": round(vx, 2) if vx else None,
                          "pct_over": round(100.0 * (trig["c"] / lvl - 1), 2)}
        res["minutes_since"] = max(0, now_min - close_min)
        return res
    if not bars:
        res.update(status="no-data", phase="open",
                   reason=f"no bars for {day.isoformat()} yet (delayed data)")
        return res
    if now_min >= cutoff:
        res.update(status="expired", phase="after-close" if now_min >= spec.close_min
                   else "after-cutoff",
                   reason=f"no close above {what} ₹{lvl:.2f} by {clock12(cutoff)}, your last "
                          "time for a new entry")
        return res
    res["phase"] = "open"
    last_close_min = spec.bar_close_min(last["slot"])
    if last["c"] > lvl and last in thin:
        av = avg_vol_at(last["slot"])
        res["reason"] = (f"the {last['start']} bar closed above at ₹{last['c']:.2f} but on "
                         f"{(last['v'] / av if av else 0):.2f}x the range's volume, "
                         f"under your {min_volume_x:.2f}x")
    elif last["c"] > lvl and last_close_min < before:
        res["reason"] = (f"the {last['start']} bar closed above at ₹{last['c']:.2f}, but no "
                         f"entries before {clock12(before)}")
    else:
        gap = lvl - last["c"]
        res["reason"] = (f"last close ₹{last['c']:.2f} ({last['start']} bar), ₹{abs(gap):.2f} "
                         f"{'under' if gap >= 0 else 'over'} {what} ₹{lvl:.2f} "
                         f"({100.0 * (last['c'] / lvl - 1):+.2f}%)")
    return res
