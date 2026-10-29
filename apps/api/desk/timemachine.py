# Ported from day-trader-skill (https://github.com/Jakeschincariol/day-trader-skill,
# commit 3d0f4ef), MIT License, Copyright (c) 2026 Jake Schincariol.
# Adapted for NSE (IST sessions, tick sizes, circuit bands, swing mode) by QuberTech.
"""Time Machine: replay a planned trade before risking a rupee.

Two modes:

**Intraday** (upstream's algorithm, same draws, same order): the pool is
every *full* past session's bar ``i`` as (o, h, l, c) ratios to bar ``i-1``'s
close. One replay starts at the entry and walks the rest of the session; for
bar ``i`` it draws a slot ``i ± window`` (kept in 1..n-1) and one past bar
from that slot, scaled to the current price. The stop is checked before the
target in every bar — a bar touching both counts as the stop. 10,000 replays
give target-first / stop-first / neither / green odds, a fan chart (a second
run of 2,000 paths, same seed) and a 24-bin histogram of the price at
``until``. The *history* re-runs the plan's opening-range rule on every one
of those past sessions — what really happened, not a simulation.

**Swing** (new for Drishti's analysis runs): the pool is every past *daily*
bar as ratios to the previous close; a replay walks ``horizon`` sessions.
The history re-runs the ``levels.py`` rule (swing-low structure stop with an
ATR buffer, ATR fallback, R-multiple target) on past sessions.

NSE additions to both: stops/targets round on NSE ticks, an optional circuit
band clamps simulated prices (and reports how often the band was hit), and
``p_gap_through_stop`` reports replays whose stop was hit by a bar that
*opened* beyond it (a real fill would be worse than −1R).

The verdict compares the target's share of the replays that hit a side with
the break-even share at the plan's target R, 1/(1+R). Simulated odds are not
predictions: earnings, news and halts can do what the pool never did.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from apps.api.desk.bars import Bar, Row, full_sessions, session_bars, split_sessions
from apps.api.desk.market import ticks
from apps.api.desk.market.session import NSE, SessionSpec, clock12, clock24, parse_clock

SAMPLE_PATHS = 160
BAND_PATHS = 2000
BAND_Q = (0.05, 0.25, 0.5, 0.75, 0.95)
HIST_BINS = 24
COIN_FLIP = 0.05
STRONG = 0.15
MANY_NEITHER = 0.40
MIN_SESSIONS = 20
MIN_SWING_POOL = 120

RoundFn = Callable[[float, bool], float]


class TMError(ValueError):
    """A plain-language refusal (bad plan, not enough data, entry not in data)."""


def nse_round(price: float, long: bool) -> float:
    return ticks.round_away(price, long)


def cents_round(price: float, long: bool) -> float:
    """Upstream's cents_away — only for port-correctness on US fixtures."""
    return ticks.round_away(price, long, tick=0.01)


# ---------------------------------------------------------------- the simulation

def build_pool(sessions: Sequence[Tuple[str, List[Bar]]], n_bars: int) -> List[List[tuple]]:
    """pool[i] = every past session's bar i as (o, h, l, c) / bar i-1's close."""
    pool: List[List[tuple]] = [[] for _ in range(n_bars)]
    for _, D in sessions:
        for i in range(1, n_bars):
            pc = D[i - 1]["c"]
            pool[i].append((D[i]["o"] / pc, D[i]["h"] / pc, D[i]["l"] / pc, D[i]["c"] / pc))
    return pool


def pct(a: Sequence[float], q: float) -> float:
    return a[min(len(a) - 1, int(q * len(a)))]


