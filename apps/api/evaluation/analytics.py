"""Trading analytics and journal over the user's own paper OR live book.

Paper and live are never combined: one call reads one ``mode``. Rows carry
the strategy attribution stamped on the trade (or, failing that, on the
ticket that created it); a record with neither is Manual/legacy, never
inferred. Open positions are marked with a real completed-bar price and its
source/age; a missing or old price is reported, not counted. The benchmark
is NIFTY 50 daily closes when available, otherwise an explicit reason.
Charges are the broker-plan estimates the ledgers already store.

Stable entry points (also for other consumers, e.g. mobile summaries):
``trading_performance(user_id, mode=, portfolio_id=, strategy_id=, strategy_version=)``,
``journal_rows(...)`` / ``to_csv(rows)`` and ``annotate(...)``.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from apps.api.desk.market.session import NSE

MANUAL = "manual"
ALL = "all"
BENCHMARK = {"symbol": "^NSEI", "name": "NIFTY 50"}
CHARGES_NOTE = ("Charges are estimates from your broker plan, not reconciled to broker "
                "contract notes.")
#: A mark older than this (from the bar's close) is stale and not counted.
FRESH_MINUTES = {"live": 10, "delayed": 30}
LIVE_OPEN = ("entry_sent", "protected", "exit_sent", "needs_attention")
CSV_FIELDS = ("mode", "trade_id", "trade_date", "symbol", "portfolio_id", "portfolio_name",
              "attribution", "strategy_id", "strategy_name", "strategy_version", "status", "qty",
              "open_qty", "entry_price", "exit_price", "exit_time", "exit_reason", "pnl_gross", "charges",
              "charges_basis", "pnl_net", "partial_pnl_net", "partial_charges", "r_net", "mark_price", "mark_source", "mark_as_of",
              "mark_status", "open_pnl", "note", "tags")
_ATTR = ("strategy_id", "strategy_name", "strategy_version")

_JOURNAL = """CREATE TABLE IF NOT EXISTS trade_journal (
    user_id TEXT NOT NULL, mode TEXT NOT NULL, trade_id TEXT NOT NULL,
    doc TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, mode, trade_id))"""


class NotFound(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------ providers

def latest_mark(user_id: str, symbol: str) -> Optional[Dict[str, Any]]:
    """Last completed 5m bar close today through the user's own bar seam."""
    from apps.api.desk import bars as bars_mod, bars_source

    now = _now()
    try:
        series = bars_source.get_session_rows(user_id, symbol, "5m", now=now)
        bars, _ = bars_source.session_bars_for(series, now)
    except bars_mod.BarsError:
        return None
    if not bars:
        return None
    b = bars[-1]
    at = NSE.local(date.fromisoformat(b["date"]), NSE.bar_close_min(b["slot"]))
    return {"price": b["c"], "source": series.source, "as_of": at.isoformat()}


def benchmark_closes() -> Sequence[tuple]:
    """NIFTY 50 daily rows (public Yahoo cache). Raises BarsError."""
    from apps.api.desk import bars as bars_mod

    return bars_mod.fetch_history(BENCHMARK["symbol"], "1d", yahoo=True)


# ------------------------------------------------------------ rows

