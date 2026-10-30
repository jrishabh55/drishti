"""Paper portfolios: independent capital, cash, trades, risk accounting and
performance inside one account.

``default`` is every user's original ledger (legacy rows were migrated to
it). It is virtual: its capital is the desk profile's ``capital``, edited on
Settings as before. Named portfolios store their own capital; the profile's
risk rules, plans and watchlist are shared by all of an account's portfolios.

A portfolio ID is never part of a user identity — bar providers, auth and
the live broker path keep the real account ID. A named portfolio is paper
only: broker hand-off and the live Kite engine stay on ``default``.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.api.desk.store import DEFAULT_PORTFOLIO, get_desk_store, now_iso

DEFAULT = DEFAULT_PORTFOLIO
MAX_PORTFOLIOS = 20
ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


class PortfolioError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _name(v: Any) -> str:
    v = " ".join(str(v or "").split())
    if not v or len(v) > 60:
        raise ValueError("name must be 1–60 characters")
    return v


class PortfolioCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    name: str
    capital: float = Field(gt=0, le=1e12)

    @field_validator("name")
    @classmethod
    def _clean(cls, v):
        return _name(v)


class PortfolioPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    name: Optional[str] = None
    capital: Optional[float] = Field(None, gt=0, le=1e12)

    @field_validator("name")
    @classmethod
    def _clean(cls, v):
        return None if v is None else _name(v)


def _view(user_id: str, doc: Dict[str, Any], store) -> Dict[str, Any]:
    trades = store.count_paper(user_id, doc["id"])
    return {"id": doc["id"], "name": doc["name"], "capital": doc["capital"],
            "is_default": doc["id"] == DEFAULT, "trades": trades,
            # Starting capital is the base of cash and performance: once a
            # portfolio has trades, changing it would rewrite that history.
            "capital_locked": doc["id"] == DEFAULT or trades > 0,
            "capital_history": doc.get("capital_history", []),
            "created_at": doc.get("created_at")}


def _default_doc(user_id: str, store) -> Dict[str, Any]:
    from apps.api.desk.profile import load_profile

    return {"id": DEFAULT, "name": "Default", "capital": load_profile(user_id, store).capital}


def get(user_id: str, portfolio_id: str, store=None) -> Optional[Dict[str, Any]]:
    """The portfolio as {id, name, capital, ...}, or None when not this user's."""
    store = store or get_desk_store()
    if portfolio_id == DEFAULT:
        return _view(user_id, _default_doc(user_id, store), store)
    doc = store.get_portfolio(portfolio_id, user_id)
    return _view(user_id, doc, store) if doc else None


def require(user_id: str, portfolio_id: str, store=None) -> Dict[str, Any]:
    pf = get(user_id, portfolio_id, store)
    if pf is None:
        raise PortfolioError(404, "portfolio not found")
    return pf


def name_of(user_id: str, portfolio_id: str, store=None) -> str:
    pf = get(user_id, portfolio_id or DEFAULT, store)
    return pf["name"] if pf else portfolio_id


def list_for(user_id: str, store=None) -> List[Dict[str, Any]]:
    store = store or get_desk_store()
    return [_view(user_id, _default_doc(user_id, store), store)] + \
        [_view(user_id, d, store) for d in store.list_portfolios(user_id)]


def _taken(user_id: str, name: str, store, but: Optional[str] = None) -> bool:
    return any(p["name"].casefold() == name.casefold() and p["id"] != but
               for p in list_for(user_id, store))


def create(user_id: str, body: PortfolioCreate, store=None) -> Dict[str, Any]:
    store = store or get_desk_store()
    if len(store.list_portfolios(user_id)) >= MAX_PORTFOLIOS:
        raise PortfolioError(422, f"at most {MAX_PORTFOLIOS} named portfolios")
    if _taken(user_id, body.name, store):
        raise PortfolioError(409, "you already have a portfolio with that name")
    doc = store.create_portfolio(user_id, {"name": body.name, "capital": body.capital,
                                           "capital_history": []})
    return _view(user_id, doc, store)


def update(user_id: str, portfolio_id: str, body: PortfolioPatch, store=None) -> Dict[str, Any]:
    """Rename and/or change starting capital. Call under the user's lock so a
    concurrent paper confirm can't slip a trade in between check and write."""
    store = store or get_desk_store()
    if portfolio_id == DEFAULT:
        raise PortfolioError(422, "the default portfolio's capital is set in Settings and it "
                                  "can't be renamed")
    doc = store.get_portfolio(portfolio_id, user_id)
    if doc is None:
        raise PortfolioError(404, "portfolio not found")
    if body.name is not None and body.name != doc["name"]:
        if _taken(user_id, body.name, store, but=portfolio_id):
            raise PortfolioError(409, "you already have a portfolio with that name")
        doc["name"] = body.name
    if body.capital is not None and not math.isclose(body.capital, doc["capital"]):
        if store.count_paper(user_id, portfolio_id):
            raise PortfolioError(409, "this portfolio has paper trades: its starting capital is "
                                      "fixed so cash and performance stay true. Create a new "
                                      "portfolio for a different size")
        doc["capital_history"] = (doc.get("capital_history") or []) + [
            {"at": now_iso(), "from": doc["capital"], "to": body.capital}]
        doc["capital"] = body.capital
    for meta in ("created_at", "updated_at", "user_id"):
        doc.pop(meta, None)
    store.update_portfolio(portfolio_id, user_id, doc)
    return get(user_id, portfolio_id, store)  # type: ignore[return-value]


def profile_for(user_id: str, portfolio_id: str, store=None):
    """The user's shared risk rules with this portfolio's capital."""
    from apps.api.desk.profile import load_profile

    store = store or get_desk_store()
    prof = load_profile(user_id, store)
    if portfolio_id == DEFAULT:
        return prof
    return prof.model_copy(update={"capital": require(user_id, portfolio_id, store)["capital"]})