def simulate(pool, entry: float, stop: float, target: float, k: int, last: int,
             replays: int = 10000, seed: int = 19, window: int = 2, side: str = "buy",
             band: Optional[Tuple[float, float]] = None) -> Dict[str, Any]:
    """Monte Carlo replays of bars k+1..last. Same draws and order as upstream.

    ``band`` = (lower, upper) circuit limits in price; when given, simulated
    highs/lows/closes are clamped into it. Neither ``band`` nor the gap
    counter consumes random draws, so with ``band=None`` the numbers equal
    upstream's exactly.
    """
    long = side == "buy"
    risk = (entry - stop) if long else (stop - entry)
    r_target = round(((target - entry) if long else (entry - target)) / risk, 4)
    n_slots = len(pool)
    lo_b, hi_b = band if band else (-math.inf, math.inf)

    def slot(rng, i):
        return i if window == 0 else min(n_slots - 1, max(1, i + rng.randint(-window, window)))

    rng = random.Random(seed)
    choice = rng.choice
    outcomes, closes, paths = [], [], []
    gaps = circuit_hits = 0
    for n in range(replays):
        p = entry
        outcome = None
        keep = n < SAMPLE_PATHS
        path = [entry]
        hit_band = False
        for i in range(k + 1, last + 1):
            o, h, l, c = choice(pool[slot(rng, i)])
            op, hi, lo, cl = p * o, p * h, p * l, p * c
            if band:
                if hi > hi_b or lo < lo_b:
                    hit_band = True
                op, hi = min(max(op, lo_b), hi_b), min(hi, hi_b)
                lo, cl = max(lo, lo_b), min(max(cl, lo_b), hi_b)
            if outcome is None:
                if long:
                    if lo <= stop:
                        outcome = "stop"        # both inside one bar: count the loss
                        gaps += op <= stop
                    elif hi >= target:
                        outcome = "target"
                else:
                    if hi >= stop:
                        outcome = "stop"
                        gaps += op >= stop
                    elif lo <= target:
                        outcome = "target"
            p = cl
            if keep:
                path.append(round(p, 2))
        circuit_hits += hit_band
        outcomes.append(outcome or "close")
        closes.append(p)
        if keep:
            paths.append(path)
    cnt = {x: outcomes.count(x) for x in ("target", "stop", "close")}
    green = sum(1 for o, p in zip(outcomes, closes)
                if o == "target" or (o == "close" and (p > entry if long else p < entry)))
    Rs = [r_target if o == "target" else -1.0 if o == "stop"
          else ((p - entry) if long else (entry - p)) / risk
          for o, p in zip(outcomes, closes)]
    srt = sorted(closes)
    # Fan-chart bands: a second run of 2,000 paths, same seed (upstream).
    rng = random.Random(seed)
    choice = rng.choice
    allp = []
    for n in range(min(BAND_PATHS, max(replays, 1))):
        p = entry
        path = [entry]
        for i in range(k + 1, last + 1):
            o, h, l, c = choice(pool[slot(rng, i)])
            p = p * c
            if band:
                p = min(max(p, lo_b), hi_b)
            path.append(p)
        allp.append(path)
    bands = []
    for j in range(last - k + 1):
        col = sorted(pp[j] for pp in allp)
        bands.append([round(pct(col, q), 2) for q in BAND_Q])
    lo_, hi_ = pct(srt, 0.01), pct(srt, 0.99)
    W = (hi_ - lo_) / HIST_BINS
    bins = [0] * HIST_BINS
    for c in srt:
        b = int((c - lo_) / W) if W > 0 else 0
        if 0 <= b < HIST_BINS:
            bins[b] += 1
    return {
        "replays": replays, "seed": seed, "window": window, "bars_left": last - k,
        "p_target": round(cnt["target"] / replays, 4), "p_stop": round(cnt["stop"] / replays, 4),
        "p_neither": round(cnt["close"] / replays, 4), "p_green": round(green / replays, 4),
        "median_close": round(pct(srt, 0.5), 2), "p5_close": round(pct(srt, 0.05), 2),
        "p95_close": round(pct(srt, 0.95), 2), "avg_R": round(statistics.mean(Rs), 3),
        "r_at_target": r_target,
        "p_gap_through_stop": round(gaps / replays, 4),
        "p_circuit": round(circuit_hits / replays, 4) if band else None,
        "band_paths": len(allp), "bands": bands, "sample_paths": paths,
        "hist": {"lo": round(lo_, 2), "hi": round(hi_, 2), "bins": bins},
    }


