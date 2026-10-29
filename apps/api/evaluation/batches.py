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
from concurrent.futures import ThreadPoolExecutor
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
    state      TEXT NOT NULL,          -- queued | submitting | submitted | rejected | cancelled
    run_id     TEXT,
    error      TEXT,
    score_json TEXT,
    PRIMARY KEY (batch_id, ticker, date)
);
"""
#: Added after the first release; ALTERed onto existing databases.
_COLUMNS = {"attempts": "INTEGER NOT NULL DEFAULT 0", "next_attempt_at": "TEXT", "claimed_at": "TEXT"}
#: Failed submissions that may succeed later (outage, disconnected helper/key,
#: vendor hiccup in preflight) are retried this many times, then rejected.
MAX_ATTEMPTS = 5
RETRY_BASE = timedelta(minutes=1)
RETRY_CAP = timedelta(minutes=30)
#: A claim older than this belongs to a process that died mid-submit. It is
#: rejected, not retried: the run may already exist and have been charged.
STALE_CLAIM = timedelta(minutes=15)
#: Score jobs queued at once: per user, and overall.
SCORE_JOBS_PER_USER = 8
SCORE_JOBS_TOTAL = 64

# ponytail: one process-wide lock around the pump (create_run does preflight I/O
# inside it); per-user locks if batch volume ever makes this a queue.
_pump_lock = threading.Lock()
_schema_ready: set = set()
_score_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="eval-score")
_score_jobs: dict[tuple[str, str, str, str], str] = {}  # key -> user_id
_score_lock = threading.Lock()



def _conn(store: JobStore):
    key = str(store.db_path)
    if key not in _schema_ready:
        with store._conn() as c:
            c.executescript(_SCHEMA)
            have = {r[1] for r in c.execute("PRAGMA table_info(eval_cells)")}
            for name, decl in _COLUMNS.items():
                if name not in have:
                    c.execute(f"ALTER TABLE eval_cells ADD COLUMN {name} {decl}")
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
            "WHERE b.user_id=? AND b.cancelled=0 AND c.state IN ('queued','submitting')", (user_id,)).fetchone()[0]
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


def cancel_batch(bid: str, user_id: str, store: Optional[JobStore] = None) -> Optional[Dict[str, int]]:
    """Stop submitting queued cells. Returns None when the batch isn't the user's.

    One transaction with the pump's claim (which requires ``cancelled=0``), so
    once this returns no queued cell can start. A cell the pump had already
    claimed is mid-submission and reported as ``starting``; runs already
    submitted finish normally.
    """
    store = store or get_store()
    if _batch_row(store, bid, user_id) is None:
        return None
    with _conn(store) as c:
        c.execute("UPDATE eval_batches SET cancelled=1 WHERE id=?", (bid,))
        cleared = c.execute("UPDATE eval_cells SET state='cancelled', next_attempt_at=NULL "
                            "WHERE batch_id=? AND state='queued'", (bid,)).rowcount
        starting = c.execute("SELECT COUNT(*) FROM eval_cells WHERE batch_id=? AND state='submitting'",
                             (bid,)).fetchone()[0]
    return {"cleared": cleared, "starting": starting}


def _submit(request: RunRequest, user_id: str) -> str:
    """One cell through the real run endpoint. Seam for tests."""
    from apps.api.routes.runs import create_run

    return create_run(request, Response(), force=False, skip_preflight=False, user_id=user_id).id


def _retryable(exc: HTTPException) -> bool:
    """Whether a refused submission may succeed later without the user changing it."""
    code = exc.status_code
    if code in (409, 429) or code >= 500:
        return True  # outage, credit check down, helper/key not connected yet
    # Preflight answers 400 no_market_data for a vendor hiccup too; bounded retries
    # tell that apart from a ticker that really has no data.
    return code == 400 and isinstance(exc.detail, dict) and exc.detail.get("code") == "no_market_data"


def _user_wide(exc: HTTPException) -> bool:
    """A failure every cell of this user would hit (not specific to one ticker)."""
    return not (exc.status_code == 400)


def pump(user_id: str, store: Optional[JobStore] = None) -> int:
    """Submit the user's next due queued cell. Returns submissions (0 or 1).

    Permanently refused cells are rejected and the next one tried; a retryable
    refusal ends the call and backs off
    (the whole user's queue for an account-wide cause such as a credit-check
    outage) instead of trying every queued cell in turn.
    """
    store = store or get_store()
    now = datetime.now(timezone.utc)
    with _pump_lock:
        with _conn(store) as c:
            c.execute(
                "UPDATE eval_cells SET state='rejected', error=? WHERE state='submitting' AND claimed_at<? "
                "AND batch_id IN (SELECT id FROM eval_batches WHERE user_id=?)",
                ("submission was interrupted by a server restart; check your runs before retrying",
                 (now - STALE_CLAIM).isoformat(), user_id))
        if store.has_active_run_for_user(user_id):
            return 0
        with _conn(store) as c:
            rows = c.execute(
                "SELECT c.*, b.request_json FROM eval_cells c JOIN eval_batches b ON b.id=c.batch_id "
                "WHERE b.user_id=? AND b.cancelled=0 AND c.state='queued' "
                "AND (c.next_attempt_at IS NULL OR c.next_attempt_at<=?) "
                "ORDER BY b.created_at, c.seq", (user_id, now.isoformat())).fetchall()
        for row in rows:
            ticker = row["ticker"]
            if store.has_active_run_for_ticker(ticker, user_id=user_id):
                continue
            where = ("WHERE batch_id=? AND ticker=? AND date=?", (row["batch_id"], ticker, row["date"]))
            with _conn(store) as c:
                # The claim: atomic with cancel_batch, so a cancelled batch never starts another cell.
                claimed = c.execute(
                    "UPDATE eval_cells SET state='submitting', claimed_at=? " + where[0] +
                    " AND state='queued' AND EXISTS (SELECT 1 FROM eval_batches b "
                    "WHERE b.id=eval_cells.batch_id AND b.cancelled=0)",
                    (now.isoformat(), *where[1])).rowcount
            if not claimed:
                continue
            req = RunRequest.model_validate_json(row["request_json"]).model_copy(
                update={"ticker": ticker, "analysis_date": row["date"]})
            update: Dict[str, Any] = {"state": "submitted", "run_id": None, "error": None,
                                      "next_attempt_at": None}
            hold_all = None
            try:
                update["run_id"] = _submit(req, user_id)
            except HTTPException as exc:
                attempts = row["attempts"] + 1
                error = _detail_text(exc.detail)
                if _retryable(exc) and attempts < MAX_ATTEMPTS:
                    retry_at = (now + min(RETRY_BASE * 2 ** (attempts - 1), RETRY_CAP)).isoformat()
                    update.update(state="queued", attempts=attempts, error=error, next_attempt_at=retry_at)
                    if _user_wide(exc):
                        hold_all = retry_at
                else:
                    if _retryable(exc):
                        error = f"gave up after {attempts} attempts: {error}"
                    update.update(state="rejected", attempts=attempts, error=error)
            except Exception as exc:  # noqa: BLE001 — explicit, never silently retried forever
                logger.exception("batch cell %s %s failed to submit", ticker, row["date"])
                update.update(state="rejected", error=f"{type(exc).__name__}: {exc}")
            with _conn(store) as c:
                cols = ", ".join(f"{k}=?" for k in update)
                c.execute(f"UPDATE eval_cells SET {cols}, claimed_at=NULL " + where[0],
                          (*update.values(), *where[1]))
                # Cancelled while this attempt was in flight: don't requeue it.
                c.execute("UPDATE eval_cells SET state='cancelled', next_attempt_at=NULL " + where[0] +
                          " AND state='queued' AND EXISTS (SELECT 1 FROM eval_batches b "
                          "WHERE b.id=eval_cells.batch_id AND b.cancelled=1)", where[1])
                if hold_all:
                    c.execute(
                        "UPDATE eval_cells SET next_attempt_at=? WHERE state='queued' "
                        "AND (next_attempt_at IS NULL OR next_attempt_at<?) "
                        "AND batch_id IN (SELECT id FROM eval_batches WHERE user_id=?)",
                        (hold_all, hold_all, user_id))
            if update["state"] == "rejected":
                continue  # specific to this cell and nothing was spent: try the next one
            return 1 if update["state"] == "submitted" else 0
    return 0


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


def _score(store: JobStore, row, rating: Optional[str], holding_days: int, *, background: bool = False,
           user_id: str = "") -> Dict[str, Any]:
    from tradingagents.agents.rating import RATING_REVIEW

    if rating == RATING_REVIEW:
        return {"status": "unscored", "reason": "the decision has no readable rating"}
    cached = json.loads(row["score_json"]) if row["score_json"] else None
    if cached and cached["status"] == "scored":
        return cached
    if cached and cached["status"] in ('pending','error') and \
            datetime.fromisoformat(cached["checked_at"]) > datetime.now(timezone.utc) - (PENDING_RECHECK if cached['status']=='pending' else timedelta(minutes=5)):
        return cached
    if background:
        key = (str(store.db_path), row["batch_id"], row["ticker"], row["date"])
        with _score_lock:
            mine = sum(1 for u in _score_jobs.values() if u == user_id)
            if key not in _score_jobs and mine < SCORE_JOBS_PER_USER and len(_score_jobs) < SCORE_JOBS_TOTAL:
                _score_jobs[key] = user_id
                try:
                    _score_pool.submit(_score_job, key, store, dict(row), rating, holding_days)
                except Exception:
                    _score_jobs.pop(key, None)
                    logger.exception("unable to queue evaluation score")
        return cached or {"status": "pending", "reason": "checking real closing prices"}
    try:
        score = score_cell(row["ticker"], row["date"], holding_days)
    except Exception as exc:  # noqa: BLE001 — explicit, never a guessed outcome
        score = {"status": "error", "reason": f"closes unavailable: {type(exc).__name__}: {exc}", 'checked_at':_now()}
    with _conn(store) as c:
        c.execute("UPDATE eval_cells SET score_json=? WHERE batch_id=? AND ticker=? AND date=?",
                  (json.dumps(score), row["batch_id"], row["ticker"], row["date"]))
    return score


def _score_job(key, store, row, rating, holding_days):
    try:
        _score(store, row, rating, holding_days)
    except Exception:
        logger.exception("evaluation score persistence failed")
    finally:
        with _score_lock:
            _score_jobs.pop(key, None)


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
            "running": count("queued", "submitting", "submitted", "running"),
            "failed": count("failed", "rejected", "cancelled", "interrupted", "error", "unscored"),
            "by_rating": by_rating}


def batch_view(bid: str, user_id: str, store: Optional[JobStore] = None, *, background: bool = False) -> Optional[Dict[str, Any]]:
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
                "error": row["error"], "retry_at": row["next_attempt_at"] if row["state"] == "queued" else None,
                "attempts": row["attempts"], "rating": run.rating if run else None,
                "run_status": run.status if run else None, "score": None}
        if row["state"] != "submitted":
            cell["phase"] = row["state"]
        elif run is None:
            cell["phase"], cell["error"] = "failed", cell["error"] or "run not found"
        elif run.status != "completed":
            cell["phase"] = run.status if run.status in ("failed", "cancelled", "interrupted") else "running"
            cell["error"] = run.error
        else:
            cell["score"] = _score(store, row, run.rating, holding, background=background, user_id=user_id)
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
