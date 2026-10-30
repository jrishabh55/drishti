# Ported from day-trader-skill (https://github.com/Jakeschincariol/day-trader-skill,
# commit 3d0f4ef), MIT License, Copyright (c) 2026 Jake Schincariol.
# Adapted for NSE (₹, IST window, square-off, circuit bands, ASM/GSM, F&O ban,
# charges-aware reward:risk; PDT removed) by QuberTech.
"""Risk check: PASS or BLOCK per order, with the reason and the fix.

Orders are checked in list order *as if each passed order had filled*, so a
batch that is fine order by order but breaks a limit together is caught.

An ENTRY opens or adds to a position; an EXIT only reduces what you hold.
Getting out must always be possible: an exit is checked for format, asset
and short sales only — every other rule turns into a warning for an exit,
never a BLOCK.

Checks (entries): format · asset (EQ series only; BE/BZ trade-for-trade can't
be traded intraday; no derivatives) · short · blocked · size · cash (no
leverage) · position cap · stop (required, on the right side) · loss ·
reward:risk (gross AND net of charges) · daily loss · trades/day · window
(IST, trading days) · square-off (MIS entries only before flat-by, and
flat-by before the broker's auto square-off) · circuit (target beyond the
band) · surveillance (ASM/GSM) · F&O ban (warn) · results this week (warn) ·
duplicate · daily order limit.

Market facts that come from NSE feeds (bands, ASM/GSM, ban list, board
meetings) are optional inputs. When one is missing the check reports
"skipped: data unavailable" — it never silently passes.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from apps.api.desk.market import charges as charges_mod
from apps.api.desk.market.session import NSE, SessionSpec, clock24, parse_clock
from apps.api.desk.profile import DeskProfile

ORDER_TYPES = ("LIMIT", "MARKET", "SL", "SL-M")
PRODUCTS = ("MIS", "CNC")
PAISA = 0.005
FINGERPRINT_FIELDS = ("symbol", "exchange", "side", "qty", "order_type", "product", "validity",
                      "price", "trigger_price", "stop", "target", "portfolio_id")
_DERIVATIVE = re.compile(r"\d{2}[A-Z]{3}.*(FUT|CE|PE)$")
_SYMBOL = re.compile(r"^[A-Z0-9&\-]{1,20}$")
TRADE_FOR_TRADE = {"BE", "BZ", "BT", "IL", "SM", "ST"}


def fingerprint(order: Dict[str, Any]) -> str:
    """The exact order that was checked. Tickets match verdicts by this."""
    core = {k: order[k] for k in FINGERPRINT_FIELDS if order.get(k) is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _f(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


#: ``_available`` keys from desk/datasets.nse_facts and how a warning names them.
_FACT_LABELS = (("bands", "circuit band"), ("asm", "ASM list"), ("gsm", "GSM list"),
                ("fo_ban", "F&O ban list"), ("results", "results calendar"))


def window_reasons(now_local: datetime, profile: DeskProfile, spec: SessionSpec = NSE
                   ) -> List[str]:
    d = now_local.date()
    t = now_local.hour * 60 + now_local.minute
    stamp = now_local.strftime("%a %Y-%m-%d %H:%M")
    if not spec.is_trading_day(d):
        return [f"window: NSE is closed today ({stamp} IST)"]
    if t < spec.open_min or t >= spec.close_min:
        return [f"window: the market is closed ({stamp} IST; the session is 09:15 to 15:30)"]
    out = []
    if profile.no_entries_before and t < parse_clock(profile.no_entries_before):
        out.append(f"window: no new entries before {profile.no_entries_before} IST (it is "
                   f"{clock24(t)})")
    if profile.no_entries_after and t > parse_clock(profile.no_entries_after):
        out.append(f"window: no new entries after {profile.no_entries_after} IST (it is "
                   f"{clock24(t)})")
    return out


def check(orders: List[Dict[str, Any]], profile: DeskProfile, *,
          now: datetime, cash: Optional[float] = None,
          holdings: Optional[Dict[str, float]] = None,
          holding_values: Optional[Dict[str, float]] = None,
          today_entries: Optional[List[Dict[str, Any]]] = None,
          today_orders: int = 0,
          prices: Optional[Dict[str, float]] = None,
          market: Optional[Dict[str, Dict[str, Any]]] = None,
          spec: SessionSpec = NSE) -> Dict[str, Any]:
    """Pure. ``today_entries`` = entries already placed today
    [{symbol, side, max_loss}], ``prices`` = fresh last prices,
    ``market`` = {SYM: {band_lower, band_upper, band_pct, asm, gsm, fo_ban,
    series, results_date}} from NSE feeds when available."""
    now_local = now.astimezone(spec.tz)
    today = now_local.date().isoformat()
    holdings = dict(holdings or {})
    values = dict(holding_values or {})
    cash_left = profile.capital if cash is None else cash
    total = cash_left + sum(values.values())
    entries_today = list(today_entries or [])
    loss_today = sum(_f(e.get("max_loss")) or 0.0 for e in entries_today)
    placed = {(str(e.get("symbol", "")).upper(), e.get("side")) for e in entries_today}
    win = window_reasons(now_local, profile, spec)
    top_warn: List[str] = []
    market = market or {}
    if not market:
        top_warn.append("Circuit band, ASM/GSM, F&O ban and results-week checks were skipped: "
                        "NSE data unavailable (they never silently pass).")
    brk = profile.brokerage()
    seen = set()
    results = []
    batch_entries, batch_loss = 0, 0.0

    for i, o in enumerate(orders, 1):
        reasons: List[str] = []
        warns: List[str] = []
        info: List[str] = []
        later: List[str] = []
        sym = str(o.get("symbol", "")).strip().upper()
        side = str(o.get("side", "")).strip().lower()
        typ = str(o.get("order_type", "LIMIT")).strip().upper()
        product = str(o.get("product", profile.product)).strip().upper()
        qty = o.get("qty")
        plan = o.get("plan") if isinstance(o.get("plan"), dict) else {}
        q = None

        # format
        if not sym:
            reasons.append("format: no symbol")
        if side not in ("buy", "sell"):
            reasons.append(f"format: side must be buy or sell, got {o.get('side')!r}")
        if isinstance(qty, bool) or not isinstance(qty, (int, float)) or qty <= 0 \
                or abs(qty - round(qty)) > 1e-9:
            reasons.append(f"format: qty must be a whole number of shares, got {qty!r}")
        else:
            q = int(round(qty))
        if typ not in ORDER_TYPES:
            reasons.append(f"format: order type must be one of {', '.join(ORDER_TYPES)}")
        if product not in PRODUCTS:
            reasons.append("format: product must be MIS (intraday) or CNC (delivery)")
        if typ in ("LIMIT", "SL") and not (_f(o.get("price")) or 0) > 0:
            reasons.append(f"format: a {typ} order needs a price")
        if typ in ("SL", "SL-M") and not (_f(o.get("trigger_price")) or 0) > 0:
            reasons.append(f"format: a {typ} order needs a trigger price")
        if str(o.get("exchange", "NSE")).upper() != "NSE":
            reasons.append("format: the desk trades NSE cash equity only")

        # asset
        series = str(o.get("series") or (market.get(sym) or {}).get("series") or "EQ").upper()
        if sym and (_DERIVATIVE.search(sym) or not _SYMBOL.match(sym)):
            reasons.append(f"asset: {sym} is not an NSE equity symbol; the desk trades cash "
                           "equity only (no futures or options)")
        elif series in TRADE_FOR_TRADE and product == "MIS":
            reasons.append(f"asset: {sym} trades in the {series} series (trade-for-trade): it "
                           "can't be bought and sold the same day")

        # reference price
        plan_entry = _f(plan.get("entry"))
        live = (prices or {}).get(sym)
        if typ in ("LIMIT", "SL"):
            ref, ref_from = _f(o.get("price")), "limit"
        else:
            cands = [(p, w) for p, w in ((plan_entry, "plan"), (live, "price")) if p]
            ref, ref_from = (max(cands, key=lambda x: x[0]) if cands else (None, None))
            if live and plan_entry and live > plan_entry + 0.01:
                info.append(f"{sym} last ₹{live:.2f} is above the plan's entry ₹{plan_entry:.2f}: "
                            f"the risk is measured from ₹{live:.2f}")
        value = q * ref if (q and ref) else None

        # entry or exit
        held = holdings.get(sym, 0.0)
        exiting = side == "sell" and held > 0 and q is not None and q <= held
        kind = "exit" if exiting else "entry"
        if kind == "entry" and value is None and q:
            later.append(f"price: no price for {sym} to size the order")

        if sym in set(profile.blocked):
            later.append(f"blocked: {sym} is on your blocked list")
        if value is not None:
            if value < profile.min_order_inr - PAISA:
                later.append(f"size: ₹{value:,.2f} is under your ₹{profile.min_order_inr:,.0f} "
                             "minimum order")
            if value > profile.max_order_inr + PAISA:
                later.append(f"size: ₹{value:,.2f} is over your ₹{profile.max_order_inr:,.0f} "
                             "max order")
        buffer = total * profile.cash_buffer_pct / 100
        if value is not None and side == "buy" and kind == "entry":
            after = cash_left - value
            if after < -PAISA:
                later.append(f"cash: needs ₹{value:,.2f} but only ₹{cash_left:,.2f} is free; "
                             "that would be leverage, which is off")
            elif after < buffer - PAISA:
                later.append(f"cash: leaves ₹{max(after, 0):,.2f}, under your "
                             f"{profile.cash_buffer_pct:g}% buffer of ₹{buffer:,.2f}")
            pos_after = values.get(sym, 0.0) + value
            cap = total * profile.max_position_pct / 100
            if total > 0 and pos_after > cap + PAISA:
                later.append(f"position: {sym} would be ₹{pos_after:,.2f}, "
                             f"{100 * pos_after / total:.1f}% of the account, over your "
                             f"{profile.max_position_pct:g}% cap")
        if side == "sell" and kind == "entry" and not profile.allow_short:
            reasons.append(f"short: selling more {sym} than you hold ({held:g} shares); short "
                           "selling is off")

        max_loss = risk_ps = rr = rr_net = stop = target = None
        if kind == "entry" and side == "buy":
            stop = _f(o.get("stop")) or _f(plan.get("stop"))
            target = _f(o.get("target")) or _f(plan.get("target"))
            if not stop:
                if profile.require_stop:
                    later.append("stop: no stop. Every entry needs one (require_stop is on)")
                else:
                    warns.append("no stop: the loss isn't capped, so it isn't counted in the "
                                 "daily loss")
            elif ref:
                if stop >= ref - 0.01:
                    later.append(f"stop: the stop ₹{stop:.2f} must be below the entry ₹{ref:.2f}")
                else:
                    risk_ps = round(ref - stop, 4)
                    max_loss = round((q or 0) * risk_ps, 2)
                    if max_loss > profile.max_loss_per_trade_inr + PAISA:
                        later.append(f"loss: {q} shares × ₹{risk_ps:.2f} risk = ₹{max_loss:,.2f} "
                                     f"at the stop, over your ₹{profile.max_loss_per_trade_inr:,.0f}"
                                     " max loss per trade")
                    if target:
                        if target <= ref + 0.01:
                            later.append(f"target: the target ₹{target:.2f} must be above the "
                                         f"entry ₹{ref:.2f}")
                        else:
                            rr = round((target - ref) / risk_ps, 2)
                            c_t = charges_mod.round_trip_cost(ref, target, q or 0, product, brk)
                            c_s = charges_mod.round_trip_cost(ref, stop, q or 0, product, brk)
                            if q and max_loss:
                                rr_net = round(((target - ref) * q - c_t) / (max_loss + c_s), 2)
                            if profile.min_reward_risk and (rr_net if rr_net is not None else rr) \
                                    < profile.min_reward_risk - 1e-9:
                                later.append(f"reward:risk: {rr_net if rr_net is not None else rr:.2f} "
                                             f"after charges (gross {rr:.2f}) is under your "
                                             f"minimum {profile.min_reward_risk:g}")
                    elif profile.min_reward_risk:
                        later.append("reward:risk: no target to measure it against")
                    # circuit headroom
                    m = market.get(sym) or {}
                    upper = _f(m.get("band_upper"))
                    if upper and target and target > upper + PAISA:
                        later.append(f"circuit: the target ₹{target:.2f} is beyond today's upper "
                                     f"circuit ₹{upper:.2f}; it can't fill there")
                    band_pct = _f(m.get("band_pct"))
                    if not upper and target and band_pct is not None:
                        warns.append(f"circuit headroom skipped for {sym}: no previous close or published upper circuit price")
                    if band_pct is not None and band_pct <= 5:
                        warns.append(f"circuit: {sym} has a {band_pct:g}% price band — it can "
                                     "freeze at the limit and trap an exit")
            used = loss_today + batch_loss
            if max_loss is not None and used + max_loss > profile.max_daily_loss_inr + PAISA:
                later.append(f"daily loss: ₹{used:,.2f} already at risk today plus "
                             f"₹{max_loss:,.2f} = ₹{used + max_loss:,.2f}, over your "
                             f"₹{profile.max_daily_loss_inr:,.0f} daily loss limit")
            if len(entries_today) + batch_entries >= profile.max_trades_per_day:
                later.append(f"trades: {len(entries_today)} entries today plus {batch_entries} "
                             f"passed in this batch; your limit is {profile.max_trades_per_day}")
            later.extend(win)
            # square-off
            if product == "MIS":
                flat_by = plan.get("flat_by") or profile.flat_by
                if parse_clock(flat_by) >= parse_clock(profile.broker_square_off):
                    later.append(f"square-off: flat-by {flat_by} is not before your broker's "
                                 f"{profile.broker_square_off} auto square-off")
                t = now_local.hour * 60 + now_local.minute
                if t >= parse_clock(flat_by):
                    later.append(f"square-off: it's past your {flat_by} flat-by — an intraday "
                                 "entry now has no time to work")
                plan_date = plan.get("date")
                if plan_date and plan_date != today:
                    later.append(f"plan: this intraday plan is for {plan_date}, not today "
                                 f"({today})")
            # surveillance and other NSE facts
            m = market.get(sym)
            avail = (m or {}).get("_available")
            if avail is not None:
                # a list that couldn't be read is a skipped check, never a clean one
                skipped = [label for key, label in _FACT_LABELS if not avail.get(key)]
                if skipped:
                    warns.append(f"NSE checks skipped for {sym}: {', '.join(skipped)} "
                                 "unavailable (not treated as clean)")
            if m:
                if m.get("asm") or m.get("gsm"):
                    stage = m.get("asm") or m.get("gsm")
                    later.append(f"surveillance: {sym} is on NSE's "
                                 f"{'ASM' if m.get('asm') else 'GSM'} list ({stage}); extra "
                                 "margins and trade-for-trade rules apply — skip it")
                if m.get("fo_ban"):
                    warns.append(f"F&O ban: {sym} is in the F&O ban period (cash trading is "
                                 "allowed, but positioning is crowded)")
                if m.get("results_date"):
                    warns.append(f"results: {sym} has a board meeting for results on "
                                 f"{m['results_date']}")
            elif market:
                warns.append(f"NSE checks skipped for {sym}: no feed data for it")
            if stop and ref and risk_ps:
                info.append(f"entry ₹{ref:.2f} ({ref_from}), stop ₹{stop:.2f}, target "
                            f"{'₹%.2f' % target if target else 'none'}: risk ₹{risk_ps:.2f} a share")
                info.append(f"max loss ₹{max_loss:,.2f} (limit ₹{profile.max_loss_per_trade_inr:,.0f}),"
                            f" reward:risk {rr if rr is not None else 'n/a'}"
                            f"{' (%.2f after charges)' % rr_net if rr_net is not None else ''}")
            info.append(f"today before this: {len(entries_today) + batch_entries} of "
                        f"{profile.max_trades_per_day} trades, ₹{loss_today + batch_loss:,.2f} of "
                        f"₹{profile.max_daily_loss_inr:,.0f} daily loss")

        key = (sym, side)
        if key in seen:
            later.append(f"duplicate: a second {side} {sym} in this batch")
        if key in placed:
            later.append(f"duplicate: a {side} {sym} was already placed today")
        passed = sum(1 for r in results if r["verdict"] == "PASS")
        if today_orders + passed >= profile.max_orders_per_day:
            later.append(f"daily limit: {today_orders} orders today plus {passed} passed in this "
                         f"batch; your limit is {profile.max_orders_per_day}")
        seen.add(key)

        if kind == "entry":
            reasons.extend(later)
        else:
            warns.extend("exit, not blocked: " + x for x in later)
        verdict = "BLOCK" if reasons else "PASS"
        if verdict == "PASS":
            if kind == "entry":
                batch_entries += 1
                batch_loss += max_loss or 0.0
            if value is not None and q:
                if side == "buy":
                    cash_left -= value
                    values[sym] = values.get(sym, 0.0) + value
                    holdings[sym] = held + q
                else:
                    cash_left += value
                    values[sym] = max(values.get(sym, 0.0) - value, 0.0)
                    holdings[sym] = held - q
        results.append({
            "index": i, "symbol": sym, "side": side, "kind": kind, "qty": q,
            "value_inr": round(value, 2) if value is not None else None,
            "entry_ref": round(ref, 2) if ref else None, "entry_ref_from": ref_from,
            "stop": stop, "target": target, "risk_per_share": risk_ps, "max_loss": max_loss,
            "reward_risk": rr, "reward_risk_net": rr_net, "fingerprint": fingerprint(o),
            "verdict": verdict, "reasons": reasons, "warnings": warns, "info": info,
        })
    return {
        "date": today, "now_ist": now_local.strftime("%Y-%m-%d %H:%M"),
        "results": results,
        "pass": sum(1 for r in results if r["verdict"] == "PASS"),
        "block": sum(1 for r in results if r["verdict"] == "BLOCK"),
        "warnings": top_warn, "cash_after": round(cash_left, 2),
        "today": {"orders_placed": today_orders, "entries_placed": len(entries_today),
                  "loss_at_risk": round(loss_today, 2)},
        "note": "A PASS is a rules check against your own limits, not a recommendation.",
    }