# ---------------------------------------------------------------- verdict

def lean(mc: Dict[str, Any]) -> Optional[Tuple[float, float, float]]:
    """(gap, share, break_even) — the target's share of the replays that hit
    a side, minus the share that breaks even at the target's R."""
    pt, ps = mc["p_target"], mc["p_stop"]
    if pt + ps <= 0:
        return None
    r = mc.get("r_at_target") or 1.0
    share, even = pt / (pt + ps), 1.0 / (1.0 + r)
    return share - even, share, even


def verdict(mc: Dict[str, Any], until_label: str) -> str:
    pt, ps = mc["p_target"], mc["p_stop"]
    lv = lean(mc)
    if lv is None:
        return (f"No replay reached the target or the stop by {until_label}: "
                f"the price at {until_label} decides every one.")
    d, share, even = lv
    r = mc.get("r_at_target") or 1.0
    one_r = abs(r - 1.0) < 0.005
    if abs(d) < COIN_FLIP:
        head = "Close to a coin flip" if one_r else "Close to break-even"
    elif abs(d) < STRONG:
        head = "Leans toward the target" if d > 0 else "Leans toward the stop"
    else:
        head = "Strongly toward the target" if d > 0 else "Strongly toward the stop"
    odds = "target first %.1f%% vs stop first %.1f%%" % (100 * pt, 100 * ps)
    if one_r:
        return f"{head}: {odds}."
    return ("%s: at %gR the target has to come first in %.0f%% of the replays that hit one "
            "side; it did in %.0f%% (%s)." % (head, round(r, 2), 100 * even, 100 * share, odds))


def verdict_notes(mc: Dict[str, Any], until_label: str) -> List[str]:
    notes = []
    lv = lean(mc)
    pn = mc.get("p_neither", 0)
    if pn > MANY_NEITHER and lv is not None:
        notes.append("%.1f%% of the replays hit neither by %s; the price at %s decides those."
                     % (100 * pn, until_label, until_label))
    avg = mc.get("avg_R")
    if lv and abs(lv[0]) >= COIN_FLIP and avg and (avg > 0) != (lv[0] > 0):
        notes.append("Average R is %+.3f all the same: the replays that hit neither pull it "
                     "the other way." % avg)
    if mc.get("p_gap_through_stop", 0) >= 0.01:
        notes.append("%.1f%% of the replays hit the stop with a bar that opened beyond it — a "
                     "real fill there is worse than −1R." % (100 * mc["p_gap_through_stop"]))
    if mc.get("p_circuit"):
        notes.append("%.1f%% of the replays touched the circuit band; trading halts at the "
                     "band, so exits there may not fill." % (100 * mc["p_circuit"]))
    return notes


# ---------------------------------------------------------------- history (intraday ORB rule)

def opening_range(D, n: int) -> Tuple[float, float]:
    rows = [D[i] for i in range(n)]
    return max(b["h"] for b in rows), min(b["l"] for b in rows)


def rule_stop(rule, oh, ol, entry, long, rnd: RoundFn) -> float:
    r = rule["stop_rule"]
    if r == "or_mid":
        return rnd((oh + ol) / 2, long)
    if r in ("or_low", "or_high"):
        return rnd(ol if long else oh, long)
    frac = rule["stop_pct"] / 100.0
    return rnd(entry * (1 - frac) if long else entry * (1 + frac), long)


