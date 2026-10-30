"""Explicit data-only delegation. Broker auth never resolves through this module."""
from __future__ import annotations

from datetime import datetime

from apps.api.auth import ANONYMOUS_USER_ID, SHARED_BEARER_USER_ID
from apps.api.kite import auth
from apps.api.kite.store import get_kite_store


def individual(user_id: str) -> bool:
    return bool(user_id) and user_id not in (ANONYMOUS_USER_ID, SHARED_BEARER_USER_ID)


def owner_for(user_id: str) -> str:
    """One hop only: a grant delegates the owner's own connection, never theirs."""
    if not individual(user_id):
        return user_id
    return get_kite_store().selected_data_source(user_id) or user_id


def still_allowed(user_id: str, owner_id: str, now: datetime) -> bool:
    if owner_for(user_id) != owner_id:
        return False
    return owner_id == user_id or auth.load_session(owner_id, now) is not None


def require_access(user_id: str, owner_id: str, now: datetime) -> None:
    if not still_allowed(user_id, owner_id, now):
        raise auth.KiteAuthError("sharing_changed", "Shared data access changed; refresh the connection.")


def data_client(user_id: str, now: datetime, transport=None):
    owner_id = owner_for(user_id)
    client = auth.client_for(owner_id, now, transport)
    require_access(user_id, owner_id, now)
    return owner_id, client


def data_status(user_id: str, now: datetime) -> dict:
    from apps.api.kite import live

    owner_id = owner_for(user_id)
    session = auth.session_status(owner_id, now)["session"]
    stream = live.freshness(owner_id, now)
    if owner_id != user_id and owner_for(user_id) != owner_id:
        # A revoke or source change while reading diagnostics invalidates the
        # result just as it does an in-flight quote/history request.
        owner_id = user_id
        session = auth.session_status(user_id, now)["session"]
        stream = live.freshness(user_id, now)
    if owner_id != user_id:
        # Only market-data diagnostics cross the boundary, never account names,
        # credentials, postbacks, provider exception strings or order state.
        stream = dict(stream, detail=None, symbols=[
            {**s, "backfill_error": "Session history unavailable" if s.get("backfill_error") else None}
            for s in stream["symbols"]])
        if session != "active":
            stream = {"state": "off", "detail": "Connection owner must log in again.",
                      "symbols": [], "reconnects": 0}
    return {"owner_id": owner_id, "shared": owner_id != user_id,
            "session": session, "live": stream}


def summary(user_id: str, now: datetime) -> dict:
    store = get_kite_store()
    return {"user_id": user_id, "enabled": individual(user_id),
            "grants": store.data_grants(user_id),
            "received": [{"owner_id": row["owner_id"], "created_at": row["created_at"]}
                         for row in store.data_grants(user_id, received=True)],
            "selected_owner_id": store.selected_data_source(user_id),
            "data_access": data_status(user_id, now)}
