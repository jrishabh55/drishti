"""Scheduled yfinance ingest: EOD after close, delayed intraday during hours.

Runs as an asyncio task inside the API process (same pattern as the jobs
runner). yfinance intraday is 15-20 min delayed — that's the accepted product
trade-off; see the spec. All fetch work happens in a thread via
asyncio.to_thread so the event loop never blocks.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

from apps.api.scanner.calendar import IST, is_market_open, is_trading_day
from apps.api.scanner.store import ScannerStore, get_scanner_store
from apps.api.scanner.universe import enrich_universe, seed_universe

logger = logging.getLogger(__name__)

#: timeframe -> (yfinance interval, lookback days). Passed as an explicit
#: `start` rather than `period`: with `period`, yfinance substitutes a
#: recently-listed ticker's first-trade date as the start, and once that date
#: ages past Yahoo's intraday window (730d for 1h, 60d for 15m/5m) every
#: request for that ticker is rejected forever. Lookbacks sit 1d inside the
#: window so clock skew can't trip the limit.
TF_FETCH = {"1d": ("1d", 729), "1h": ("1h", 729),
            "15m": ("15m", 59), "5m": ("5m", 59)}
CHUNK = 100
RETENTION = 320
INTRADAY_REFRESH_SECONDS = 600
EOD_HOUR_IST = 18  # refresh daily bars after 18:00 IST
#: Sleep between yf.download chunks so a full-universe sweep doesn't hammer
#: Yahoo with back-to-back batch requests. Module-level so tests can zero it.
CHUNK_SLEEP_SECONDS = 1.0


def refresh_timeframe(store: ScannerStore, timeframe: str, *, recent_days: int | None = None) -> int:
    interval, lookback_days = TF_FETCH[timeframe]
    start = datetime.now(timezone.utc) - timedelta(days=min(lookback_days, recent_days or lookback_days))
    inst = store.instruments_df()
    if inst.empty:
        return 0
    yf_to_ours = dict(zip(inst["yf_symbol"], inst.index))
    written = 0
    yf_symbols = list(yf_to_ours)
    for i in range(0, len(yf_symbols), CHUNK):
        chunk = yf_symbols[i:i + CHUNK]
        try:
            data = yf.download(tickers=" ".join(chunk), start=start,
                               interval=interval, group_by="ticker",
                               auto_adjust=False, threads=True, progress=False)
            long = _to_long(data, chunk, yf_to_ours)
            if not long.empty:
                store.upsert_bars(timeframe, long)
                written += len(long)
        except Exception as exc:  # noqa: BLE001 — partial universe beats no universe
            logger.warning("yf.download %s chunk %d failed: %s", timeframe, i, exc)
            continue
        finally:
            # Be polite to Yahoo across chunks of a full-universe sweep —
            # skip the sleep after the last chunk, there's nothing after it.
            if CHUNK_SLEEP_SECONDS and i + CHUNK < len(yf_symbols):
                time.sleep(CHUNK_SLEEP_SECONDS)
    store.prune_bars(timeframe, keep=RETENTION)
    return written


def _to_long(data: pd.DataFrame, chunk: list[str], yf_to_ours: dict) -> pd.DataFrame:
    frames = []
    for yf_sym in chunk:
        try:
            df = data[yf_sym] if isinstance(data.columns, pd.MultiIndex) else data
        except KeyError:
            continue
        df = df.dropna(subset=["Close"])
        if df.empty:
            continue
        frames.append(pd.DataFrame({
            "symbol": yf_to_ours[yf_sym],
            "ts": [t.isoformat() for t in df.index],
            "open": df["Open"].to_numpy(), "high": df["High"].to_numpy(),
            "low": df["Low"].to_numpy(), "close": df["Close"].to_numpy(),
            "volume": df["Volume"].to_numpy(),
        }))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def refresh_all(store: ScannerStore) -> None:
    for tf in TF_FETCH:
        n = refresh_timeframe(store, tf)
        logger.info("scanner ingest: %s -> %d bars", tf, n)


def _publish_and_warm() -> None:
    """Bump the bar version (once per cycle — see bump_bar_version) and
    pre-build scan panels, so scans serve the previous snapshot during a
    sweep and never pay the cold rebuild themselves. Late import: engine
    imports the store module; keeping ingest's import lazy avoids a cycle."""
    from apps.api.scanner.engine import get_engine

    get_scanner_store().bump_bar_version()
    try:
        get_engine().warm()
    except Exception:  # noqa: BLE001 — warmup is an optimization, never fatal
        logger.exception("scanner panel warmup failed")


