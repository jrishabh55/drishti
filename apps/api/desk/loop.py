"""Background desk loop: advance paper trades and ring alerts for every user.

Runs inside the API process (same pattern as scanner/ingest.py's
ingest_loop). Every ``DESK_LOOP_SECONDS`` while NSE is open, plus one sweep
shortly after the close so flat-by exits and end-of-day cancels land even
if nobody has the page open. Pages also refresh on read, so the loop is a
convenience, not the source of truth. Disable with DESK_LOOP=0.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from apps.api.scanner.calendar import IST, is_market_open

logger = logging.getLogger(__name__)

DESK_LOOP_SECONDS = 60


def sweep() -> int:
    """One pass over every user with open work. Returns users touched."""
    from apps.api.desk.store import get_desk_store
    from apps.api.routes import desk as desk_routes

    store = get_desk_store()
    users = {t["user_id"] for t in _with_user(store.open_paper_all_users())}
    users |= {a["user_id"] for a in _with_user(store.active_alerts_all_users())}
    try:
        from apps.api.desk.notifications import pending_users

        users |= pending_users()
    except Exception:  # noqa: BLE001 — notification upkeep never blocks the sweep
        logger.exception("listing pending notifications failed")
    for uid in sorted(users):
        try:
            desk_routes.refresh_paper(uid)
            desk_routes.evaluate_alerts(uid)
        except Exception:  # noqa: BLE001 — one user's bad row must not stop the others
            logger.exception("desk sweep failed for user %s", uid)
    return len(users)


def _with_user(rows):
    # list_* helpers return the doc; the owning user_id lives in the row.
    return [r for r in rows if r.get("user_id")]


async def desk_loop() -> None:
    after_close_done = None
    while True:
        # Sleep first: app startup (and test clients) shouldn't trigger a sweep.
        await asyncio.sleep(DESK_LOOP_SECONDS)
        try:
            now = datetime.now(timezone.utc)
            local = now.astimezone(IST)
            if is_market_open(now):
                await asyncio.to_thread(sweep)
            elif local.hour * 60 + local.minute >= 15 * 60 + 35 and \
                    after_close_done != local.date():
                await asyncio.to_thread(sweep)
                after_close_done = local.date()
        except Exception:  # noqa: BLE001
            logger.exception("desk loop iteration failed")
