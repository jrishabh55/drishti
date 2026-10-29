"""Evaluation endpoints (all per-user; another user's rows 404).

  GET  /evaluation/portfolio-snapshot     the paper book a run would be given
  POST /evaluation/batches/quote          cells + max credits for a grid
  POST /evaluation/batches                start a confirmed batch
  GET  /evaluation/batches                list
  GET  /evaluation/batches/{id}           cells, scores, per-rating summary
  POST /evaluation/batches/{id}/cancel    stop submitting queued cells
  GET  /evaluation/paper-performance      equity, drawdown, days, replay vs actual
  GET  /runs/{run_id}/report.html         standalone, script-free report page
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

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
    return batches.batch_view(bid, user_id)  # type: ignore[return-value]


@router.get("/evaluation/batches")
def list_batches(user_id: str = Depends(current_user_id)) -> List[Dict[str, Any]]:
    return batches.list_batches(user_id)


@router.get("/evaluation/batches/{bid}")
def get_batch(bid: str, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    batches.pump(user_id)
    view = batches.batch_view(bid, user_id)
    if view is None:
        raise HTTPException(status_code=404, detail="batch not found")
    return view


@router.post("/evaluation/batches/{bid}/cancel")
def cancel_batch(bid: str, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    if not batches.cancel_batch(bid, user_id):
        raise HTTPException(status_code=404, detail="batch not found")
    return batches.batch_view(bid, user_id)  # type: ignore[return-value]


@router.get("/evaluation/paper-performance")
def paper_performance(user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    from apps.api.evaluation.performance import performance

    return performance(user_id)


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