def intraday_stale(latest: str | None, now: datetime) -> bool:
    """Whether the latest 5m bar is behind the most recent delayed NSE session."""
    if latest is None:
        return True
    local = now.astimezone(IST)
    day = local.date()
    if not is_trading_day(day) or local.hour * 60 + local.minute < 9 * 60 + 15:
        day -= timedelta(days=1)
        while not is_trading_day(day):
            day -= timedelta(days=1)
    close = datetime.combine(day, datetime.min.time(), tzinfo=IST).replace(hour=15, minute=25)
    expected = min(local - timedelta(minutes=30), close)
    try:
        stamp = datetime.fromisoformat(latest)
    except ValueError:
        return True
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=IST)
    return stamp < expected


# Enough recent sessions for 320 retained bars, without refetching years of
# intraday data just to catch up a deployment after hours.
CATCHUP_DAYS = {"5m": 14, "15m": 30, "1h": 90}


async def ingest_loop() -> None:
    store = get_scanner_store()
    if store.instruments_df().empty:
        await asyncio.to_thread(seed_universe, store)
        logger.info("scanner universe seeded")
    enrich_done_for = ""
    needs_daily = store.latest_ts("1d") is None
    pending = {tf for tf in TF_FETCH if store.latest_ts(tf) is None}
    now0 = datetime.now(IST)
    closed_day = now0.date()
    if now0.hour < EOD_HOUR_IST:
        closed_day -= timedelta(days=1)
    while not is_trading_day(closed_day):
        closed_day -= timedelta(days=1)
    if (store.latest_ts("1d") or "")[:10] < closed_day.isoformat():
        pending.add("1d")
    if intraday_stale(store.latest_ts("5m"), now0):
        pending.update(CATCHUP_DAYS)
    # Daily data in an existing volume must not prevent intraday catch-up.
    for tf in ("5m", "1d", "15m", "1h"):
        if tf in pending:
            logger.info("scanner startup backfill: %s", tf)
            await asyncio.to_thread(refresh_timeframe, store, tf,
                                    recent_days=45 if tf == "1d" and not needs_daily else CATCHUP_DAYS.get(tf))
    if pending:
        await asyncio.to_thread(_publish_and_warm)
    if needs_daily:
        await asyncio.to_thread(enrich_universe, store)
        await asyncio.to_thread(_publish_and_warm)
        now0 = datetime.now(IST)
        enrich_done_for = f"{now0.isocalendar().year}-{now0.isocalendar().week}"

    last_intraday = asyncio.get_running_loop().time() if pending else 0.0
    eod_done_for = ""
    while True:
        try:
            now = datetime.now(IST)
            loop_t = asyncio.get_running_loop().time()
            if (is_market_open(now) or intraday_stale(store.latest_ts("5m"), now)) and loop_t - last_intraday > INTRADAY_REFRESH_SECONDS:
                last_intraday = loop_t
                for tf in ("5m", "15m", "1h"):
                    await asyncio.to_thread(refresh_timeframe, store, tf, recent_days=CATCHUP_DAYS.get(tf))
                await asyncio.to_thread(_publish_and_warm)
            today = now.date().isoformat()
            if (is_trading_day(now.date()) and now.hour >= EOD_HOUR_IST
                    and eod_done_for != today):
                eod_done_for = today
                await asyncio.to_thread(refresh_timeframe, store, "1d")
                await asyncio.to_thread(_publish_and_warm)
            # Weekly fundamentals sweep on Saturdays.
            week = f"{now.isocalendar().year}-{now.isocalendar().week}"
            if now.weekday() == 5 and enrich_done_for != week:
                enrich_done_for = week
                await asyncio.to_thread(enrich_universe, store)
                # Panels embed fundamentals/meta — republish so the fresh
                # enrichment is visible before the next bar refresh.
                await asyncio.to_thread(_publish_and_warm)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the loop must survive any single failure
            logger.exception("scanner ingest cycle failed")
        await asyncio.sleep(60)
