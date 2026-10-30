"""Paper performance: equity, drawdown, per-day results and, where a Time
Machine replay of the same plan exists, its odds next to what happened.

Read-only over one of the user's own paper portfolios (``default`` unless named). Expectations are only ever the
stored replay's numbers; a trade without one says so instead of guessing.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

# Paper exit reasons as the Time Machine's three outcomes.
_OUTCOME = {"target": "target", "stop": "stop"}


def _same_plan(tm_plan: Dict[str, Any], plan: Dict[str, Any]) -> bool:
    return (str(tm_plan.get("symbol", "")).upper() == str(plan.get("symbol", "")).upper()
            and tm_plan.get("date") == plan.get("date")
            and tm_plan.get("interval", "5m") == plan.get("interval", "5m")
            and tm_plan.get("side", "buy") == plan.get("side", "buy")
            and tm_plan.get("entry_time") == plan.get("entry_time")
            and all(abs(float(tm_plan.get(k) or 0) - float(plan.get(k) or 0)) < 1e-6
                    for k in ("entry", "stop", "target")))


def _replays(store, user_id: str) -> List[Dict[str, Any]]:
    with store._conn() as c:
        rows = c.execute("SELECT doc FROM desk_tm_results WHERE user_id=? ORDER BY created_at DESC",
                         (user_id,)).fetchall()
    docs = [json.loads(r["doc"]) for r in rows]
    return [d for d in docs if d.get("mode") == "intraday" and d.get("plan") and d.get("mc")]


def _expectation(store, user_id: str, trade: Dict[str, Any], replays) -> Optional[Dict[str, Any]]:
    ticket = store.get_ticket(trade.get("ticket_id") or "", user_id)
    plan = store.get_plan(ticket["plan_id"], user_id) if ticket and ticket.get("plan_id") else None
    if not plan:
        return None
    tm = next((d for d in replays if _same_plan(d["plan"], plan)
               and (not plan.get("flat_by") or d["mc"].get("until") == plan["flat_by"])), None)
    if tm is None:
        return None
    mc = tm["mc"]
    return {"plan_id": plan["id"], "replays": mc.get("replays"), "p_target": mc.get("p_target"),
            "p_stop": mc.get("p_stop"), "p_neither": mc.get("p_neither"), "avg_r": mc.get("avg_R"),
            "data_as_of": tm.get("data_as_of")}


def performance(user_id: str, store=None, portfolio_id: str = "default") -> Dict[str, Any]:
    from apps.api.desk import paper, portfolios
    from apps.api.desk.store import get_desk_store

    store = store or get_desk_store()
    capital = portfolios.profile_for(user_id, portfolio_id, store).capital
    trades = store.list_paper(user_id, limit=10_000, portfolio_id=portfolio_id)
    closed = sorted((t for t in trades if t.get("status") == "closed" and t.get("pnl_net") is not None),
                    key=lambda t: (t["trade_date"], t["exit_fill"]["time"]))

    equity, peak, curve = capital, capital, [{"at": None, "equity": capital, "drawdown": 0.0,
                                              "drawdown_pct": 0.0}]
    days: Dict[str, Dict[str, Any]] = {}
    for t in closed:
        equity += t["pnl_net"]
        peak = max(peak, equity)
        curve.append({"at": f"{t['trade_date']} {t['exit_fill']['time']}", "equity": round(equity, 2),
                      "drawdown": round(peak - equity, 2),
                      "drawdown_pct": round((peak - equity) / peak * 100, 3) if peak > 0 else None})
        d = days.setdefault(t["trade_date"], {"date": t["trade_date"], "trades": 0, "wins": 0,
                                              "pnl_net": 0.0, "charges": 0.0, "r_net": 0.0})
        d["trades"] += 1
        d["wins"] += t["pnl_net"] > 0
        d["pnl_net"] = round(d["pnl_net"] + t["pnl_net"], 2)
        d["charges"] = round(d["charges"] + (t.get("charges") or 0), 2)
        d["r_net"] = round(d["r_net"] + (t.get("r_net") or 0), 2)

    replays = _replays(store, user_id)
    comparisons = []
    for t in closed:
        exp = _expectation(store, user_id, t, replays)
        comparisons.append({
            "trade_id": t["id"], "symbol": t["symbol"], "trade_date": t["trade_date"],
            "actual": {"outcome": _OUTCOME.get(t["exit_fill"]["reason"], "neither"),
                       "exit_reason": t["exit_fill"]["reason"], "r_gross": t.get("r_gross"),
                       "r_net": t.get("r_net")},
            "expected": exp,
        })
    matched = [c for c in comparisons if c["expected"]]
    calibration = None
    if matched:
        n = len(matched)
        r_pairs = [(c["expected"]["avg_r"], c["actual"]["r_gross"]) for c in matched
                   if c["expected"]["avg_r"] is not None and c["actual"]["r_gross"] is not None]
        calibration = {
            "trades": n,
            "expected_target_rate": round(sum(c["expected"]["p_target"] or 0 for c in matched) / n, 4),
            "actual_target_rate": round(sum(c["actual"]["outcome"] == "target" for c in matched) / n, 4),
            "expected_stop_rate": round(sum(c["expected"]["p_stop"] or 0 for c in matched) / n, 4),
            "actual_stop_rate": round(sum(c["actual"]["outcome"] == "stop" for c in matched) / n, 4),
            "expected_avg_r": round(sum(e for e, _ in r_pairs) / len(r_pairs), 3) if r_pairs else None,
            "actual_avg_r": round(sum(a for _, a in r_pairs) / len(r_pairs), 3) if r_pairs else None,
        }
    stats = paper.stats(trades)
    if closed:
        stats["expectancy_net"] = round(sum(t["pnl_net"] for t in closed) / len(closed), 2)
        stats["max_drawdown_pct"] = max((p["drawdown_pct"] or 0) for p in curve)
    return {"portfolio_id": portfolio_id, "capital": capital, "equity": round(equity, 2), "stats": stats, "curve": curve,
            "days": sorted(days.values(), key=lambda d: d["date"], reverse=True),
            "comparisons": comparisons, "calibration": calibration,
            "unmatched": len(comparisons) - len(matched)}
