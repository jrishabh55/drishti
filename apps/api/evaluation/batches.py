"""Bounded backtest batches: a (tickers x dates) grid of ordinary analysis runs.

Every cell is submitted through ``routes.runs.create_run`` itself, so a cell is
exactly a run the user could have started by hand: the same provider checks,
preflight, per-user cache, active-run guard and credit debit. Nothing here
spends or bills on its own. Cells for one ticker go one at a time because the
active-run guard allows one live run per ticker per user; the queue is pumped
on creation, when the user looks, and whenever one of their runs finishes.

Scoring uses the core's settlement maths (``fetch_returns`` vs the regional
benchmark from ``resolve_benchmark``) and the core backtest's rating
directions. A cell is scored only once its whole holding window has real
closes; until then it is pending. Nothing is estimated.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, Response

from apps.api.jobs.store import JobStore, get_store
from apps.api.schemas import RunRequest

logger = logging.getLogger(__name__)

MAX_CELLS = 24
MAX_TICKERS = 6
#: Batches a user may have still submitting cells at once.
MAX_OPEN_BATCHES = 2
#: How long a "not yet traded" check is trusted before closes are asked for again.
PENDING_RECHECK = timedelta(hours=1)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS eval_batches (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    request_json TEXT NOT NULL,
    quoted_credits INTEGER NOT NULL,
    cancelled    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_eval_batches_user ON eval_batches(user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS eval_cells (
    batch_id   TEXT NOT NULL REFERENCES eval_batches(id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,
    ticker     TEXT NOT NULL,
    date       TEXT NOT NULL,
    state      TEXT NOT NULL,          -- queued | submitted | rejected | cancelled
    run_id     TEXT,
    error      TEXT,
    score_json TEXT,
    PRIMARY KEY (batch_id, ticker, date)
);
"""

# ponytail: one process-wide lock around the pump (create_run does preflight I/O
# inside it); per-user locks if batch volume ever makes this a queue.
_pump_lock = threading.Lock()
_schema_ready: set = set()


def _conn(store: JobStore):
    key = str(store.db_path)
    if key not in _schema_ready:
        with store._conn() as c:
            c.executescript(_SCHEMA)
        _schema_ready.add(key)
    return store._conn()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def billable(provider: "str | RunRequest", user_id: str) -> bool:
    """Mirror of create_run's debit condition: whether a fresh cell costs a credit.

    Every tier counts (a deep tier on the server's OpenAI key is billed even
    when the default provider is not), via the same normalised policy.
    """
    from apps.api import clerk_users
    from apps.api.auth import ANONYMOUS_USER_ID, SHARED_BEARER_USER_ID
    from apps.api.integrations.product_providers import ADMIN_PROVIDERS, uses_server_credit

    server_funded = (uses_server_credit(provider) if isinstance(provider, RunRequest)
                     else provider.strip().lower() in {"openai", *ADMIN_PROVIDERS})
    return (server_funded and clerk_users.enabled()
            and user_id not in (ANONYMOUS_USER_ID, SHARED_BEARER_USER_ID))


def plan_cells(tickers: List[str], start_date: str, end_date: str,
               every_n_days: int) -> List[Tuple[str, str]]:
    """The grid, bounded, via the core's own date grid (never past today)."""
    from tradingagents.backtest import iter_grid

    clean = list(dict.fromkeys(t.strip().upper() for t in tickers if t and t.strip()))
    if not clean:
        raise ValueError("add at least one ticker")
    if len(clean) > MAX_TICKERS:
        raise ValueError(f"a batch takes at most {MAX_TICKERS} tickers")
    dates = iter_grid(start_date, end_date, every_n_days)
    if not dates:
        raise ValueError("the date range has no past dates to analyse")
    cells = [(t, d) for t in clean for d in dates]
    if len(cells) > MAX_CELLS:
        raise ValueError(f"{len(cells)} cells is over the {MAX_CELLS}-cell limit: "
                         "narrow the range, widen the step or drop a ticker")
    return cells


