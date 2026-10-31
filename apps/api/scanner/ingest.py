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

from apps.api.scanner.calendar import IST, OPEN, CLOSE, is_market_open, is_trading_day
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
    """Prepare panels, then publish the bar version and cache together so
    scans keep serving the previous snapshot during a sweep. Late import: engine
    imports the store module; keeping ingest's import lazy avoids a cycle."""
    from apps.api.scanner.engine import get_engine

    try:
        get_engine().warm(publish=True)
    except Exception:  # noqa: BLE001 — warmup is an optimization, never fatal
        # A failed prepare must not pin readers to old data indefinitely.
        # Fall back to the ordinary version-invalidated, lazy rebuild path.
        get_scanner_store().bump_bar_version()
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


def final_session_incomplete(store: ScannerStore, now: datetime) -> bool:
    """A current MAX timestamp cannot prove that the universe has every bar."""
    day = now.astimezone(IST).date()
    symbols = store.instruments_df().index
    if symbols.empty:
        return False
    df = store.load_session_bars("5m", day.isoformat(), symbols)
    if df.empty:
        return True
    expected = pd.date_range(datetime.combine(day, OPEN, IST),
                             datetime.combine(day, CLOSE, IST), freq="5min", inclusive="left")
    valid = df.dropna(subset=["open", "high", "low", "close"]).copy()
    valid["stamp"] = pd.to_datetime(valid["ts"], utc=True)
    counts = valid[valid["stamp"].isin(expected)].groupby("symbol")["stamp"].nunique()
    return bool((counts.reindex(symbols, fill_value=0) < len(expected)).any())


# Enough recent sessions for 320 retained bars, without refetching years of
# intraday data just to catch up a deployment after hours.
CATCHUP_DAYS = {"5m": 14, "15m": 30, "1h": 90}


async def ingest_loop() -> None:
    store = get_scanner_store()
    # Existing volumes already have usable bars. Prepare their read snapshot
    # before any network catch-up (a full-universe download can take minutes).
    # Do not bump the version or rewrite/download history just to warm a cache.
    if store.latest_ts('1d') is not None:
        from apps.api.scanner.engine import get_engine
        try:
            await asyncio.to_thread(get_engine().warm)
        except Exception:  # noqa: BLE001 — keep ingestion alive if warmup fails
            logger.exception('scanner existing-data warmup failed')
    if store.instruments_df().empty:
        await asyncio.to_thread(seed_universe, store)
        logger.info("scanner universe seeded")
    from apps.api.scanner.universe import apply_fo_membership
    await asyncio.to_thread(apply_fo_membership, store)
    enrich_done_for = ""
    needs_daily = store.latest_ts("1d") is None
    pending = {tf for tf in TF_FETCH if store.latest_ts(tf) is None}
    now0 = datetime.now(IST)
    fo_done_for = now0.date().isoformat()
    closed_day = now0.date()
    if now0.hour < EOD_HOUR_IST:
        closed_day -= timedelta(days=1)
    while not is_trading_day(closed_day):
        closed_day -= timedelta(days=1)
    if (store.latest_ts("1d") or "")[:10] < closed_day.isoformat():
        pending.add("1d")
    # A global MAX can be advanced by one watched symbol. Always attempt
    # a bounded universe sweep on startup, independently of that timestamp.
    pending.update(CATCHUP_DAYS)
    startup_5m_written = 0
    # Daily data in an existing volume must not prevent intraday catch-up.
    for tf in ("5m", "1d", "15m", "1h"):
        if tf in pending:
            logger.info("scanner startup backfill: %s", tf)
            written = await asyncio.to_thread(
                refresh_timeframe, store, tf,
                recent_days=45 if tf == "1d" and not needs_daily else CATCHUP_DAYS.get(tf))
            if tf == "5m":
                startup_5m_written = written
    if pending:
        await asyncio.to_thread(_publish_and_warm)
    if needs_daily:
        await asyncio.to_thread(enrich_universe, store)
        await asyncio.to_thread(_publish_and_warm)
        enriched_at = datetime.now(IST)
        enrich_done_for = f"{enriched_at.isocalendar().year}-{enriched_at.isocalendar().week}"

    last_intraday = asyncio.get_running_loop().time() if pending else 0.0
    # Track the final *universe sweep*, not an individual symbol's newest
    # bar. A sweep started before 16:00 can miss Yahoo's final delayed bars.
    final_done_for = now0.date().isoformat() if now0.hour >= 16 and startup_5m_written else ""
    eod_done_for = ""
    while True:
        try:
            now = datetime.now(IST)
            loop_t = asyncio.get_running_loop().time()
            today = now.date().isoformat()
            if fo_done_for != today:
                await asyncio.to_thread(apply_fo_membership, store)
                fo_done_for = today
                await asyncio.to_thread(_publish_and_warm)
            # A positive row count only proves the request returned something.
            # Retry incomplete final sessions at the normal cadence until 18:00;
            # suspended/vendor-missing symbols must not trigger overnight polling.
            final_due = (is_trading_day(now.date()) and 16 <= now.hour < EOD_HOUR_IST
                         and loop_t - last_intraday > INTRADAY_REFRESH_SECONDS
                         and (final_done_for != today
                              or await asyncio.to_thread(final_session_incomplete, store, now)))
            recovery_hours = (is_trading_day(now.date()) and OPEN <= now.time()
                              and now.hour < EOD_HOUR_IST)
            stale_due = recovery_hours and intraday_stale(store.latest_ts("5m"), now)
            if (is_market_open(now) or stale_due or final_due) and loop_t - last_intraday > INTRADAY_REFRESH_SECONDS:
                last_intraday = loop_t
                written_5m = 0
                for tf in ("5m", "15m", "1h"):
                    written = await asyncio.to_thread(refresh_timeframe, store, tf, recent_days=CATCHUP_DAYS.get(tf))
                    if tf == "5m":
                        written_5m = written
                if final_due and written_5m:
                    final_done_for = today
                await asyncio.to_thread(_publish_and_warm)
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
