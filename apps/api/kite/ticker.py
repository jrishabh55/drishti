"""Kite WebSocket ticks → private 1m/5m NSE-session bars.

Own parser instead of the `kiteconnect` package (Twisted/autobahn). Frame
layout per https://kite.trade/docs/connect/v3/websocket/: a 2-byte big-endian
packet count, then per packet a 2-byte length and the packet. Equity prices
are paise (÷100). Packet sizes: 8 (ltp), 44 (quote), 184 (full, adds the
exchange timestamp at bytes 60-64). A 1-byte message is a heartbeat. Text
messages are JSON: ``order`` postbacks, ``error``, ``message``.

``BarBuilder`` folds ticks into 1-minute bars on the 09:15-aligned NSE grid;
volume is the difference of the cumulative day volume. 5-minute bars are
rolled up from the 1-minute ones. A bar is *final* once its end + 2 s has
passed; the forming bar is exposed too (desk code drops incomplete bars
itself — bars.session_bars).
"""
from __future__ import annotations

import asyncio
import json
import logging
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from apps.api.desk.bars import Row
from apps.api.desk.market.session import NSE, SessionSpec

logger = logging.getLogger(__name__)

WS_URL = "wss://ws.kite.trade"
FINAL_GRACE = timedelta(seconds=2)


@dataclass
class Tick:
    token: int
    ltp: float
    volume: Optional[int] = None          # cumulative day volume (quote/full)
    exchange_ts: Optional[datetime] = None  # full mode only


def parse_binary(frame: bytes, divisor: float = 100.0) -> List[Tick]:
    if len(frame) < 2:
        return []   # heartbeat
    (count,) = struct.unpack_from(">H", frame, 0)
    off, out = 2, []
    for _ in range(count):
        if off + 2 > len(frame):
            raise ValueError("truncated Kite frame: missing packet length")
        (n,) = struct.unpack_from(">H", frame, off)
        pkt = frame[off + 2: off + 2 + n]
        off += 2 + n
        if len(pkt) != n:
            raise ValueError("truncated Kite frame: short packet")
        if n < 8:
            continue
        token, ltp = struct.unpack_from(">iI", pkt, 0)
        tick = Tick(token, ltp / divisor)
        if n in (44, 184):
            (tick.volume,) = struct.unpack_from(">I", pkt, 16)
        if n == 184:
            (ets,) = struct.unpack_from(">I", pkt, 60)
            tick.exchange_ts = datetime.fromtimestamp(ets, timezone.utc) if ets else None
        out.append(tick)
    return out


def parse_text(text: str) -> Dict[str, Any]:
    try:
        msg = json.loads(text)
    except ValueError:
        return {"type": "unknown", "data": text}
    return msg if isinstance(msg, dict) else {"type": "unknown", "data": msg}


class BarBuilder:
    """Per-symbol 1-minute bars for the current session. Not thread-safe;
    one builder per user hub, fed from that hub's single task."""

    def __init__(self, spec: SessionSpec = NSE) -> None:
        self.spec = spec
        self._bars: Dict[str, Dict[datetime, list]] = {}
        self._cum: Dict[str, int] = {}
        self.last_tick: Dict[str, datetime] = {}

    def on_tick(self, symbol: str, ltp: float, cum_volume: Optional[int], at: datetime) -> bool:
        local = at.astimezone(self.spec.tz)
        mins = local.hour * 60 + local.minute
        if not (self.spec.open_min <= mins < self.spec.close_min) or ltp <= 0:
            return False
        start = local.replace(second=0, microsecond=0)
        bars = self._bars.setdefault(symbol, {})
        if bars and min(bars).date() != start.date():
            bars.clear()           # new session
            self._cum.pop(symbol, None)
        # ponytail: a fresh hub's first tick has no previous cumulative volume,
        # so its delta is 0 (reconnects keep the builder, so they don't lose any).
        prev = self._cum.get(symbol)
        dv = max(0, cum_volume - prev) if (cum_volume is not None and prev is not None) else 0
        if cum_volume is not None:
            self._cum[symbol] = cum_volume
        b = bars.get(start)
        if b is None:
            bars[start] = [ltp, ltp, ltp, ltp, float(dv)]
        else:
            b[1], b[2], b[3] = max(b[1], ltp), min(b[2], ltp), ltp
            b[4] += dv
        self.last_tick[symbol] = at
        return True

    def rows(self, symbol: str, interval: str) -> List[Row]:
        one = sorted(self._bars.get(symbol, {}).items())
        if interval == "1m":
            return [(ts, *v) for ts, v in one]  # type: ignore[misc]
        if interval != "5m":
            raise ValueError("live bars are 1m or 5m")
        out: Dict[datetime, list] = {}
        for ts, (o, h, lo, c, v) in one:
            k = (ts.hour * 60 + ts.minute - self.spec.open_min) // 5
            start = ts.replace(hour=0, minute=0) + timedelta(minutes=self.spec.open_min + 5 * k)
            b = out.get(start)
            if b is None:
                out[start] = [o, h, lo, c, v]
            else:
                b[1], b[2], b[3] = max(b[1], h), min(b[2], lo), c
                b[4] += v
        return [(ts, *v) for ts, v in sorted(out.items())]  # type: ignore[misc]

    @staticmethod
    def final(rows: List[Row], interval: str, now: datetime) -> List[Row]:
        size = timedelta(minutes=1 if interval == "1m" else 5)
        return [r for r in rows if r[0] + size + FINAL_GRACE <= now]


