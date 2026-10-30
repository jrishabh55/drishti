"""Minimal Kite Connect v3 REST client (https://kite.trade/docs/connect/v3/).

No `kiteconnect` dependency: a handful of endpoints over httpx. The transport
is injectable — tests pass a fake Kite (tests/fakes_kite.py) so no request
ever reaches Zerodha from the test suite.

Every response is ``{"status": "success", "data": ...}`` or
``{"status": "error", "message": ..., "error_type": ...}``; errors become
:class:`KiteError`. ``TokenException`` means the daily session is gone
(expired at 06:00 IST, logged out, or revoked) — callers mark the session
expired on it.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import threading
from datetime import date, datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

ROOT = "https://api.kite.trade"
LOGIN_URL = "https://kite.zerodha.com/connect/login"

#: (method, url, params, data, headers) -> (http status, body text)
Transport = Callable[[str, str, Optional[list], Optional[dict], Dict[str, str]], Tuple[int, str]]


class KiteError(RuntimeError):
    def __init__(self, message: str, error_type: str = "GeneralException", status: int = 0):
        super().__init__(message)
        self.error_type = error_type
        self.status = status

    @property
    def token_error(self) -> bool:
        return self.error_type == "TokenException" or self.status == 403


def _httpx_transport(method, url, params, data, headers):  # pragma: no cover - network
    import httpx

    r = httpx.request(method, url, params=params, data=data, headers=headers, timeout=10.0)
    return r.status_code, r.text


def checksum(api_key: str, request_token: str, api_secret: str) -> str:
    return hashlib.sha256(f"{api_key}{request_token}{api_secret}".encode()).hexdigest()


class KiteClient:
    def __init__(self, api_key: str, access_token: Optional[str] = None,
                 transport: Optional[Transport] = None) -> None:
        self.api_key = api_key
        self.access_token = access_token
        self._transport = transport or _httpx_transport

    def _call(self, method: str, path: str, params: Optional[Iterable] = None,
              data: Optional[dict] = None, *, raw: bool = False) -> Any:
        headers = {"X-Kite-Version": "3"}
        if self.access_token:
            headers["Authorization"] = f"token {self.api_key}:{self.access_token}"
        try:
            status, body = self._transport(method, ROOT + path, list(params or []) or None,
                                           data, headers)
        except KiteError:
            raise
        except Exception as exc:  # noqa: BLE001 — network failure is a plain KiteError
            raise KiteError(f"could not reach Kite: {exc}", "NetworkException") from exc
        if raw and 200 <= status < 300:
            return body
        try:
            payload = json.loads(body) if body else {}
        except ValueError:
            payload = {}
        if not (200 <= status < 300) or payload.get("status") == "error":
            raise KiteError(payload.get("message") or f"Kite answered HTTP {status}",
                            payload.get("error_type") or "GeneralException", status)
        return payload.get("data")

    # ------------------------------------------------------------ session
    def generate_session(self, request_token: str, api_secret: str) -> Dict[str, Any]:
        return self._call("POST", "/session/token", data={
            "api_key": self.api_key, "request_token": request_token,
            "checksum": checksum(self.api_key, request_token, api_secret)})

    def invalidate_session(self) -> None:
        self._call("DELETE", "/session/token",
                   [("api_key", self.api_key), ("access_token", self.access_token or "")])

    def profile(self) -> Dict[str, Any]:
        return self._call("GET", "/user/profile")

    # ------------------------------------------------------------ market data
    def quote(self, symbols: List[str]) -> Dict[str, Any]:
        """Full quotes for NSE equities, keyed by bare symbol."""
        data = self._call("GET", "/quote", [("i", f"NSE:{s}") for s in symbols]) or {}
        return {k.split(":", 1)[1]: v for k, v in data.items()}

    def historical(self, token: int, interval: str, start: datetime, end: datetime
                   ) -> List[list]:
        """Candles ``[ts, o, h, l, c, v]``; interval is Kite's (minute, 5minute, day)."""
        fmt = "%Y-%m-%d %H:%M:%S"
        data = self._call("GET", f"/instruments/historical/{int(token)}/{interval}",
                          [("from", start.strftime(fmt)), ("to", end.strftime(fmt))])
        return (data or {}).get("candles") or []

    def instruments_csv(self, exchange: str = "NSE") -> str:
        return self._call("GET", f"/instruments/{exchange}", raw=True)

    # ------------------------------------------------------------ orders
    def place_order(self, variety: str = "regular", **params: Any) -> str:
        clean = {k: v for k, v in params.items() if v is not None}
        return self._call("POST", f"/orders/{variety}", data=clean)["order_id"]

    def modify_order(self, order_id: str, variety: str = "regular", **params: Any) -> None:
        """PUT /orders/{variety}/{id}; ``quantity`` is the order's new TOTAL."""
        self._call("PUT", f"/orders/{variety}/{order_id}",
                   data={k: v for k, v in params.items() if v is not None})

    def cancel_order(self, order_id: str, variety: str = "regular") -> None:
        self._call("DELETE", f"/orders/{variety}/{order_id}")

    def orders(self) -> List[Dict[str, Any]]:
        return self._call("GET", "/orders") or []

    def positions(self) -> Dict[str, Any]:
        return self._call("GET", "/portfolio/positions") or {}

    def margins_equity(self) -> Dict[str, Any]:
        """The equity segment's funds; ``available.live_balance`` is the
        current available balance (Kite Connect v3 /user/margins)."""
        return self._call("GET", "/user/margins/equity") or {}


# ---------------------------------------------------------------- instruments

# ponytail: the NSE instrument master is public reference data (tokens, tick
# sizes), not licensed market data, so one in-memory copy per IST day is
# shared across users. Downloaded with whichever user's session asks first.
_instruments: Dict[str, Tuple[date, Dict[str, Dict[str, Any]]]] = {}
_inst_lock = threading.Lock()


def parse_instruments(text: str) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("instrument_type") != "EQ" or row.get("segment") != "NSE":
            continue
        out[row["tradingsymbol"]] = {"token": int(row["instrument_token"]),
                                     "tick_size": float(row.get("tick_size") or 0.05),
                                     "lot_size": int(row.get("lot_size") or 1)}
    return out


def nse_instruments(client: KiteClient, today: date) -> Dict[str, Dict[str, Any]]:
    with _inst_lock:
        hit = _instruments.get("NSE")
        if hit and hit[0] == today:
            return hit[1]
    table = parse_instruments(client.instruments_csv("NSE"))
    if not table:
        raise KiteError("Kite returned an empty NSE instrument list", "DataException")
    with _inst_lock:
        _instruments["NSE"] = (today, table)
    return table


def clear_instruments_for_tests() -> None:
    with _inst_lock:
        _instruments.clear()
