"""Per-user live hubs and the private live-bar provider for desk/bars_source.

One hub per connected user: a ticker task (ticker.run_ticker) feeding a
BarBuilder. Everything lives in this process's memory only — Kite data is
never written to the shared scanner/desk caches. Explicit direct grants may
allow other signed-in accounts to read the owner's feed through sharing.py.

``provider`` is what root wiring registers with
``bars_source.register_live_provider``. It answers only for the caller's own
hub or an explicitly selected, directly granted owner's hub, only for
subscribed symbols whose current session has been backfilled
from the user's Kite minute history (a hub started — or reconnected — after
09:15 would otherwise serve a session missing its opening bars) and that
have a recent tick (``STALE_AFTER``). Any other case returns None, and
bars_source falls back to its delayed source, whose BarSeries says
``delayed:yahoo``. History (Time Machine pools) comes
from the selected connection's Kite historical API and remains uncached.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from apps.api.desk import bars as bars_mod
from apps.api.desk.bars_source import BarSeries
from apps.api.desk.market.session import NSE as NSE_SPEC
from apps.api.kite import auth, history, rest, sharing, ticker
from apps.api.scanner.calendar import IST

logger = logging.getLogger(__name__)

SOURCE = "live:kite"
STALE_AFTER = timedelta(seconds=90)
MAX_SYMBOLS = 500        # bounded NIFTY500; Kite permits 3,000 per websocket
SESSION_END = time(15, 35)
BACKFILL_SPACING_S = 0.35   # Kite's historical API allows 3 requests a second


@dataclass
class Hub:
    user_id: str
    symbols: List[str]
    builder: ticker.BarBuilder = field(default_factory=ticker.BarBuilder)
    state: str = "starting"
    detail: Optional[str] = None
    reconnects: int = 0
    started_at: Optional[str] = None
    task: Optional[asyncio.Task] = None
    postbacks: List[Dict[str, Any]] = field(default_factory=list)
    #: symbols whose session so far is complete (history + ticks)
    backfilled: set = field(default_factory=set)
    backfill_errors: Dict[str, str] = field(default_factory=dict)
    backfill_task: Optional[asyncio.Task] = None
    history: Dict[str, Dict[str, list]] = field(default_factory=dict)
    history_errors: Dict[str, str] = field(default_factory=dict)
    history_task: Optional[asyncio.Task] = None
    dropped: bool = False
    on_state: Optional[Callable[[str, Optional[str]], None]] = None
    pump: Optional["PostbackPump"] = None

    def __post_init__(self) -> None:
        if self.pump is None:
            self.pump = PostbackPump(self.user_id)


_hubs: Dict[str, Hub] = {}
#: Order postbacks go to the execution engine (registered by engine wiring).
order_listeners: List[Callable[[str, Dict[str, Any]], None]] = []


def _now() -> datetime:
    return datetime.now(timezone.utc)


def session_open(now: datetime) -> datetime:
    om = NSE_SPEC.open_min
    return datetime.combine(now.astimezone(IST).date(), time(om // 60, om % 60), IST)


def _rows(candles: List[list]) -> List[Any]:
    return [(datetime.fromisoformat(c[0]), float(c[1]), float(c[2]), float(c[3]),
             float(c[4]), float(c[5] if len(c) > 5 else 0)) for c in candles]


#: Waits before each retry of symbols whose history failed (429, timeout…).
#: Bounded: after the last one the symbol stays DELAYED and says why.
BACKFILL_RETRY_S = (5, 15, 30, 60, 60, 60)


async def _backfill(hub: Hub, client: rest.KiteClient, table: Dict[str, Dict[str, Any]],
                    clock: Callable[[], datetime]) -> None:
    """Fill each symbol's session so far from the user's Kite minute candles
    (per user, in memory). Until a symbol is filled it isn't served live.
    Transient failures are retried with a bounded backoff; a token failure
    stops at once (the session is gone)."""
    for attempt in range(len(BACKFILL_RETRY_S) + 1):
        if attempt:
            await asyncio.sleep(BACKFILL_RETRY_S[attempt - 1])
        for sym in list(hub.symbols):
            if sym in hub.backfilled:
                continue
            now = clock()
            try:
                candles = await asyncio.to_thread(history.gate.fetch, hub.user_id, client, table[sym]["token"],
                                                  "minute", session_open(now),
                                                  now.astimezone(IST))
            except rest.KiteError as exc:
                left = len(BACKFILL_RETRY_S) - attempt
                hub.backfill_errors[sym] = (f"session history unavailable: {exc}"
                                            + (f"; retrying ({left} more)" if left and
                                               not exc.token_error else ""))
                if exc.token_error:
                    auth.mark_revoked(hub.user_id)
                    return
                continue
            hub.builder.seed(sym, _rows(candles))
            hub.backfilled.add(sym)
            hub.backfill_errors.pop(sym, None)
            await asyncio.sleep(BACKFILL_SPACING_S)
        if all(s in hub.backfilled for s in hub.symbols):
            return


async def _bootstrap_history(hub: Hub, client, table, clock) -> None:
    """At most two calls per symbol: 730 calendar days daily + ten days 5m.

    Retain 320 actual candles per series. No retry storm: failures remain
    visible until an explicit stream restart. Session backfill has its own
    bounded retry policy and goes first.
    """
    for interval, kite_interval, days in (("1d", "day", 730), ("5m", "5minute", 10)):
        for sym in hub.symbols:
            now = clock()
            if auth.load_session(hub.user_id, now) is None:
                return
            key = f"{sym}:{interval}"
            try:
                candles = await asyncio.to_thread(history.gate.fetch, hub.user_id, client,
                                                  table[sym]['token'], kite_interval,
                                                  (now - timedelta(days=days)).astimezone(IST), now.astimezone(IST))
                if auth.load_session(hub.user_id, clock()) is None:
                    return
                # The request's end bounds what the provider observed. A
                # forming row must never become a closed candle merely because
                # this one-shot snapshot is read later (or HTTP crossed close).
                rows = []
                for row in _rows(candles):
                    ts = row[0]
                    local = ts.replace(tzinfo=IST) if ts.tzinfo is None else ts.astimezone(IST)
                    end = (datetime.combine(local.date(), time(NSE_SPEC.close_min // 60,
                                                               NSE_SPEC.close_min % 60), IST)
                           if interval == '1d' else local + timedelta(minutes=5))
                    if end + timedelta(seconds=2) <= now:
                        rows.append(row)
                rows = rows[-320:]
                hub.history.setdefault(interval, {})[sym] = rows
                if not rows:
                    hub.history_errors[key] = "No historical candles available"
            except rest.KiteError as exc:
                hub.history_errors[key] = "Historical candles unavailable; restart stream to retry"
                if exc.token_error:
                    auth.mark_revoked(hub.user_id)
                    return
            except (ValueError, TypeError, IndexError):
                hub.history_errors[key] = "Invalid historical candle response; restart stream to retry"


async def _history_after_session(hub, client, table, clock):
    if hub.backfill_task:
        await asyncio.shield(hub.backfill_task)
    await asyncio.sleep(BACKFILL_SPACING_S)
    await _bootstrap_history(hub, client, table, clock)


class PostbackPump:
    """Runs the order-postback listeners (blocking: Kite REST and the user's
    broker lock) on ONE worker thread per hub, never on the event loop.
    Postbacks arriving while it runs coalesce into a single re-run: the
    listener re-reads Kite's whole order book, so only "something changed"
    matters, not each message."""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self._lock = threading.Lock()
        self._running = False
        self._pending: Optional[Dict[str, Any]] = None
        self.runs = 0

    def submit(self, data: Dict[str, Any]) -> None:
        with self._lock:
            if self._running:
                self._pending = data
                return
            self._running = True
        threading.Thread(target=self._work, args=(data,), daemon=True,
                         name=f"kite-postback-{self.user_id}").start()

    def _work(self, data: Dict[str, Any]) -> None:
        while True:
            for fn in list(order_listeners):
                try:
                    fn(self.user_id, data)
                except Exception:  # noqa: BLE001
                    logger.exception("order postback listener failed")
            with self._lock:
                self.runs += 1
                if self._pending is None:
                    self._running = False
                    return
                data, self._pending = self._pending, None

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._running


def _schedule_backfill(hub: Hub, client, table, clock) -> None:
    if hub.backfill_task is None or hub.backfill_task.done():
        hub.backfill_task = asyncio.create_task(_backfill(hub, client, table, clock),
                                                name=f"kite-backfill-{hub.user_id}")


def stop_at_for(now: datetime, expires_at: datetime) -> datetime:
    end = datetime.combine(now.astimezone(IST).date(), SESSION_END, IST).astimezone(timezone.utc)
    return min(end, expires_at)


async def start(user_id: str, symbols: List[str], *, transport: Optional[rest.Transport] = None,
                connect: Optional[ticker.Connect] = None, now: Optional[datetime] = None) -> Hub:
    """(Re)start the user's hub for ``symbols``. Raises KiteAuthError / KiteError."""
    now = now or _now()
    syms = sorted({bars_mod.nse_symbol(s) for s in symbols})
    if not syms:
        raise ValueError("pick at least one NSE symbol to stream")
    if len(syms) > MAX_SYMBOLS:
        raise ValueError(f"stream at most {MAX_SYMBOLS} symbols")
    client = auth.client_for(user_id, now, transport)
    sess = auth.load_session(user_id, now)
    table = await asyncio.to_thread(rest.nse_instruments, client, now.astimezone(IST).date())
    missing = [s for s in syms if s not in table]
    if missing:
        raise ValueError(f"not NSE equities on Kite: {', '.join(missing[:10])}")
    await stop(user_id)
    hub = Hub(user_id, syms, started_at=now.isoformat())
    tokens = {table[s]["token"]: s for s in syms}
    wall0 = _now()

    def clock() -> datetime:   # the start time plus elapsed wall time (tests pass a start)
        return now + (_now() - wall0)

    def on_state(state: str, detail: Optional[str]) -> None:
        if state == "reconnecting":
            hub.reconnects += 1
            hub.dropped = True
        if state == "auth_failed":
            auth.mark_revoked(user_id)
        if state == "live" and hub.dropped and clock() >= session_open(clock()):
            # ticks were missed while disconnected: refill before serving live again
            hub.dropped = False
            hub.backfilled.clear()
            _schedule_backfill(hub, client, table, clock)
        hub.state, hub.detail = state, detail

    def on_text(msg: Dict[str, Any]) -> None:
        if msg.get("type") == "order":
            hub.pump.submit(msg.get("data") or {})      # never blocks the event loop
        elif msg.get("type") == "error":
            hub.detail = str(msg.get("data"))

    hub.on_state = on_state
    hub.task = asyncio.create_task(ticker.run_ticker(
        api_key=client.api_key, access_token=client.access_token or "", tokens=tokens,
        on_tick=lambda sym, t, at: hub.builder.on_tick(sym, t.ltp, t.volume, at),
        on_text=on_text, on_state=on_state, connect=connect,
        stop_at=stop_at_for(now, datetime.fromisoformat(sess["expires_at"]))),
        name=f"kite-ticker-{user_id}")
    if now < session_open(now):
        hub.backfilled = set(syms)          # nothing of today's session has happened yet
    else:
        _schedule_backfill(hub, client, table, clock)
    _hubs[user_id] = hub
    hub.history_task = asyncio.create_task(_history_after_session(hub, client, table, clock),
                                           name=f"kite-history-{user_id}")
    return hub