def _attribution(doc: Dict[str, Any], ticket: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    for src in (doc, ticket or {}):
        if src.get("strategy_id"):
            return {k: src.get(k) for k in _ATTR}
    return dict.fromkeys(_ATTR)


def _mark(user_id: str, symbol: str, qty: float, entry: Optional[float], now: datetime):
    m = latest_mark(user_id, symbol)
    if not m or m.get("price") is None:
        return {"status": "unavailable", "price": None, "source": None, "as_of": None}, None
    at = datetime.fromisoformat(m["as_of"])
    kind = "live" if str(m.get("source", "")).startswith("live") else "delayed"
    same_day = at.astimezone(NSE.tz).date() == now.astimezone(NSE.tz).date()
    fresh = same_day and now - at <= timedelta(minutes=FRESH_MINUTES[kind])
    mark = {"status": kind if fresh else "stale", "price": m["price"], "source": m.get("source"),
            "as_of": m["as_of"]}
    pnl = round((m["price"] - entry) * qty, 2) if fresh and entry is not None else None
    return mark, pnl


def _paper_row(t: Dict[str, Any], profile) -> Dict[str, Any]:
    from apps.api.desk import paper

    entry, ex = t.get("entry_fill") or {}, t.get("exit_fill") or {}
    acc = paper.accounting(t, profile)
    is_open = t["status"] == "open"
    # An open trade's booked scale-out legs; a closed trade's are inside pnl_net.
    return {"trade_id": t["id"], "portfolio_id": t.get("portfolio_id") or "default",
            "status": t["status"], "qty": t.get("qty"), "open_qty": acc["open_qty"] if is_open else 0,
            "entry_price": entry.get("price"), "exit_price": ex.get("price"),
            "exit_time": ex.get("time"), "exit_reason": ex.get("reason"),
            "partial_pnl_net": acc["realized"] if is_open else 0.0,
            "partial_charges": acc["charges"] if is_open else 0.0,
            "partial_time": (max((x["time"] for x in t.get("exit_legs") or []), default=None)
                             if is_open else None)}


def _live_row(t: Dict[str, Any]) -> Dict[str, Any]:
    ex = t.get("exit_fill") or {}
    open_qty = (t.get("held_qty") or 0) if t["status"] in LIVE_OPEN else 0
    return {"trade_id": t["id"], "portfolio_id": "default", "status": t["status"],
            "qty": t.get("filled_qty") or t.get("qty"), "open_qty": open_qty,
            "entry_price": t.get("entry_avg"), "exit_price": t.get("exit_avg") or ex.get("price"),
            # Live trades record the reason at the top level; exit_fill is {price, time}.
            "exit_time": ex.get("time"), "exit_reason": t.get("exit_reason") or ex.get("reason"),
            "partial_pnl_net": 0.0, "partial_charges": 0.0, "partial_time": None}


def _version(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def journal_rows(user_id: str, *, mode: str = "paper", portfolio_id: str = "default",
                 strategy_id: Optional[str] = None, strategy_version: Optional[int] = None,
                 store=None, kite_store=None) -> List[Dict[str, Any]]:
    """The filtered trades of one book, oldest first. Raises NotFound for a
    portfolio that is not this user's. ``strategy_version`` (a positive int)
    narrows a ``strategy_id`` filter to that version; ValueError otherwise."""
    from apps.api.desk import portfolios
    from apps.api.desk.store import get_desk_store

    if strategy_version is not None and (
            strategy_id in (None, MANUAL) or isinstance(strategy_version, bool)
            or not isinstance(strategy_version, int) or strategy_version < 1):
        raise ValueError("strategy_version needs a strategy_id and must be a positive integer")
    store = store or get_desk_store()
    names = {p["id"]: p["name"] for p in portfolios.list_for(user_id, store)}
    if portfolio_id != ALL and portfolio_id not in names:
        raise NotFound("portfolio not found")
    if mode == "paper":
        from apps.api.desk.profile import load_profile

        docs = store.list_paper(user_id, limit=10_000,
                                portfolio_id=None if portfolio_id == ALL else portfolio_id)
        # Charges use the shared broker plan; per-portfolio profiles differ only in capital.
        profile = load_profile(user_id, store)
        shape = lambda t: _paper_row(t, profile)  # noqa: E731
    else:
        from apps.api.kite.store import get_kite_store

        # Live trades belong to the Default account; a named paper book has none.
        docs = ((kite_store or get_kite_store()).list_trades(user_id, limit=10_000)
                if portfolio_id in (ALL, "default") else [])
        shape = _live_row
    notes = _notes(store, user_id, mode)
    tickets = {k["id"]: k for k in store.list_tickets(user_id, limit=100_000)}
    now = _now()
    out = []
    for t in docs:
        attr = _attribution(t, tickets.get(t.get("ticket_id")))
        if strategy_id == MANUAL and attr["strategy_id"] is not None:
            continue
        if strategy_id not in (None, MANUAL) and attr["strategy_id"] != strategy_id:
            continue
        if strategy_version is not None and _version(attr["strategy_version"]) != strategy_version:
            continue
        row = {"mode": mode, "trade_date": t.get("trade_date"), "symbol": t["symbol"], **shape(t),
               **attr, "attribution": "strategy" if attr["strategy_id"] else MANUAL,
               "pnl_gross": t.get("pnl_gross"), "charges": t.get("charges"),
               "charges_basis": "estimated", "pnl_net": t.get("pnl_net"), "r_net": t.get("r_net"),
               "mark": None, "open_pnl": None, "annotation": notes.get(t["id"])}
        row["portfolio_name"] = names.get(row["portfolio_id"], row["portfolio_id"])
        open_qty = row["open_qty"]
        if open_qty:
            row["mark"], row["open_pnl"] = _mark(user_id, t["symbol"], open_qty,
                                                 row["entry_price"], now)
        out.append(row)
    return sorted(out, key=lambda r: (r["trade_date"] or "", r["exit_time"] or ""))


# ------------------------------------------------------------ aggregates

def _closed(rows):
    return [r for r in rows if r["status"] == "closed" and r["pnl_net"] is not None]


def _booked(rows) -> Dict[str, Any]:
    """Realized money: closed trades' net plus open trades' booked scale-out
    legs (never both for one trade — see _paper_row). Trade counts stay closed-only."""
    closed = _closed(rows)
    partial = round(sum(r["partial_pnl_net"] for r in rows), 2)
    return {"closed": round(sum(r["pnl_net"] for r in closed), 2), "partial": partial,
            "pnl_net": round(sum(r["pnl_net"] for r in closed) + partial, 2),
            "charges": round(sum(r["charges"] or 0 for r in closed)
                             + sum(r["partial_charges"] for r in rows), 2)}


def _stats(rows) -> Dict[str, Any]:
    closed = _closed(rows)
    if not closed:
        return {"trades": 0}
    wins = [r["pnl_net"] for r in closed if r["pnl_net"] > 0]
    losses = [-r["pnl_net"] for r in closed if r["pnl_net"] <= 0]
    rs = [r["r_net"] for r in closed if r["r_net"] is not None]
    return {"trades": len(closed), "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins) / len(closed), 3),
            "avg_r_net": round(sum(rs) / len(rs), 3) if rs else None,
            "pnl_net": round(sum(r["pnl_net"] for r in closed), 2),
            "charges": round(sum(r["charges"] or 0 for r in closed), 2),
            "profit_factor": round(sum(wins) / sum(losses), 2) if sum(losses) > 0 else None,
            "expectancy_net": round(sum(r["pnl_net"] for r in closed) / len(closed), 2),
            "max_drawdown": max(p["drawdown"] for p in _curve(rows))}


def _curve(rows) -> List[Dict[str, Any]]:
    """Realized P&L over time: each closed trade at its exit, and each open
    trade's booked scale-out legs as one point at its latest leg."""
    # ponytail: an open trade's legs are one point; per-leg points need per-leg costs.
    events = [(f"{r['trade_date']} {r['exit_time'] or ''}".strip(), r["pnl_net"], "closed")
              for r in _closed(rows)]
    events += [(f"{r['trade_date']} {r['partial_time'] or ''}".strip(), r["partial_pnl_net"],
                "partial") for r in rows if r["partial_time"]]
    pnl, peak, out = 0.0, 0.0, [{"at": None, "pnl": 0, "drawdown": 0, "kind": None}]
    for at, v, kind in sorted(events, key=lambda e: e[0]):
        pnl = round(pnl + v, 2)
        peak = max(peak, pnl)
        out.append({"at": at, "pnl": pnl, "drawdown": round(peak - pnl, 2), "kind": kind})
    return out


def _open(rows) -> Dict[str, Any]:
    pos = [r for r in rows if r["mark"] is not None]
    marked = [r["open_pnl"] for r in pos if r["open_pnl"] is not None]
    return {"positions": len(pos), "marked": len(marked), "unmarked": len(pos) - len(marked),
            "pnl": round(sum(marked), 2), "basis": "gross, before exit charges"}


def _group(rows, extra: Dict[str, Any]) -> Dict[str, Any]:
    o = _open(rows)
    return {**extra, "trades": len(rows), "closed": len(_closed(rows)),
            "realized": _booked(rows)["pnl_net"],
            "open_pnl": o["pnl"], "open_positions": o["positions"],
            "unmarked": o["unmarked"], "stats": _stats(rows)}


def _benchmark(rows, capital: Optional[float]) -> Dict[str, Any]:
    from apps.api.desk.bars import BarsError

    closed = _closed(rows)
    out = {**BENCHMARK, "available": False, "start": None, "end": None, "return_pct": None,
           "book_return_pct": None, "source": "delayed:yahoo daily closes", "as_of": None,
           "reason": None}
    if not closed:
        return {**out, "reason": "no closed trades to compare"}
    start, end = closed[0]["trade_date"], closed[-1]["trade_date"]
    out.update(start=start, end=end)
    try:
        daily = [(r[0].astimezone(NSE.tz).date().isoformat(), r[4]) for r in benchmark_closes()
                 if r[4] is not None]
    except BarsError as exc:
        return {**out, "reason": f"benchmark unavailable: {exc}"}
    before = [c for d, c in daily if d < start]
    upto = [(d, c) for d, c in daily if d <= end]
    if not before or not upto or upto[-1][0] < start:
        return {**out, "reason": f"no {BENCHMARK['name']} close on record before {start} "
                                 f"and through {end}"}
    out.update(available=True, as_of=upto[-1][0],
               return_pct=round((upto[-1][1] / before[-1] - 1) * 100, 3))
    if capital:
        out["book_return_pct"] = round(sum(r["pnl_net"] for r in closed) / capital * 100, 3)
    return out


def trading_performance(user_id: str, *, mode: str = "paper", portfolio_id: str = "default",
                        strategy_id: Optional[str] = None, strategy_version: Optional[int] = None,
                        store=None, kite_store=None) -> Dict[str, Any]:
    from apps.api.desk import portfolios
    from apps.api.desk.profile import load_profile
    from apps.api.desk.store import get_desk_store

    store = store or get_desk_store()
    rows = journal_rows(user_id, mode=mode, portfolio_id=portfolio_id, strategy_id=strategy_id,
                        strategy_version=strategy_version, store=store, kite_store=kite_store)
    pfs = portfolios.list_for(user_id, store)
    # Paper capitals are never summed into one account; live uses the profile's.
    capital = (load_profile(user_id, store).capital if mode == "live" else
               next((p["capital"] for p in pfs if p["id"] == portfolio_id), None))
    closed = _closed(rows)
    booked = _booked(rows)
    realized = booked["pnl_net"]
    groups: Dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r["strategy_id"], r["strategy_version"]), []).append(r)
    by_strategy = [_group(g, {"strategy_id": k[0], "strategy_version": k[1],
                              "strategy_name": g[-1]["strategy_name"],
                              "label": g[-1]["strategy_name"] or "Manual/legacy"})
                   for k, g in groups.items()]
    out = {"mode": mode, "filters": {"portfolio_id": portfolio_id, "strategy_id": strategy_id,
                                       "strategy_version": strategy_version},
           "generated_at": _now().isoformat(), "capital": capital,
           "realized": {"pnl_net": realized, "trades": len(closed),
                        "closed_pnl_net": booked["closed"], "partial_pnl_net": booked["partial"],
                        "charges": booked["charges"],
                        "return_pct": round(realized / capital * 100, 3) if capital else None},
           "open": _open(rows), "stats": _stats(rows), "curve": _curve(rows),
           "by_strategy": by_strategy, "by_portfolio": [], "benchmark": _benchmark(rows, capital),
           "charges_note": CHARGES_NOTE, "trades": rows}
    if mode == "paper":
        out["by_portfolio"] = [
            _group([r for r in rows if r["portfolio_id"] == p["id"]],
                   {"id": p["id"], "name": p["name"], "capital": p["capital"]})
            for p in pfs if portfolio_id in (ALL, p["id"])]
    return out