def quote(base: RunRequest, cells: List[Tuple[str, str]], user_id: str) -> Dict[str, Any]:
    per_cell = 1 if billable(base, user_id) else 0
    out: Dict[str, Any] = {"cells": len(cells), "max_credits": per_cell * len(cells),
                           "credits_per_cell": per_cell, "credits_available": None}
    if per_cell:
        from apps.api import clerk_users

        try:
            out["credits_available"] = clerk_users.get_gate(user_id).credits
        except Exception:  # noqa: BLE001 — the debit itself fails closed later
            pass
    return out


def create_batch(user_id: str, base: RunRequest, cells: List[Tuple[str, str]],
                 confirmed_credits: int, store: Optional[JobStore] = None) -> str:
    """Record a confirmed batch. Raises HTTPException when the confirmation is stale."""
    store = store or get_store()
    q = quote(base, cells, user_id)
    if confirmed_credits != q["max_credits"]:
        raise HTTPException(status_code=409, detail={
            "code": "cost_not_confirmed", "quote": q,
            "message": f"this batch can cost up to {q['max_credits']} credits; "
                       "confirm that amount to start it"})
    if q["credits_available"] is not None and q["credits_available"] < q["max_credits"]:
        raise HTTPException(status_code=402, detail={
            "code": "insufficient_credits", "quote": q,
            "message": f"this batch needs up to {q['max_credits']} credits and you have "
                       f"{q['credits_available']}"})
    with _conn(store) as c:
        open_batches = c.execute(
            "SELECT COUNT(DISTINCT b.id) FROM eval_batches b JOIN eval_cells c ON c.batch_id=b.id "
            "WHERE b.user_id=? AND b.cancelled=0 AND c.state='queued'", (user_id,)).fetchone()[0]
    if open_batches >= MAX_OPEN_BATCHES:
        raise HTTPException(status_code=409, detail={
            "code": "too_many_batches",
            "message": f"you already have {open_batches} batches still starting runs; "
                       "wait for one or stop it first"})
    base = base.model_copy(update={"use_paper_portfolio": False, "portfolio_snapshot": None})
    bid = str(uuid.uuid4())
    base = base.model_copy(update={'evaluation_batch_id':bid})
    with _conn(store) as c:
        c.execute("INSERT INTO eval_batches(id, user_id, created_at, request_json, quoted_credits) "
                  "VALUES (?,?,?,?,?)", (bid, user_id, _now(), base.model_dump_json(), q["max_credits"]))
        c.executemany("INSERT INTO eval_cells(batch_id, seq, ticker, date, state) VALUES (?,?,?,?,'queued')",
                      [(bid, i, t, d) for i, (t, d) in enumerate(cells)])
    return bid


def _batch_row(store: JobStore, bid: str, user_id: str):
    with _conn(store) as c:
        return c.execute("SELECT * FROM eval_batches WHERE id=? AND user_id=?", (bid, user_id)).fetchone()


def cancel_batch(bid: str, user_id: str, store: Optional[JobStore] = None) -> bool:
    """Stop submitting queued cells. Runs already submitted finish normally."""
    store = store or get_store()
    if _batch_row(store, bid, user_id) is None:
        return False
    with _conn(store) as c:
        c.execute("UPDATE eval_batches SET cancelled=1 WHERE id=?", (bid,))
        c.execute("UPDATE eval_cells SET state='cancelled' WHERE batch_id=? AND state='queued'", (bid,))
    return True


def _submit(request: RunRequest, user_id: str) -> str:
    """One cell through the real run endpoint. Seam for tests."""
    from apps.api.routes.runs import create_run

    return create_run(request, Response(), force=False, skip_preflight=False, user_id=user_id).id