# ---------------------------------------------------------------- connection

Connect = Callable[[str], Awaitable[Any]]


async def _default_connect(url: str):  # pragma: no cover - network
    import websockets

    return await websockets.connect(url, open_timeout=10, ping_interval=None, max_size=2 ** 22)


async def run_ticker(*, api_key: str, access_token: str, tokens: Dict[int, str],
                     on_tick: Callable[[str, Tick, datetime], None],
                     on_text: Callable[[Dict[str, Any]], None],
                     on_state: Callable[[str, Optional[str]], None],
                     stop_at: datetime, connect: Optional[Connect] = None,
                     clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                     backoff: Tuple[float, ...] = (1, 2, 5, 10, 30)) -> str:
    """Stream until ``stop_at``, cancellation, or an auth failure. Reconnects
    with backoff on drops. Returns why it stopped: ``stopped`` | ``auth``."""
    from urllib.parse import urlencode

    url = f"{WS_URL}?" + urlencode({"api_key": api_key, "access_token": access_token})
    connect = connect or _default_connect
    attempt = 0
    while clock() < stop_at:
        if attempt == 0:
            on_state("connecting", None)
        try:
            ws = await connect(url)
        except Exception as exc:  # noqa: BLE001
            if _is_auth_failure(exc):
                on_state("auth_failed", "Kite refused the session (token expired or revoked)")
                return "auth"
            on_state("reconnecting", f"connect failed: {type(exc).__name__}")
            await asyncio.sleep(backoff[min(attempt, len(backoff) - 1)])
            attempt += 1
            continue
        try:
            await ws.send(json.dumps({"a": "subscribe", "v": list(tokens)}))
            await ws.send(json.dumps({"a": "mode", "v": ["quote", list(tokens)]}))
            on_state("live", None)
            attempt = 0
            async for msg in ws:
                now = clock()
                if isinstance(msg, (bytes, bytearray)):
                    for t in parse_binary(bytes(msg)):
                        sym = tokens.get(t.token)
                        if sym:
                            on_tick(sym, t, t.exchange_ts or now)
                else:
                    on_text(parse_text(msg))
                if now >= stop_at:
                    break
            else:
                on_state("reconnecting", "Kite closed the connection")
        except asyncio.CancelledError:
            await _close(ws)
            raise
        except Exception as exc:  # noqa: BLE001
            on_state("reconnecting", f"stream dropped: {type(exc).__name__}")
        await _close(ws)
        if clock() >= stop_at:
            break
        await asyncio.sleep(backoff[min(attempt, len(backoff) - 1)])
        attempt += 1
    on_state("stopped", None)
    return "stopped"


def _is_auth_failure(exc: Exception) -> bool:
    code = getattr(getattr(exc, "response", None), "status_code", None) or \
        getattr(exc, "status_code", None)
    return code in (401, 403)


async def _close(ws) -> None:
    try:
        await ws.close()
    except Exception:  # noqa: BLE001
        pass
