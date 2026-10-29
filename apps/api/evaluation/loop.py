"""Background batch sweep: keeps confirmed backtest batches moving (backoff
retries, deferrals behind the user's own run) without the batch page open.
Root wiring starts ``evaluation_loop()`` in the app lifespan (EVALUATION_LOOP,
default on). The first sweep runs at once, so batches queued before a restart
resume immediately."""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

EVALUATION_LOOP_SECONDS = 30


async def evaluation_loop(interval: float = EVALUATION_LOOP_SECONDS) -> None:
    from apps.api.evaluation import batches

    while True:
        try:
            await asyncio.to_thread(batches.sweep)
        except Exception:  # noqa: BLE001 — the loop outlives any one bad pass
            logger.exception("batch sweep pass failed")
        await asyncio.sleep(interval)
