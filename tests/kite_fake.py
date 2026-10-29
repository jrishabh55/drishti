"""A scripted fake of the Kite Connect REST API + ticker, for tests only.

Never reaches the network. Records every call so tests can assert that a
refused user produced none.
"""
from __future__ import annotations

import asyncio
import json
import struct
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl

from apps.api.kite import rest

INSTRUMENTS = (
    "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,"
    "lot_size,instrument_type,segment,exchange\n"
    "779521,3045,SBIN,STATE BANK OF INDIA,0,,0,0.05,1,EQ,NSE,NSE\n"
    "738561,2885,RELIANCE,RELIANCE INDUSTRIES,0,,0,0.1,1,EQ,NSE,NSE\n"
)


class FakeKite:
    def __init__(self, api_key="kiteapikey1", api_secret="kitesecret01"):
        self.api_key, self.api_secret = api_key, api_secret
        self.calls: List[tuple] = []
        self.tokens_issued: List[str] = []
        self.revoked: set = set()
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.reject_next: Dict[str, str] = {}   # order_type -> message
        self.fail_cancel = False
        self.candles: List[list] = []

    # ------------------------------------------------------------ transport
    def __call__(self, method, url, params, data, headers):
        path = url[len(rest.ROOT):]
        self.calls.append((method, path, dict(params or []), dict(data or {})))
        auth = headers.get("Authorization", "")
        if path != "/session/token" or method != "POST":
            token = auth.split(":", 1)[1] if ":" in auth else (dict(params or []).get("access_token"))
            if token not in self.tokens_issued or token in self.revoked:
                return 403, json.dumps({"status": "error", "message": "Incorrect api_key or access_token.",
                                        "error_type": "TokenException"})
        if method == "POST" and path == "/session/token":
            want = rest.checksum(self.api_key, data["request_token"], self.api_secret)
            if data["checksum"] != want or data["api_key"] != self.api_key:
                return 403, json.dumps({"status": "error", "message": "Invalid checksum",
                                        "error_type": "TokenException"})
            tok = f"acc{len(self.tokens_issued)}tok"
            self.tokens_issued.append(tok)
            return self._ok({"access_token": tok, "user_id": "AB1234", "user_name": "Test User"})
        if method == "DELETE" and path == "/session/token":
            self.revoked.add(dict(params)["access_token"])
            return self._ok(True)
        if path == "/instruments/NSE":
            return 200, INSTRUMENTS
        if path == "/quote":
            return self._ok({i: {"instrument_token": 1, "last_price": 801.5, "volume": 1000,
                                 "ohlc": {"open": 800}} for _, i in params})
        if path.startswith("/instruments/historical/"):
            return self._ok({"candles": self.candles})
        if method == "POST" and path == "/orders/regular":
            msg = self.reject_next.pop(data["order_type"], None)
            if msg:
                return 400, json.dumps({"status": "error", "message": msg,
                                        "error_type": "InputException"})
            oid = f"ord{len(self.orders) + 1}"
            self.orders[oid] = dict(data, order_id=oid, status="OPEN", filled_quantity=0,
                                    average_price=0)
            if data["order_type"] in ("SL-M", "SL"):
                self.orders[oid]["status"] = "TRIGGER PENDING"
            return self._ok({"order_id": oid})
        if method == "DELETE" and path.startswith("/orders/regular/"):
            if self.fail_cancel:
                return 400, json.dumps({"status": "error", "message": "nope",
                                        "error_type": "OrderException"})
            o = self.orders[path.rsplit("/", 1)[1]]
            if o["status"] not in ("COMPLETE", "CANCELLED", "REJECTED"):
                o["status"] = "CANCELLED"
            return self._ok({"order_id": o["order_id"]})
        if path == "/orders":
            return self._ok(list(self.orders.values()))
        if path == "/portfolio/positions":
            return self._ok({"net": [], "day": []})
        return 404, json.dumps({"status": "error", "message": "no route", "error_type": "GeneralException"})

    @staticmethod
    def _ok(data):
        return 200, json.dumps({"status": "success", "data": data})

    # ------------------------------------------------------------ scripting
    def by_tag_suffix(self, letter: str) -> Dict[str, Any]:
        hits = [o for o in self.orders.values() if o.get("tag", "").endswith(letter)]
        assert hits, f"no order with leg {letter}"
        return hits[-1]

    def fill(self, letter: str, price: float, qty: Optional[int] = None) -> None:
        o = self.by_tag_suffix(letter)
        o.update(status="COMPLETE", filled_quantity=qty or int(o["quantity"]), average_price=price)

    def placed(self) -> List[Dict[str, Any]]:
        return [c[3] for c in self.calls if c[0] == "POST" and c[1] == "/orders/regular"]


# ---------------------------------------------------------------- ticker

def quote_packet(token: int, ltp_paise: int, volume: int) -> bytes:
    return struct.pack(">iIIIIIIIIII", token, ltp_paise, 1, ltp_paise, volume, 0, 0,
                       ltp_paise, ltp_paise, ltp_paise, ltp_paise)


def frame(*packets: bytes) -> bytes:
    out = struct.pack(">H", len(packets))
    for p in packets:
        out += struct.pack(">H", len(p)) + p
    return out


class FakeSocket:
    """An async-iterable websocket that yields scripted messages, then waits."""

    def __init__(self, messages: List[Any], hold: bool = True):
        self.messages = list(messages)
        self.sent: List[str] = []
        self.closed = False
        self.hold = hold

    async def send(self, msg):
        self.sent.append(msg)

    async def close(self):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.messages:
            await asyncio.sleep(0)
            return self.messages.pop(0)
        if self.hold:
            await asyncio.sleep(3600)
        raise StopAsyncIteration
