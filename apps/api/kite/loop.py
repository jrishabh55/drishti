"""Background Kite loop: reconcile live trades, run strategies, enforce flat-by.

Every ``KITE_LOOP_SECONDS`` while NSE is open (plus the 15 minutes after the
close so flat-by exits settle): reconcile every user's open trades against
Kite's order book, then scan paper/confirm strategies once a minute. Order
postbacks from a user's ticker trigger an immediate reconcile as well; this
loop is the polling fallback. Disable with KITE_LOOP=0.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from apps.api.scanner.calendar import is_market_open

logger = logging.getLogger(__name__)

KITE_LOOP_SECONDS = 15


def reconcile_all(now: datetime) -> int:
    from apps.api.desk.profile import load_profile
    from apps.api.kite import execution
    from apps.api.kite.store import get_kite_store

    users = get_kite_store().users_with_open_trades()
    for uid in users:
        try:
            execution.reconcile_user(uid, now, load_profile(uid))
        except Exception:  # noqa: BLE001
            logger.exception("kite reconcile failed for %s", uid)
    return len(users)


async def kite_loop() -> None:
    from apps.api.kite import strategies

    last_scan = None
    while True:
        await asyncio.sleep(KITE_LOOP_SECONDS)
        now = datetime.now(timezone.utc)
        if not (is_market_open(now) or is_market_open(now - timedelta(minutes=15))):
            continue
        try:
            await asyncio.to_thread(reconcile_all, now)
            if last_scan is None or now - last_scan >= timedelta(seconds=60):
                last_scan = now
                if is_market_open(now):
                    await asyncio.to_thread(strategies.sweep, now)
        except Exception:  # noqa: BLE001
            logger.exception("kite loop pass failed")


def install() -> None:
    """Root wiring (lifespan): live bar provider + order postbacks."""
    from apps.api.kite import execution, live

    live.install()
    if execution.on_postback not in live.order_listeners:
        live.order_listeners.append(execution.on_postback)