def pump(user_id: str, store: Optional[JobStore] = None) -> int:
    """Submit the next queued cell of each ticker that has no live run. Returns submissions."""
    store = store or get_store()
    submitted = 0
    with _pump_lock:
        if store.has_active_run_for_user(user_id):
            return 0
        with _conn(store) as c:
            rows = c.execute(
                "SELECT c.*, b.request_json FROM eval_cells c JOIN eval_batches b ON b.id=c.batch_id "
                "WHERE b.user_id=? AND b.cancelled=0 AND c.state='queued' "
                "ORDER BY b.created_at, c.seq", (user_id,)).fetchall()
        busy: set = set()
        for row in rows:
            ticker = row["ticker"]
            if ticker in busy or store.has_active_run_for_ticker(ticker, user_id=user_id):
                busy.add(ticker)
                continue
            busy.add(ticker)
            req = RunRequest.model_validate_json(row["request_json"]).model_copy(
                update={"ticker": ticker, "analysis_date": row["date"]})
            state, run_id, error = "submitted", None, None
            try:
                run_id = _submit(req, user_id)
                submitted += 1
            except HTTPException as exc:
                if exc.status_code == 409 and "already queued or running" in str(exc.detail):
                    continue  # raced a manual run: retry on the next pump
                state, error = "rejected", _detail_text(exc.detail)
            except Exception as exc:  # noqa: BLE001 — one bad cell must not stall the rest
                logger.exception("batch cell %s %s failed to submit", ticker, row["date"])
                state, error = "rejected", f"{type(exc).__name__}: {exc}"
            with _conn(store) as c:
                c.execute("UPDATE eval_cells SET state=?, run_id=?, error=? "
                          "WHERE batch_id=? AND ticker=? AND date=?",
                          (state, run_id, error, row["batch_id"], ticker, row["date"]))
            if state == 'submitted':
                break  # the runner serializes by user; don't occupy workers waiting on that lock
    return submitted


def _detail_text(detail: Any) -> str:
    if isinstance(detail, dict):
        return str(detail.get("message") or detail)
    return str(detail)


# ---------------------------------------------------------------- scoring

def score_cell(ticker: str, date: str, holding_days: int) -> Dict[str, Any]:
    """Real-close outcome of a decision dated ``date``: scored, or pending."""
    from tradingagents.dataflows.config import run_config
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.memory import settlement

    benchmark = settlement.resolve_benchmark(ticker, DEFAULT_CONFIG)
    with run_config(DEFAULT_CONFIG):
        raw, alpha, days, resolved = settlement.fetch_returns(ticker, date, holding_days, benchmark=benchmark)
    if raw is None:
        return {"status": "pending", "benchmark": benchmark, "checked_at": _now()}
    return {"status": "scored", "benchmark": benchmark, "raw_return": raw, "alpha": alpha,
            "holding_days": days, "resolved": resolved}


def _score(store: JobStore, row, rating: Optional[str], holding_days: int) -> Dict[str, Any]:
    from tradingagents.agents.rating import RATING_REVIEW

    if rating == RATING_REVIEW:
        return {"status": "unscored", "reason": "the decision has no readable rating"}
    cached = json.loads(row["score_json"]) if row["score_json"] else None
    if cached and cached["status"] == "scored":
        return cached
    if cached and cached["status"] in ('pending','error') and \
            datetime.fromisoformat(cached["checked_at"]) > datetime.now(timezone.utc) - (PENDING_RECHECK if cached['status']=='pending' else timedelta(minutes=5)):
        return cached
    try:
        score = score_cell(row["ticker"], row["date"], holding_days)
    except Exception as exc:  # noqa: BLE001 — explicit, never a guessed outcome
        score = {"status": "error", "reason": f"closes unavailable: {type(exc).__name__}: {exc}", 'checked_at':_now()}
    with _conn(store) as c:
        c.execute("UPDATE eval_cells SET score_json=? WHERE batch_id=? AND ticker=? AND date=?",
                  (json.dumps(score), row["batch_id"], row["ticker"], row["date"]))
    return score


