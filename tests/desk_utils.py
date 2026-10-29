"""Synthetic NSE bars + app wiring shared by the Trade Desk tests."""
from __future__ import annotations

import gzip
import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.desk.market.session import NSE, SessionSpec

FIX = Path(__file__).parent / "fixtures" / "desk"


def upstream_tsla_result() -> dict:
    with gzip.open(FIX / "upstream-TSLA-60d5m.json.gz", "rt", encoding="utf-8") as fh:
        return json.load(fh)["chart"]["result"][0]


def trading_days(end: date, n: int, spec: SessionSpec = NSE) -> list[date]:
    """The n trading days ending at ``end`` (inclusive), oldest first."""
    out, d = [], end
    while len(out) < n:
        if spec.is_trading_day(d):
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def session_rows(day: date, closes: list[float], spec: SessionSpec = NSE,
                 highs=None, lows=None, opens=None, volume: float = 1000.0) -> list:
    """Rows for consecutive bars from the session open of ``day``."""
    rows = []
    for k, c in enumerate(closes):
        ts = spec.local(day, spec.bar_start_min(k))
        o = opens[k] if opens else (closes[k - 1] if k else c)
        h = highs[k] if highs else max(o, c) + 0.05
        lo = lows[k] if lows else min(o, c) - 0.05
        rows.append((ts, o, h, lo, c, volume))
    return rows


def random_walk_sessions(end: date, n_days: int, start_price: float = 1000.0,
                         seed: int = 7, spec: SessionSpec = NSE, vol: float = 0.0015) -> list:
    """n_days full sessions of a seeded random walk (rows), oldest first."""
    rng = random.Random(seed)
    rows, p = [], start_price
    for d in trading_days(end, n_days, spec):
        closes = []
        for _ in range(spec.n_bars):
            p *= 1 + rng.gauss(0, vol)
            closes.append(round(p, 2))
        rows += session_rows(d, closes, spec)
    return rows


def daily_rows(end: date, closes: list[float]) -> list:
    days = trading_days(end, len(closes))
    rows = []
    for i, (d, c) in enumerate(zip(days, closes)):
        o = closes[i - 1] if i else c
        ts = datetime.combine(d, datetime.min.time(), NSE.tz)
        rows.append((ts, o, max(o, c) * 1.005, min(o, c) * 0.995, c, 1e6))
    return rows


def desk_client(router, user: dict | None = None) -> tuple[TestClient, dict]:
    current = user or {"id": "user_a"}
    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):  # noqa: ANN001
        request.state.user_id = current["id"]
        return await call_next(request)

    app.include_router(router, prefix="/api")
    return TestClient(app), current
