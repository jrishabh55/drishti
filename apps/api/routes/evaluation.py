"""Evaluation endpoints (all per-user; another user's rows 404).

  GET  /evaluation/portfolio-snapshot     the paper book a run would be given
  POST /evaluation/batches/quote          cells + max credits for a grid
  POST /evaluation/batches                start a confirmed batch
  GET  /evaluation/batches                list
  GET  /evaluation/batches/{id}           cells, scores, per-rating summary
  POST /evaluation/batches/{id}/cancel    stop submitting queued cells
  GET  /evaluation/paper-performance      equity, drawdown, days, replay vs actual
  GET  /evaluation/trading-performance    one book (paper|live): strategy/portfolio results
  GET  /evaluation/trade-journal.csv      the same filtered trades as CSV
  PUT  /evaluation/trade-journal/{mode}/{trade_id}   note + tags on an owned trade
  GET  /runs/{run_id}/report.html         standalone, script-free report page
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.api.auth import current_user_id
from apps.api.evaluation import batches
from apps.api.schemas import RunRequest

router = APIRouter()


@router.get("/evaluation/portfolio-snapshot")
def portfolio_snapshot(user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    from apps.api.evaluation.portfolio import snapshot

    return snapshot(user_id)


class BatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tickers: List[str] = Field(..., min_length=1, max_length=batches.MAX_TICKERS)
    start_date: str
    end_date: str
    every_n_days: int = Field(5, ge=1, le=90)
    # Model settings for every cell: a RunRequest without ticker/date.
    settings: Dict[str, Any]
    confirmed_credits: int = Field(-1, description="Must equal the quote's max_credits to start")

    def plan(self, user_id: str):
        try:
            base = RunRequest(**{**self.settings, "ticker": self.tickers[0], "analysis_date": self.start_date})
            cells = batches.plan_cells(self.tickers, self.start_date, self.end_date, self.every_n_days)
        except ValueError as exc:  # pydantic's ValidationError is a ValueError too
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return base, cells


@router.post("/evaluation/batches/quote")
def quote_batch(body: BatchBody, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    base, cells = body.plan(user_id)
    return {**batches.quote(base, cells, user_id), "grid": [{"ticker": t, "date": d} for t, d in cells]}


@router.post("/evaluation/batches", status_code=201)
def start_batch(body: BatchBody, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    base, cells = body.plan(user_id)
    bid = batches.create_batch(user_id, base, cells, body.confirmed_credits)
    batches.pump(user_id)
    return batches.batch_view(bid, user_id, background=True)  # type: ignore[return-value]


@router.get("/evaluation/batches")
def list_batches(user_id: str = Depends(current_user_id)) -> List[Dict[str, Any]]:
    return batches.list_batches(user_id)


@router.get("/evaluation/batches/{bid}")
def get_batch(bid: str, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    batches.pump(user_id)
    view = batches.batch_view(bid, user_id, background=True)
    if view is None:
        raise HTTPException(status_code=404, detail="batch not found")
    return view


@router.post("/evaluation/batches/{bid}/cancel")
def cancel_batch(bid: str, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    result = batches.cancel_batch(bid, user_id)
    if result is None:
        raise HTTPException(status_code=404, detail="batch not found")
    # ``cancel_result.starting``: cells already mid-submission, which will still start.
    return {**batches.batch_view(bid, user_id, background=True), "cancel_result": result}  # type: ignore[dict-item]


@router.get("/evaluation/paper-performance")
def paper_performance(portfolio_id: str = Query("default", pattern=r"^[A-Za-z0-9_-]{1,64}$"),
                      user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    from apps.api.desk import portfolios
    from apps.api.evaluation.performance import performance

    if portfolios.get(user_id, portfolio_id) is None:
        raise HTTPException(status_code=404, detail="portfolio not found")
    return performance(user_id, portfolio_id=portfolio_id)


_PF = Query("default", pattern=r"^[A-Za-z0-9_-]{1,64}$", description="all | default | <id>")
_STRAT = Query(None, pattern=r"^[A-Za-z0-9_-]{1,64}$", description="<id> | manual")


def _not_found(fn, *args, **kw):
    from apps.api.evaluation import analytics

    try:
        return fn(*args, **kw)
    except analytics.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/evaluation/trading-performance")
def trading_performance(mode: Literal["paper", "live"] = "paper", portfolio_id: str = _PF,
                        strategy_id: Optional[str] = _STRAT,
                        user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    from apps.api.evaluation import analytics

    return _not_found(analytics.trading_performance, user_id, mode=mode,
                      portfolio_id=portfolio_id, strategy_id=strategy_id)


@router.get("/evaluation/trade-journal.csv")
def trade_journal_csv(mode: Literal["paper", "live"] = "paper", portfolio_id: str = _PF,
                      strategy_id: Optional[str] = _STRAT,
                      user_id: str = Depends(current_user_id)) -> Response:
    from apps.api.evaluation import analytics

    rows = _not_found(analytics.journal_rows, user_id, mode=mode, portfolio_id=portfolio_id,
                      strategy_id=strategy_id)
    return Response(analytics.to_csv(rows), media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="drishti-{mode}-journal.csv"'})


class AnnotationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field("", max_length=2000)
    tags: List[str] = Field(default_factory=list, max_length=10)

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: List[str]) -> List[str]:
        out = list(dict.fromkeys(t.strip() for t in v if t.strip()))
        if any(len(t) > 32 for t in out):
            raise ValueError("a tag is at most 32 characters")
        return out


@router.put("/evaluation/trade-journal/{mode}/{trade_id}")
def annotate_trade(mode: Literal["paper", "live"], trade_id: str, body: AnnotationBody,
                   user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    from apps.api.evaluation import analytics

    doc = _not_found(analytics.annotate, user_id, mode, trade_id, body.note.strip(), body.tags)
    return doc or {"note": "", "tags": [], "updated_at": None}


@router.get("/runs/{run_id}/report.html")
def download_report_html(run_id: str, user_id: str = Depends(current_user_id)) -> Response:
    from apps.api.evaluation.report import render_html
    from apps.api.jobs.store import get_store

    detail = get_store().get_run(run_id)
    if detail is None or (detail.user_id or "anonymous") != user_id:
        raise HTTPException(status_code=404, detail="run not found")
    filename = f"{detail.ticker}_{detail.analysis_date}.html".replace('"', "")
    return Response(
        render_html(detail),
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # Same policy the page declares: nothing loads, nothing runs.
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
            "X-Content-Type-Options": "nosniff",
        },
    )