def summarize(cells: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-rating hit rate and mean alpha over scored cells (the core backtest's rules)."""
    from tradingagents.backtest import _DIRECTION

    scored = [c for c in cells if (c.get("score") or {}).get("status") == "scored"]
    by_rating: Dict[str, Any] = {}
    for rating in dict.fromkeys(c["rating"] for c in scored):
        alphas = [c["score"]["alpha"] for c in scored if c["rating"] == rating]
        direction = _DIRECTION.get(rating, 0)
        by_rating[rating] = {
            "count": len(alphas),
            "hit_rate": (sum(a * direction > 0 for a in alphas) / len(alphas)) if direction else None,
            "mean_alpha": sum(alphas) / len(alphas),
        }
    count = lambda *s: sum(1 for c in cells if c["phase"] in s)  # noqa: E731
    return {"scored": len(scored), "pending": count("pending"),
            "running": count("queued", "submitted", "running"),
            "failed": count("failed", "rejected", "cancelled", "interrupted", "error", "unscored"),
            "by_rating": by_rating}


def batch_view(bid: str, user_id: str, store: Optional[JobStore] = None) -> Optional[Dict[str, Any]]:
    from tradingagents.default_config import DEFAULT_CONFIG

    store = store or get_store()
    b = _batch_row(store, bid, user_id)
    if b is None:
        return None
    holding = int(DEFAULT_CONFIG.get("holding_period_days", 5))
    with _conn(store) as c:
        rows = c.execute("SELECT * FROM eval_cells WHERE batch_id=? ORDER BY seq", (bid,)).fetchall()
    cells = []
    for row in rows:
        run = store.get_run(row["run_id"]) if row["run_id"] else None
        if run is not None and (run.user_id or "anonymous") != user_id:
            run = None  # never surface another user's row
        cell = {"ticker": row["ticker"], "date": row["date"], "run_id": row["run_id"],
                "error": row["error"], "rating": run.rating if run else None,
                "run_status": run.status if run else None, "score": None}
        if row["state"] != "submitted":
            cell["phase"] = row["state"]
        elif run is None:
            cell["phase"], cell["error"] = "failed", cell["error"] or "run not found"
        elif run.status != "completed":
            cell["phase"] = run.status if run.status in ("failed", "cancelled", "interrupted") else "running"
            cell["error"] = run.error
        else:
            cell["score"] = _score(store, row, run.rating, holding)
            cell["phase"] = cell["score"]["status"]
        cells.append(cell)
    req = json.loads(b["request_json"])
    return {"id": b["id"], "created_at": b["created_at"], "cancelled": bool(b["cancelled"]),
            "quoted_credits": b["quoted_credits"], "holding_days": holding,
            "settings": {k: req.get(k) for k in ("llm_provider", "shallow_thinker", "deep_thinker",
                                                 "research_depth", "analysts", "output_language")},
            "cells": cells, "summary": summarize(cells)}


def list_batches(user_id: str, store: Optional[JobStore] = None) -> List[Dict[str, Any]]:
    store = store or get_store()
    with _conn(store) as c:
        rows = c.execute(
            "SELECT b.id, b.created_at, b.quoted_credits, b.cancelled, COUNT(c.ticker) AS cells, "
            "GROUP_CONCAT(DISTINCT c.ticker) AS tickers, MIN(c.date) AS first_date, MAX(c.date) AS last_date "
            "FROM eval_batches b LEFT JOIN eval_cells c ON c.batch_id=b.id WHERE b.user_id=? "
            "GROUP BY b.id ORDER BY b.created_at DESC LIMIT 50", (user_id,)).fetchall()
    return [{**dict(r), "cancelled": bool(r["cancelled"]),
             "tickers": (r["tickers"] or "").split(",") if r["tickers"] else []} for r in rows]
