"""A scripted fake of the Kite Connect REST API + ticker, for tests only.

Never reaches the network. Records every call so tests can assert that a
refused user produced none.
"""
from __future__ import annotations

import asyncio
import json
import struct
from typing import Any, Dict, List, Optional

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
        #: scripted transport failures: (method, path-prefix, predicate(data)) -> "timeout"
        #: (Kite received the request, the answer is lost) | "down" (never received) | "5xx"
        self.faults: List[tuple] = []
        #: called after each request is applied, to script fills between requests
        self.after: List[Any] = []
        #: MIS quantity the user holds outside the engine (manual trades), per symbol
        self.manual: Dict[str, int] = {}
        self.positions_down = False
        #: order types whose new orders Kite accepts but hides from GET /orders
        #: until ``reveal()`` — a lagging order book
        self.hidden_types: set = set()
        self._hidden: set = set()

    # ------------------------------------------------------------ transport
    def __call__(self, method, url, params, data, headers):
        path = url[len(rest.ROOT):]
        for i, (m, prefix, pred, kind) in enumerate(self.faults):
            if m == method and path.startswith(prefix) and pred(dict(data or {})):
                self.faults.pop(i)
                if kind == "down":
                    raise ConnectionError("connection refused")
                out = self._apply(method, url, path, params, data, headers)
                if kind == "timeout":
                    raise TimeoutError("read timed out")
                return 502, json.dumps({"status": "error", "message": "OMS unreachable",
                                        "error_type": "NetworkException"})
        out = self._apply(method, url, path, params, data, headers)
        for fn in list(self.after):
            fn(method, path, data)
        return out

    def _apply(self, method, url, path, params, data, headers):
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
            if data["order_type"] in self.hidden_types:
                self._hidden.add(oid)
            return self._ok({"order_id": oid})
        if method == "PUT" and path.startswith("/orders/regular/"):
            o = self.orders[path.rsplit("/", 1)[1]]
            if o["status"] in ("COMPLETE", "CANCELLED", "REJECTED"):
                return 400, json.dumps({"status": "error", "message": "Order cannot be modified",
                                        "error_type": "OrderException"})
            o["quantity"] = int(data["quantity"])
            return self._ok({"order_id": o["order_id"]})
        if method == "DELETE" and path.startswith("/orders/regular/"):
            if self.fail_cancel:
                return 400, json.dumps({"status": "error", "message": "nope",
                                        "error_type": "OrderException"})
            o = self.orders[path.rsplit("/", 1)[1]]
            if o["status"] in ("COMPLETE", "CANCELLED", "REJECTED"):   # as Kite answers
                return 400, json.dumps({"status": "error", "message": f"Order is {o['status']}",
                                        "error_type": "OrderException"})
            o["status"] = "CANCELLED"
            return self._ok({"order_id": o["order_id"]})
        if path == "/orders":
            return self._ok([o for k, o in self.orders.items() if k not in self._hidden])
        if path == "/portfolio/positions":
            if self.positions_down:
                return 503, json.dumps({"status": "error", "message": "positions unavailable",
                                        "error_type": "NetworkException"})
            net: Dict[str, int] = dict(self.manual)
            for o in self.orders.values():
                q = int(o["filled_quantity"] or 0)
                sign = 1 if o["transaction_type"] == "BUY" else -1
                net[o["tradingsymbol"]] = net.get(o["tradingsymbol"], 0) + sign * q
            rows = [{"tradingsymbol": k, "exchange": "NSE", "product": "MIS", "quantity": v}
                    for k, v in net.items()]
            return self._ok({"net": rows, "day": rows})
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
        """Fill ``qty`` more (default: the rest). COMPLETE once fully filled."""
        o = self.by_tag_suffix(letter)
        done = int(o["filled_quantity"] or 0)
        add = qty if qty is not None else int(o["quantity"]) - done
        total = done + add
        avg = ((o["average_price"] or 0) * done + price * add) / total if total else 0
        o.update(filled_quantity=total, average_price=round(avg, 4),
                 status="COMPLETE" if total >= int(o["quantity"]) else "OPEN")

    def reveal(self) -> None:
        """The order book catches up: every hidden order becomes visible."""
        self._hidden.clear()
        self.hidden_types.clear()

    def sold(self) -> int:
        return sum(int(o["filled_quantity"]) for o in self.orders.values()
                   if o["transaction_type"] == "SELL")

    def bought(self) -> int:
        return sum(int(o["filled_quantity"]) for o in self.orders.values()
                   if o["transaction_type"] == "BUY")

    def sell_exposure(self) -> int:
        """Shares a fill of every working SELL could sell, on top of what's sold."""
        return sum(int(o["quantity"]) - int(o["filled_quantity"]) for o in self.orders.values()
                   if o["transaction_type"] == "SELL"
                   and o["status"] not in ("COMPLETE", "CANCELLED", "REJECTED"))

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

    async def recv(self):
        try:
            return await self.__anext__()
        except StopAsyncIteration:
            raise ConnectionError("connection closed") from None

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