def history_replay(sessions, rule, last: int, side: str, spec: SessionSpec,
                   rnd: RoundFn) -> Dict[str, Any]:
    """The plan's ORB rule on every past full session: entry = the first bar
    after the opening range to close beyond it (up to ``last``), then stop
    first, then target, bar by bar, else the close at ``last``."""
    long = side == "buy"
    n = rule["or_minutes"] // spec.bar_min
    trades = []
    for d, D in sessions:
        oh, ol = opening_range(D, n)
        kk = next((i for i in range(n, last + 1)
                   if (D[i]["c"] > oh if long else D[i]["c"] < ol)), None)
        if kk is None:
            continue
        e = round(D[kk]["c"], 2)
        s = rule_stop(rule, oh, ol, e, long, rnd)
        risk = (e - s) if long else (s - e)
        if risk <= 0:
            continue
        t = round(e + rule["target_r"] * risk, 2) if long else round(e - rule["target_r"] * risk, 2)
        oc = None
        for b in D[kk + 1:last + 1]:
            if (b["l"] <= s) if long else (b["h"] >= s):
                oc = "stop"
                break
            if (b["h"] >= t) if long else (b["l"] <= t):
                oc = "target"
                break
        cl = D[last]["c"]
        R = (((t - e) if long else (e - t)) / risk if oc == "target" else -1.0 if oc == "stop"
             else ((cl - e) if long else (e - cl)) / risk)
        trades.append({"date": d, "time": clock12(spec.bar_close_min(kk)), "entry": e, "stop": s,
                       "target": t, "outcome": oc or "close", "R": round(R, 2)})
    return _history_summary(len(sessions), trades, rule)


def _history_summary(n_sessions: int, trades: List[dict], rule) -> Dict[str, Any]:
    return {
        "sessions": n_sessions, "triggered": len(trades),
        "target": sum(1 for x in trades if x["outcome"] == "target"),
        "stop": sum(1 for x in trades if x["outcome"] == "stop"),
        "close": sum(1 for x in trades if x["outcome"] == "close"),
        "green": sum(1 for x in trades if x["R"] > 0),
        "avg_R": round(statistics.mean(x["R"] for x in trades), 3) if trades else None,
        "rule": rule, "trades": trades,
    }


def resolve_rule(plan, entry_day, spec: SessionSpec, rnd: RoundFn) -> Optional[Dict[str, Any]]:
    """The rule the history uses: orb, the plan's opening range, its stop rule
    (or_mid / or_low / or_high, else 'pct' = the plan's stop distance as a
    percent of entry) and the plan's target R. 'auto' infers the stop rule
    from the plan day's own opening range. None when the setup isn't orb."""
    if plan["setup"].lower() != "orb":
        return None
    long = plan["side"] == "buy"
    n = plan["or_minutes"] // spec.bar_min
    rule = {"setup": "orb", "or_minutes": plan["or_minutes"], "stop_rule": plan["stop_rule"],
            "target_r": plan["target_r"],
            "stop_pct": round(100.0 * plan["risk_per_share"] / plan["entry"], 4)}
    far = "or_low" if long else "or_high"
    if rule["stop_rule"] == "auto":
        rule["stop_rule"] = "pct"
        if all(i in entry_day for i in range(n)):
            oh, ol = opening_range(entry_day, n)
            if abs(rnd((oh + ol) / 2, long) - plan["stop"]) < 0.005:
                rule["stop_rule"] = "or_mid"
            elif abs(rnd(ol if long else oh, long) - plan["stop"]) < 0.005:
                rule["stop_rule"] = far
    elif rule["stop_rule"] in ("or_low", "or_high") and rule["stop_rule"] != far:
        rule["stop_rule"] = "pct"
    elif rule["stop_rule"] not in ("or_mid", far):
        rule["stop_rule"] = "pct"
    if rule["stop_rule"] != "pct":
        del rule["stop_pct"]
    return rule


# ---------------------------------------------------------------- plans

def _num(v, what: str) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise TMError(f"the plan needs a number for {what}, got {v!r}") from None
    if not x > 0 or math.isinf(x):
        raise TMError(f"the plan's {what} must be a positive price, got {v!r}")
    return x