async def stop(user_id: str) -> bool:
    hub = _hubs.pop(user_id, None)
    if hub is None:
        return False
    for task in (hub.task, hub.history_task, hub.backfill_task):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
    hub.state = "stopped"
    return True


async def shutdown() -> None:
    for uid in list(_hubs):
        await stop(uid)


def get_hub(user_id: str) -> Optional[Hub]:
    return _hubs.get(user_id)


def freshness(user_id: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Per-symbol last tick and whether bars for it are served live."""
    now = now or _now()
    hub = _hubs.get(user_id)
    if hub is None:
        return {"state": "off", "detail": None, "symbols": [], "reconnects": 0}
    syms = []
    active = auth.load_session(user_id, now) is not None
    for s in hub.symbols:
        last = hub.builder.last_tick.get(s)
        live = active and hub.state == "live" and last is not None and now - last <= STALE_AFTER \
            and s in hub.backfilled
        minute_rows = hub.builder.rows(s, "1m")
        syms.append({"symbol": s, "last_tick_at": last.isoformat() if last else None,
                     "last_price": minute_rows[-1][4] if minute_rows and last else None,
                     "source": SOURCE if live else "delayed:yahoo",
                     "backfilled": s in hub.backfilled,
                     "backfill_error": hub.backfill_errors.get(s),
                     "bars_1m": len(hub.builder.rows(s, "1m"))})
    return {"state": hub.state, "detail": hub.detail, "symbols": syms,
            "reconnects": hub.reconnects, "started_at": hub.started_at,
            "bootstrap": {"total": len(hub.symbols) * 2,
                          "completed": sum(bool(rows) for ss in hub.history.values() for rows in ss.values()),
                          "failed": len(hub.history_errors),
                          "running": hub.history_task is not None and not hub.history_task.done(),
                          "session_ready": len(hub.backfilled)},
            "history_errors": dict(hub.history_errors), "max_symbols": MAX_SYMBOLS}


# ---------------------------------------------------------------- provider

_KITE_INTERVAL = {"1m": "minute", "5m": "5minute", "1d": "day"}
_HISTORY_DAYS = {"1m": 30, "5m": 59, "1d": 730}


def provider(user_id: str, symbol: str, interval: str, kind: str, *,
             now: Optional[datetime] = None, transport: Optional[rest.Transport] = None,
             expected_owner: Optional[str] = None
             ) -> Optional[BarSeries]:
    # Explicit timestamps keep replay/test reads deterministic; ordinary
    # requests must re-read the clock after a blocking provider call.
    clock = _now if now is None else lambda: now
    now = now or _now()
    owner_id = sharing.owner_for(user_id)
    if expected_owner is not None and owner_id != expected_owner:
        return None
    if not access_valid(user_id, owner_id, now):
        return None
    if kind == "session":
        hub = _hubs.get(owner_id)
        if hub is None or symbol not in hub.symbols or interval not in ("1m", "5m"):
            return None
        last = hub.builder.last_tick.get(symbol)
        if hub.state != "live" or last is None or now - last > STALE_AFTER \
                or symbol not in hub.backfilled:
            return None   # bars_source falls back to delayed bars, labelled as such
        rows = hub.builder.rows(symbol, interval)
        if not access_valid(user_id, owner_id, clock()):
            return None
        return BarSeries(symbol, interval, rows, SOURCE,
                         max(r[0] for r in rows).isoformat() if rows else None)
    if kind == "history" and interval in _KITE_INTERVAL:
        try:
            owner_id, client = sharing.data_client(user_id, now, transport)
            table = rest.nse_instruments(client, now.astimezone(IST).date())
            if symbol not in table:
                return None
            candles = history.gate.fetch(owner_id, client, table[symbol]["token"], _KITE_INTERVAL[interval],
                                        (now - timedelta(days=_HISTORY_DAYS[interval]))
                                        .astimezone(IST), now.astimezone(IST))
            sharing.require_access(user_id, owner_id, clock())
            if not access_valid(user_id, owner_id, clock()):
                return None
        except auth.KiteAuthError:
            return None
        except rest.KiteError as exc:
            if exc.token_error:
                auth.mark_revoked(owner_id)
            logger.warning("Kite history failed for %s %s: %s", symbol, interval, exc)
            return None
        rows = _rows(candles)
        if not rows:
            return None
        return BarSeries(symbol, interval, rows, SOURCE, max(r[0] for r in rows).isoformat())
    return None


def access_valid(user_id: str, owner_id: str, now: datetime) -> bool:
    """Recheck both selection/grant and the owner's session, including own reads."""
    return sharing.still_allowed(user_id, owner_id, now) and auth.load_session(owner_id, now) is not None


def install() -> None:
    """Root wiring: register the private provider with the desk bar seam."""
    from apps.api.desk import bars_source

    bars_source.register_live_provider(provider)


def clear_for_tests() -> None:
    _hubs.clear()
    order_listeners.clear()
