"""Saved strategy backtests (kite/backtest.py). Results are immutable: there
is no update or delete route — a new run is a new record.

Routes (under /api, Clerk-authed, per user):
  GET  /kite/strategy-backtests?strategy_id=   own runs, newest first (summaries)
  POST /kite/strategy-backtests                {strategy_id, start_date, end_date,
                                                split_date?, symbols?} → 201 full result
                                               (scanner strategies: symbols = frozen universe)
  GET  /kite/strategy-backtests/{id}           one own run, in full
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.auth import current_user_id
from apps.api.desk import portfolios as pf_mod
from apps.api.kite import backtest
from apps.api.kite.store import get_kite_store

router = APIRouter()

SUMMARY_KEYS = ("id", "created_at", "strategy_id", "strategy_name", "strategy_version",
                "config", "summary", "costs", "sources", "generated_at")


def _now() -> datetime:
    return datetime.now(timezone.utc)


@router.get("/kite/strategy-backtests")
def list_backtests(strategy_id: Optional[str] = Query(None, max_length=64),
                   user_id: str = Depends(current_user_id)) -> List[Dict[str, Any]]:
    return [{k: b.get(k) for k in SUMMARY_KEYS}
            for b in get_kite_store().list_backtests(user_id, strategy_id)]


@router.post("/kite/strategy-backtests", status_code=201)
def create_backtest(body: backtest.BacktestBody, user_id: str = Depends(current_user_id)
                    ) -> Dict[str, Any]:
    store = get_kite_store()
    s = store.get_strategy(body.strategy_id, user_id)
    if s is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    try:
        profile = pf_mod.profile_for(user_id, s.get("portfolio_id") or pf_mod.DEFAULT)
        result = backtest.run(user_id, s, body, _now(), profile)
    except pf_mod.PortfolioError as exc:
        raise HTTPException(status_code=422, detail=f"strategy portfolio: {exc}") from exc
    except backtest.BacktestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return store.create_backtest(user_id, result)


@router.get("/kite/strategy-backtests/{bid}")
def get_backtest(bid: str, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    b = get_kite_store().get_backtest(bid, user_id)
    if b is None:
        raise HTTPException(status_code=404, detail="backtest not found")
    return b