def _prices(raw: Dict[str, Any]) -> Tuple[str, float, float, float]:
    side = str(raw.get("side") or "buy").lower()
    if side in ("long",):
        side = "buy"
    if side in ("short",):
        side = "sell"
    if side not in ("buy", "sell"):
        raise TMError(f"side must be buy or sell, got {raw.get('side')!r}")
    entry, stop, target = (_num(raw.get(k), k) for k in ("entry", "stop", "target"))
    if side == "buy" and not stop < entry < target:
        raise TMError(f"a buy needs stop < entry < target (got stop {stop}, entry {entry}, "
                      f"target {target})")
    if side == "sell" and not target < entry < stop:
        raise TMError(f"a sell needs target < entry < stop (got target {target}, entry {entry}, "
                      f"stop {stop})")
    return side, entry, stop, target


def normalize_intraday_plan(raw: Dict[str, Any], spec: SessionSpec = NSE) -> Dict[str, Any]:
    """A plan dict -> the checked plan the intraday time machine runs."""
    sym = str(raw.get("symbol") or "").strip().upper()
    if not sym:
        raise TMError("the plan needs a symbol")
    side, entry, stop, target = _prices(raw)
    try:
        day = date.fromisoformat(str(raw.get("date") or "")).isoformat()
    except ValueError:
        raise TMError(f"the plan needs a date like 2026-10-28, got {raw.get('date')!r}") from None
    k = None
    if raw.get("entry_time"):
        try:
            k = spec.bar_for_close(parse_clock(raw["entry_time"]))
        except ValueError:
            raise TMError(f"entry_time {raw['entry_time']} is not a {spec.bar_min}-minute bar close "
                          f"(e.g. 09:35)") from None
    if raw.get("entry_bar") is not None:
        try:
            kb = int(raw["entry_bar"])
        except (TypeError, ValueError):
            raise TMError(f"entry_bar must be a bar number, got {raw['entry_bar']!r}") from None
        if k is not None and kb != k:
            raise TMError(f"the plan disagrees with itself: entry_bar {kb} but entry_time "
                          f"{raw['entry_time']} is bar {k}")
        k = kb
    if k is None:
        raise TMError("the plan needs entry_time (the entry bar's close, e.g. 09:35) or entry_bar")
    if not 0 <= k <= spec.n_bars - 2:
        raise TMError(f"the entry must close between {clock12(spec.bar_close_min(0))} and "
                      f"{clock12(spec.bar_close_min(spec.n_bars - 2))} to leave bars to replay")
    or_min = raw.get("or_minutes") or (raw.get("opening_range") or {}).get("minutes") or 15
    try:
        or_min = int(or_min)
    except (TypeError, ValueError):
        raise TMError("or_minutes must be a number") from None
    span = spec.n_bars * spec.bar_min
    if or_min < spec.bar_min or or_min % spec.bar_min or or_min >= span:
        raise TMError(f"or_minutes must be a multiple of {spec.bar_min} (got {or_min})")
    long = side == "buy"
    risk = (entry - stop) if long else (stop - entry)
    return {
        "symbol": sym, "side": side, "date": day, "setup": str(raw.get("setup") or "orb"),
        "entry": entry, "entry_time": clock24(spec.bar_close_min(k)), "entry_bar": k,
        "stop": stop, "target": target, "risk_per_share": round(risk, 2),
        "target_r": round(((target - entry) if long else (entry - target)) / risk, 2),
        "or_minutes": or_min, "stop_rule": str(raw.get("stop_rule") or "auto"),
    }