# ------------------------------------------------------------ journal + CSV

def _notes(store, user_id: str, mode: str) -> Dict[str, Dict[str, Any]]:
    with store._conn() as c:
        c.execute(_JOURNAL)
        rows = c.execute("SELECT trade_id, doc FROM trade_journal WHERE user_id=? AND mode=?",
                         (user_id, mode)).fetchall()
    return {r["trade_id"]: json.loads(r["doc"]) for r in rows}


def annotate(user_id: str, mode: str, trade_id: str, note: str, tags: List[str], store=None,
             kite_store=None) -> Optional[Dict[str, Any]]:
    """Set (or, when empty, clear) the note on one of the user's own trades."""
    from apps.api.desk.store import get_desk_store, now_iso

    store = store or get_desk_store()
    if mode == "paper":
        owned = store.get_paper(trade_id, user_id)
    else:
        from apps.api.kite.store import get_kite_store

        owned = (kite_store or get_kite_store()).get_trade(trade_id, user_id)
    if owned is None:
        raise NotFound("trade not found")
    doc = {"note": note, "tags": tags, "updated_at": now_iso()}
    with store._conn() as c:
        c.execute(_JOURNAL)
        if not note and not tags:
            c.execute("DELETE FROM trade_journal WHERE user_id=? AND mode=? AND trade_id=?",
                      (user_id, mode, trade_id))
            return None
        c.execute("INSERT INTO trade_journal VALUES (?,?,?,?,?) ON CONFLICT(user_id, mode, trade_id)"
                  " DO UPDATE SET doc=excluded.doc, updated_at=excluded.updated_at",
                  (user_id, mode, trade_id, json.dumps(doc), doc["updated_at"]))
    return doc


def _cell(v: Any) -> Any:
    # Text that a spreadsheet would run as a formula is quoted; numbers stay numbers.
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return "" if v is None else v


def to_csv(rows: List[Dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS)
    w.writeheader()
    for r in rows:
        mark, note = r["mark"] or {}, r["annotation"] or {}
        flat = {**{k: r.get(k) for k in CSV_FIELDS}, "mark_price": mark.get("price"),
                "mark_source": mark.get("source"), "mark_as_of": mark.get("as_of"),
                "mark_status": mark.get("status"), "note": note.get("note"),
                "tags": " ".join(note.get("tags") or [])}
        w.writerow({k: _cell(v) for k, v in flat.items()})
    return buf.getvalue()
