"""The user's paper book as the core's ``PortfolioContext``.

Snapshotted once, when a run is submitted, then frozen in the run's config so
the cache key and a resume both see the same book. The ledger is the Trade
Desk's paper ledger; cash uses the same formula the desk's risk check does.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from tradingagents.portfolio import PortfolioContext, Position

from apps.api.schemas import RunRequest


def paper_portfolio(user_id: str, store=None) -> PortfolioContext:
    """This user's Default paper book: cash and open positions (remaining
    quantity at average fill = cost basis). Named portfolios are separate books."""
    from apps.api.desk import paper
    from apps.api.desk.profile import load_profile
    from apps.api.desk.store import DEFAULT_PORTFOLIO, get_desk_store

    store = store or get_desk_store()
    profile = load_profile(user_id, store)
    trades = store.list_paper(user_id, limit=10_000, portfolio_id=DEFAULT_PORTFOLIO)
    realized, committed = 0.0, 0.0
    held: Dict[str, list] = {}  # symbol -> [qty, cost]
    for t in trades:
        acc = paper.accounting(t, profile)
        realized += acc["realized"]
        committed += acc["committed"]
        if acc["open_qty"]:
            q = held.setdefault(t["symbol"], [0, 0.0])
            q[0] += acc["open_qty"]
            q[1] += acc["committed"]
    positions = [
        # Desk symbols are bare NSE codes; the analysis speaks Yahoo's .NS.
        Position(ticker=f"{sym}.NS", quantity=qty, average_price=round(cost / qty, 4) if qty else None)
        for sym, (qty, cost) in sorted(held.items())
    ]
    return PortfolioContext(cash=round(profile.capital + realized - committed, 2),
                            currency="INR", positions=positions)


def snapshot(user_id: str, store=None) -> Dict[str, Any]:
    return paper_portfolio(user_id, store).model_dump()


def apply_portfolio_snapshot(request: RunRequest, user_id: str) -> RunRequest:
    """Server-own the snapshot: never trust a client-sent book."""
    request.portfolio_snapshot = snapshot(user_id) if request.use_paper_portfolio else None
    return request


def context_for(request: RunRequest) -> Optional[PortfolioContext]:
    """The frozen book for the graph, or None (no context, not a flat book)."""
    if request.portfolio_snapshot is None:
        return None
    return PortfolioContext.model_validate(request.portfolio_snapshot)
