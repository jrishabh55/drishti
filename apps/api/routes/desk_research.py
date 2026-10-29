"""Trade Desk research: Indian news, quarterly results and insider/deal
tracking for one NSE symbol or the caller's own watchlist.

  GET /desk/research/watchlist/news?days=3
  GET /desk/research/deals?scope=watchlist|all
  GET /desk/research/{symbol}/facts
  GET /desk/research/{symbol}/news?days=7
  GET /desk/research/{symbol}/results
  GET /desk/research/{symbol}/insiders?days=90

Every response lists its sources with status (ok | empty | unavailable),
fetch time and cache/stale flags. Public exchange data is cached shared;
watchlists stay per user. A bad symbol is a 422; a source that is down is
reported in the body, not hidden.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Literal

from fastapi import APIRouter, Depends, HTTPException

from apps.api.auth import current_user_id
from apps.api.desk import insider, news, research, results
from apps.api.desk.datasets import FeedUnavailable
from apps.api.desk.store import get_desk_store

router = APIRouter()


def _s(symbol: str) -> str:
    return symbol.upper().removesuffix(".NS")


def _run(fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    try:
        return fn()
    except FeedUnavailable as exc:
        if exc.reason == "bad_params":
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/desk/research/watchlist/news")
def watchlist_news(days: int = 3, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    watch = get_desk_store().watchlist(user_id)
    return _run(lambda: news.watchlist_news(watch, days=days))


@router.get("/desk/research/deals")
def deals(scope: Literal["watchlist", "all"] = "watchlist",
          user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    watch = get_desk_store().watchlist(user_id)
    return _run(lambda: insider.latest_deals(watch, only_watchlist=scope == "watchlist"))


@router.get("/desk/research/{symbol}/facts")
def facts(symbol: str, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    return _run(lambda: research.symbol_facts(_s(symbol)))


@router.get("/desk/research/{symbol}/news")
def symbol_news(symbol: str, days: int = 7, user_id: str = Depends(current_user_id)
                ) -> Dict[str, Any]:
    return _run(lambda: news.symbol_news(_s(symbol), days=days))


@router.get("/desk/research/{symbol}/results")
def symbol_results(symbol: str, user_id: str = Depends(current_user_id)) -> Dict[str, Any]:
    return _run(lambda: results.symbol_results(_s(symbol), yahoo=results.yahoo_quarters))


@router.get("/desk/research/{symbol}/insiders")
def symbol_insiders(symbol: str, days: int = 90, user_id: str = Depends(current_user_id)
                    ) -> Dict[str, Any]:
    return _run(lambda: insider.symbol_insiders(_s(symbol), days=days))