def plan_key(mode: str, plan: Dict[str, Any], data_as_of: Optional[str], **kw) -> str:
    blob = json.dumps({"mode": mode, "plan": plan, "as_of": data_as_of, **kw}, sort_keys=True,
                      default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


# ---------------------------------------------------------------- intraday run

def run_intraday(rows: Sequence[Row], plan: Dict[str, Any], *, spec: SessionSpec = NSE,
                 now: Optional[datetime] = None, replays: int = 10000, seed: int = 19,
                 window: int = 2, until: Optional[str] = None,
                 circuit_pct: Optional[float] = None, rnd: RoundFn = nse_round,
                 source: str = "delayed:yahoo") -> Dict[str, Any]:
    """Raw 5m rows + a normalized intraday plan -> the result document."""
    if not 1 <= replays <= 100000:
        raise TMError("replays must be between 1 and 100,000")
    if not 0 <= window <= 10:
        raise TMError("window must be between 0 and 10 bars")
    now = now or datetime.now(timezone.utc)
    sym = plan["symbol"]
    bars, dropped = session_bars(rows, spec, now)
    if not bars:
        raise TMError(f"no complete {spec.bar_min}-minute bars for {sym} in the data")
    k = plan["entry_bar"]
    until_min = spec.close_min if until is None else parse_clock(until)
    if (until_min - spec.open_min) % spec.bar_min or until_min > spec.close_min \
            or until_min <= spec.open_min:
        raise TMError(f"until must be on the {spec.bar_min}-minute grid and no later than "
                      f"{clock12(spec.close_min)}")
    last = (until_min - spec.open_min) // spec.bar_min - 1
    if last <= k:
        raise TMError(f"until {clock12(until_min)} leaves nothing to replay after the "
                      f"{plan['entry_time']} entry")
    days = split_sessions(bars)
    entry_day = days.get(plan["date"], {})
    if k not in entry_day:
        reason = ("no bars for that day" if not entry_day else
                  "the data stops at the %s bar" % entry_day[max(entry_day)]["start"])
        raise TMError(f"the entry is not in the data: no complete "
                      f"{clock24(spec.bar_start_min(k))} bar (closing {plan['entry_time']}) on "
                      f"{plan['date']} for {sym} ({reason})")
    eb = entry_day[k]
    warn = []
    if not eb["l"] - 0.01 <= plan["entry"] <= eb["h"] + 0.01:
        warn.append("the plan's entry %.2f is outside that bar's range %.2f to %.2f"
                    % (plan["entry"], eb["l"], eb["h"]))
    sessions, short = full_sessions(days, plan["date"], spec, through=last)
    if len(sessions) < MIN_SESSIONS:
        hint = ""
        if spec.bar_min == 1:
            hint = (". Yahoo serves only about 6 days of 1-minute bars; Drishti keeps what it "
                    "fetches, so 1-minute history grows day by day. Connect Kite for deeper "
                    "1-minute history now, or replay this plan on 5-minute bars")
        raise TMError(f"only {len(sessions)} full session(s) of {sym} before {plan['date']} in "
                      f"the data through {clock24(until_min)}; the time machine needs at least {MIN_SESSIONS}{hint}")
    band = None
    band_reference = None
    if circuit_pct:
        prior_date = max(d for d in days if d < plan["date"])
        prev_day = days[prior_date]
        last_bar = prev_day[max(prev_day)]
        prev_close = last_bar["c"]
        band_reference = {"date": prior_date, "bar": last_bar["start"], "close": prev_close,
                          "kind": "last_available_intraday_close"}
        warn.append(f"The simulated circuit band uses the last available intraday close "
                    f"on {prior_date} at {last_bar['start']}; it may differ from the official auction close.")
        band = (prev_close * (1 - circuit_pct / 100), prev_close * (1 + circuit_pct / 100))
    pool = build_pool(sessions, last + 1)
    mc = simulate(pool, plan["entry"], plan["stop"], plan["target"], k, last, replays, seed,
                  window, plan["side"], band)
    until_label = clock12(until_min)
    if band_reference is not None:
        mc["circuit_reference"] = band_reference
    mc.update(until=clock24(until_min), pool_sessions=len(sessions), pool_from=sessions[0][0],
              pool_to=sessions[-1][0],
              band_times=[clock24(spec.bar_close_min(j)) for j in range(k, last + 1)])
    rule = resolve_rule(plan, entry_day, spec, rnd)
    plan_out = dict(plan)
    if rule:
        plan_out["stop_rule"] = rule["stop_rule"]
        history = history_replay(sessions, rule, last, plan["side"], spec, rnd)
    else:
        history = {"sessions": len(sessions), "triggered": 0, "rule": None, "trades": [],
                   "note": "skipped: the history replay knows the opening-range breakout (orb) "
                           f"only; this plan's setup is {plan['setup']!r}"}
    last_bar = bars[-1]
    return {
        "mode": "intraday",
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "data": {"symbol": sym, "interval": f"{spec.bar_min}m", "session": spec.name,
                 "last_bar": f"{last_bar['date']} {last_bar['start']}",
                 "dropped": dropped, "short_sessions_left_out": short},
        "plan": plan_out, "mc": mc, "history": history,
        "verdict": verdict(mc, until_label), "verdict_notes": verdict_notes(mc, until_label),
        "warnings": warn,
        "note": (f"A simulation built from {sym}'s own last {len(sessions)} complete session windows "
                 f"through {clock24(until_min)}. It is "
                 "not a prediction: results, news and circuit halts can do things those "
                 "sessions never did. Same seed, same numbers."),
    }


# ---------------------------------------------------------------- swing mode

def _wilder_atr(bars: Sequence[Bar], n: int = 14) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(bars)
    atr = None
    for i in range(1, len(bars)):
        b, pc = bars[i], bars[i - 1]["c"]
        tr = max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc))
        atr = tr if atr is None else atr + (tr - atr) / n
        if i >= n:
            out[i] = atr
    return out


