"""Per-run wiring for the residential fetch proxy.

The upstream fetchers (reddit.py, stocktwits.py) call
``tradingagents.dataflows.fetch_proxy.urlopen_maybe_proxied``, which consults
a registered resolver. This module provides that resolver as a
run context. LangGraph copies it into parallel analyst tasks, while
unrelated threads and other runs keep their own proxy credentials.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

from tradingagents.dataflows import fetch_proxy as upstream

_proxy: ContextVar[Optional[upstream.FetchProxy]] = ContextVar("app_fetch_proxy", default=None)


def install_resolver() -> None:
    """Point the upstream hook at the current run context."""
    upstream.set_resolver(_proxy.get)


def fetch_shim_url() -> str:
    """Where a worker thread reaches this API's own fetch shim."""
    import os

    from apps.api.integrations.helper_backend import DEFAULT_SELF_URL, SELF_URL_ENV

    base = os.environ.get(SELF_URL_ENV, DEFAULT_SELF_URL).rstrip("/")
    return f"{base}/internal/relay/fetch"


def activate_for_run(user_id: str) -> Optional[str]:
    """Enable proxying in this run and its graph tasks when the helper is online.

    Mints a per-run internal token (same registry the LLM relay uses) and
    returns it so the caller can revoke it when the run ends; returns None —
    and leaves the run unproxied — when no helper is connected.
    """
    install_resolver()
    from apps.api.integrations.helper_backend import relay_available
    from apps.api.routes.relay import get_internal_tokens

    _proxy.set(None)
    if not relay_available(user_id):
        return None
    token = get_internal_tokens().mint(user_id or "anonymous")
    _proxy.set(upstream.FetchProxy(url=fetch_shim_url(), token=token))
    return token


def deactivate(token: Optional[str]) -> None:
    """Clear this run's proxy and revoke its token. Safe on every path."""
    _proxy.set(None)
    if token:
        from apps.api.routes.relay import get_internal_tokens

        get_internal_tokens().revoke(token)