def levels_rule_stop(bars: Sequence[Bar], t: int, atr: float) -> Optional[float]:
    """levels.py's stop as of bar t: swing low less an ATR buffer, ATR
    fallback when the structure is too far. Same constants, imported."""
    from apps.api.integrations.levels import (
        ATR_STOP_MULTIPLE,
        MAX_STRUCTURE_ATR,
        STOP_BUFFER_ATR,
        SWING_LOOKBACK,
    )
    entry = bars[t]["c"]
    swing_low = min(b["l"] for b in bars[max(0, t - SWING_LOOKBACK + 1):t + 1])
    structure = swing_low - STOP_BUFFER_ATR * atr
    dist = entry - structure
    if dist > MAX_STRUCTURE_ATR * atr or dist <= 0:
        return entry - ATR_STOP_MULTIPLE * atr
    return structure


def swing_history(bars: Sequence[Bar], horizon: int, target_r: float, lookback: int = 250,
                  rnd: RoundFn = nse_round) -> Dict[str, Any]:
    """The levels.py rule entered at the close of each of the last
    ``lookback`` sessions that have ``horizon`` sessions after them."""
    atr = _wilder_atr(bars)
    n = len(bars)
    start = max(60, n - horizon - lookback)
    trades = []
    for t in range(start, n - horizon):
        if atr[t] is None or atr[t] <= 0:
            continue
        e = bars[t]["c"]
        s = levels_rule_stop(bars, t, atr[t])
        if s is None or s >= e:
            continue
        s = rnd(s, True)
        risk = e - s
        tgt = round(e + target_r * risk, 2)
        oc, gap = None, False
        for b in bars[t + 1:t + horizon + 1]:
            if b["l"] <= s:
                oc, gap = "stop", b["o"] <= s
                break
            if b["h"] >= tgt:
                oc = "target"
                break
        cl = bars[t + horizon]["c"]
        R = target_r if oc == "target" else -1.0 if oc == "stop" else (cl - e) / risk
        trades.append({"date": bars[t]["date"], "entry": round(e, 2), "stop": s, "target": tgt,
                       "outcome": oc or "close", "R": round(R, 2), "gap": gap})
    rule = {"setup": "levels", "target_r": target_r, "horizon": horizon}
    out = _history_summary(len(range(start, n - horizon)), trades, rule)
    out["gap_through_stop"] = sum(1 for x in trades if x["gap"])
    return out


def run_swing(rows_daily: Sequence[Row], raw_plan: Dict[str, Any], *, horizon: int = 10,
              now: Optional[datetime] = None, replays: int = 10000, seed: int = 19,
              circuit_pct: Optional[float] = None, rnd: RoundFn = nse_round,
              source: str = "delayed:yahoo", spec: SessionSpec = NSE) -> Dict[str, Any]:
    """Daily rows + {symbol, date, entry, stop, target} -> the result document.

    Entry is assumed at the close of ``date``; each replay walks ``horizon``
    sessions of resampled daily bars.
    """
    from apps.api.desk.bars import daily_bars

    if not 1 <= horizon <= 60:
        raise TMError("horizon must be between 1 and 60 sessions")
    if not 1 <= replays <= 100000:
        raise TMError("replays must be between 1 and 100,000")
    now = now or datetime.now(timezone.utc)
    side, entry, stop, target = _prices(raw_plan)
    if side != "buy":
        raise TMError("swing mode is long-only (like the computed levels)")
    try:
        day = date.fromisoformat(str(raw_plan.get("date") or "")).isoformat()
    except ValueError:
        raise TMError("the plan needs a date like 2026-10-28") from None
    sym = str(raw_plan.get("symbol") or "").upper()
    every = daily_bars(rows_daily, spec)
    upto = [b for b in every if b["date"] <= day]
    if not upto or upto[-1]["date"] != day:
        # The analysis date can be a non-trading day: use the last session on or before it.
        if not upto:
            raise TMError(f"no daily bars for {sym} on or before {day}")
    pool_bars = upto[-501:]
    ratios = [(b["o"] / a["c"], b["h"] / a["c"], b["l"] / a["c"], b["c"] / a["c"])
              for a, b in zip(pool_bars, pool_bars[1:])]
    if len(ratios) < MIN_SWING_POOL:
        raise TMError(f"only {len(ratios)} daily bars of {sym} before {day}; swing mode needs "
                      f"at least {MIN_SWING_POOL}")
    pool = [ratios] * (horizon + 1)
    band = None
    if circuit_pct:
        # A daily band compounds; clamp the whole horizon to N × band as a sanity bound.
        band = (entry * (1 - circuit_pct / 100) ** horizon, entry * (1 + circuit_pct / 100) ** horizon)
    mc = simulate(pool, entry, stop, target, 0, horizon, replays, seed, 0, "buy", band)
    until_label = f"session {horizon}"
    mc.update(until=until_label, pool_sessions=len(ratios), pool_from=pool_bars[0]["date"],
              pool_to=pool_bars[-1]["date"], band_times=[f"D{j}" for j in range(horizon + 1)])
    target_r = mc["r_at_target"]
    history = swing_history(upto, horizon, round(target_r, 2), rnd=rnd)
    plan_out = {"symbol": sym, "side": "buy", "date": upto[-1]["date"], "entry": entry,
                "stop": stop, "target": target, "risk_per_share": round(entry - stop, 2),
                "target_r": round(target_r, 2), "horizon": horizon}
    return {
        "mode": "swing",
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "data": {"symbol": sym, "interval": "1d", "last_bar": upto[-1]["date"]},
        "plan": plan_out, "mc": mc, "history": history,
        "verdict": verdict(mc, f"{horizon} sessions"),
        "verdict_notes": verdict_notes(mc, f"{horizon} sessions"),
        "warnings": [] if upto[-1]["date"] == day else
        [f"{day} has no daily bar; used the last session before it, {upto[-1]['date']}"],
        "note": (f"A simulation built from {sym}'s own last {len(ratios)} daily bars, resampled "
                 "independently day by day (no trend or volatility clustering). Not a "
                 "prediction. Same seed, same numbers."),
    }
