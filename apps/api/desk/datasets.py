"""NSE/BSE dataset catalog + fetch client (spec §3).

Server first: every dataset has ``route="server"`` until the spike
(scripts/desk_spike.py, run FROM THE DEPLOYED SERVER) shows NSE/BSE block
the server's IP for it. Only then is it flipped to ``"helper"`` and fetched
through the user's Drishti helper (the ``__dataset__`` relay provider —
built only if the spike leaves any dataset on that route).

Every URL here is a pinned template; callers pass a dataset NAME and typed
params, never a URL. Responses are cached in desk.sqlite ``feed_cache``
(shared: public data), with a short negative cache so an NSE outage can't
slow every risk check down.

VERIFY: endpoints and response formats are from public knowledge of NSE's
site and are UNVERIFIED until the spike records fixtures. Parsers below
are written tolerant (header-name matching, not column positions) and are
tested against the assumed formats; re-test them against real fixtures.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Literal, Optional

logger = logging.getLogger(__name__)

Route = Literal["server", "helper"]
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
NEGATIVE_TTL_S = 600
TIMEOUT_S = 6


class FeedUnavailable(RuntimeError):
    """``reason`` is one of: source_error | helper_offline | helper_outdated |
    helper_route_not_built | bad_params."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


_SYMBOL = re.compile(r"^[A-Z0-9&\-]{1,20}$")


def _sym(v: Any) -> str:
    s = str(v or "").upper()
    if not _SYMBOL.match(s):
        raise FeedUnavailable("bad_params", f"{v!r} is not an NSE symbol")
    return s


def _ddmmyyyy(v: Any) -> str:
    try:
        return date.fromisoformat(str(v)).strftime("%d-%m-%Y")
    except ValueError:
        raise FeedUnavailable("bad_params", f"{v!r} is not a date like 2026-10-28") from None


@dataclass(frozen=True)
class Dataset:
    name: str
    url: str                                  # template; {params} filled by validators
    kind: Literal["csv", "json"]
    ttl_s: int
    params: Dict[str, Callable[[Any], str]] = field(default_factory=dict)
    route: Route = "server"
    warmup: Optional[str] = None              # homepage to visit first for cookies
    referer: Optional[str] = None
    used_by: str = ""


NSE_HOME = "https://www.nseindia.com/"
CATALOG: Dict[str, Dataset] = {d.name: d for d in (
    Dataset("nse.fo_ban", "https://nsearchives.nseindia.com/content/fo/fo_secban.csv",
            "csv", 6 * 3600, used_by="risk (warn)"),
    Dataset("nse.price_bands", "https://nsearchives.nseindia.com/content/equities/sec_list.csv",
            "csv", 6 * 3600, used_by="risk (circuit headroom), Time Machine clamp"),
    Dataset("nse.bulk_deals", "https://nsearchives.nseindia.com/content/equities/bulk.csv",
            "csv", 3600, used_by="insider tracker"),
    Dataset("nse.block_deals", "https://nsearchives.nseindia.com/content/equities/block.csv",
            "csv", 3600, used_by="insider tracker"),
    Dataset("nse.announcements",
            "https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={symbol}",
            "json", 300, {"symbol": _sym}, warmup=NSE_HOME, used_by="news"),
    Dataset("nse.board_meetings",
            "https://www.nseindia.com/api/corporate-board-meetings?index=equities",
            "json", 3 * 3600, warmup=NSE_HOME, used_by="results-week warning"),
    Dataset("nse.insider_pit",
            "https://www.nseindia.com/api/corporates-pit?index=equities&from_date={from}&to_date={to}",
            "json", 1800, {"from": _ddmmyyyy, "to": _ddmmyyyy}, warmup=NSE_HOME,
            used_by="insider tracker"),
    Dataset("nse.financial_results",
            "https://www.nseindia.com/api/corporates-financial-results?index=equities"
            "&period=Quarterly&symbol={symbol}",
            "json", 6 * 3600, {"symbol": _sym}, warmup=NSE_HOME, used_by="results analyst"),
    Dataset("nse.asm", "https://www.nseindia.com/api/reportASM", "json", 6 * 3600,
            warmup=NSE_HOME, used_by="risk (block)"),
    Dataset("nse.gsm", "https://www.nseindia.com/api/reportGSM", "json", 6 * 3600,
            warmup=NSE_HOME, used_by="risk (block)"),
    Dataset("nse.holidays", "https://www.nseindia.com/api/holiday-master?type=trading",
            "json", 24 * 3600, warmup=NSE_HOME, used_by="session calendar"),
)}


def build_url(ds: Dataset, params: Dict[str, Any]) -> str:
    extra = set(params) - set(ds.params)
    if extra:
        raise FeedUnavailable("bad_params", f"{ds.name} takes no {sorted(extra)}")
    vals = {}
    for k, fn in ds.params.items():
        if k not in params:
            raise FeedUnavailable("bad_params", f"{ds.name} needs {k}")
        vals[k] = fn(params[k])
    return ds.url.format(**vals)


def params_hash(params: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()
                          ).hexdigest()[:24]


# ---------------------------------------------------------------- server-direct fetch

_sessions: Dict[str, Any] = {}
_warmed: Dict[str, float] = {}
_last_call: Dict[str, float] = {}
_neg: Dict[str, float] = {}
_lock = threading.Lock()


def _http_get(ds: Dataset, url: str) -> bytes:  # pragma: no cover - network
    import requests
    from urllib.parse import urlsplit

    host = urlsplit(url).hostname or ""
    with _lock:
        s = _sessions.get(host)
        if s is None:
            s = requests.Session()
            s.headers.update({"User-Agent": UA, "Accept": "*/*",
                              "Accept-Language": "en-IN,en;q=0.9"})
            _sessions[host] = s
        wait = 1.0 - (time.monotonic() - _last_call.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    headers = {"Referer": ds.referer} if ds.referer else {}
    if ds.warmup and time.monotonic() - _warmed.get(host, 0) > 600:
        s.get(ds.warmup, timeout=TIMEOUT_S)
        _warmed[host] = time.monotonic()
    r = s.get(url, timeout=TIMEOUT_S, headers=headers, allow_redirects=False)
    _last_call[host] = time.monotonic()
    if r.status_code in (401, 403) and ds.warmup:
        s.get(ds.warmup, timeout=TIMEOUT_S)
        _warmed[host] = time.monotonic()
        r = s.get(url, timeout=TIMEOUT_S, headers=headers, allow_redirects=False)
    if r.status_code != 200:
        raise FeedUnavailable("source_error", f"{ds.name}: HTTP {r.status_code}")
    return r.content[:2_000_000]


def fetch(name: str, params: Optional[Dict[str, Any]] = None, *, user_id: str = "",
          store=None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """{"text": str, "fetched_at": iso, "cached": bool, "route": Route}. Raises FeedUnavailable."""
    from apps.api.desk.store import get_desk_store

    if name not in CATALOG:
        raise FeedUnavailable("bad_params", f"unknown dataset {name!r}")
    ds = CATALOG[name]
    params = dict(params or {})
    url = build_url(ds, params)
    store = store or get_desk_store()
    now = now or datetime.now(timezone.utc)
    key = params_hash(params)
    cached = store.get_feed(name, key)
    if cached:
        age = (now - datetime.fromisoformat(cached["fetched_at"])).total_seconds()
        if age < ds.ttl_s:
            return {"text": cached["payload"], "fetched_at": cached["fetched_at"],
                    "cached": True, "route": ds.route}
    neg_key = f"{name}:{key}"
    if _neg.get(neg_key, 0) > time.monotonic():
        if cached:
            return {"text": cached["payload"], "fetched_at": cached["fetched_at"],
                    "cached": True, "route": ds.route, "stale": True}
        raise FeedUnavailable("source_error", f"{name} failed recently; retrying later")
    try:
        if ds.route == "helper":
            raise FeedUnavailable(
                "helper_route_not_built",
                f"{name} is routed through the Drishti helper, which isn't built yet — run "
                "scripts/desk_spike.py from the server and see the spec §3")
        body = _http_get(ds, url)
    except FeedUnavailable as exc:
        _neg[neg_key] = time.monotonic() + NEGATIVE_TTL_S
        if cached and exc.reason == "source_error":
            return {"text": cached["payload"], "fetched_at": cached["fetched_at"],
                    "cached": True, "route": ds.route, "stale": True}
        raise
    except Exception as exc:  # noqa: BLE001 — network errors become a typed reason
        _neg[neg_key] = time.monotonic() + NEGATIVE_TTL_S
        if cached:
            return {"text": cached["payload"], "fetched_at": cached["fetched_at"],
                    "cached": True, "route": ds.route, "stale": True}
        raise FeedUnavailable("source_error", f"{name}: {type(exc).__name__}: {exc}") from exc
    text = body.decode("utf-8", "replace")
    store.put_feed(name, key, text, fetched_at=now.isoformat())
    return {"text": text, "fetched_at": now.isoformat(), "cached": False, "route": ds.route}


def clear_negative_cache_for_tests() -> None:
    _neg.clear()


# ---------------------------------------------------------------- parsers (assumed formats)

def _norm(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", h.lower())


def _dict_rows(text: str) -> List[Dict[str, str]]:
    """CSV -> dict rows with normalised header keys; skips title lines above the header."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    for i, ln in enumerate(lines):
        if "symbol" in ln.lower():
            reader = csv.DictReader(io.StringIO("\n".join(lines[i:])))
            return [{_norm(k or ""): (v or "").strip() for k, v in row.items()} for row in reader]
    return []


def parse_fo_ban(text: str) -> List[str]:
    """fo_secban.csv: a title line ("Securities in Ban For Trade Date …") then
    ``n,SYMBOL`` rows. Returns symbols."""
    out = []
    for ln in text.splitlines():
        parts = [p.strip() for p in ln.split(",")]
        if len(parts) >= 2 and parts[0].isdigit() and _SYMBOL.match(parts[1].upper()):
            out.append(parts[1].upper())
    return out


def parse_price_bands(text: str) -> Dict[str, Dict[str, Any]]:
    """sec_list.csv: Symbol, Series, Security Name, Band, Remarks.
    Band is a percent ("5", "20") or "No Band" -> None."""
    out: Dict[str, Dict[str, Any]] = {}
    for row in _dict_rows(text):
        sym = row.get("symbol", "").upper()
        if not sym:
            continue
        band_raw = row.get("band") or row.get("pricebandpercent") or ""
        try:
            band = float(band_raw)
        except ValueError:
            band = None
        series = row.get("series", "").upper()
        cur = out.get(sym)
        if cur is None or series == "EQ":
            out[sym] = {"band_pct": band, "series": series or None}
    return out


def parse_deals(text: str) -> List[Dict[str, Any]]:
    """bulk.csv / block.csv: Date, Symbol, Security Name, Client Name,
    Buy/Sell, Quantity Traded, Trade Price / Wght. Avg. Price, Remarks."""
    out = []
    for row in _dict_rows(text):
        sym = row.get("symbol", "").upper()
        if not sym:
            continue
        qty_raw = (row.get("quantitytraded") or row.get("quantity") or "0").replace(",", "")
        px_raw = next((v for k, v in row.items() if k.startswith("tradeprice")), "0")
        try:
            qty, px = int(float(qty_raw)), float(px_raw.replace(",", ""))
        except ValueError:
            continue
        out.append({"date": row.get("date"), "symbol": sym, "name": row.get("securityname"),
                    "client": row.get("clientname"),
                    "side": (row.get("buysell") or "").upper(), "qty": qty, "price": px,
                    "value_cr": round(qty * px / 1e7, 2)})
    return out


# ---------------------------------------------------------------- facts for the risk check

def nse_facts(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """Circuit band (+ upper/lower from the last daily close), series and the
    F&O ban flag for each symbol. Missing data -> the symbol is absent, and
    the risk check reports "skipped" for it."""
    out: Dict[str, Dict[str, Any]] = {}
    try:
        bands = parse_price_bands(fetch("nse.price_bands")["text"])
    except FeedUnavailable as exc:
        logger.info("price bands unavailable: %s", exc)
        bands = {}
    try:
        ban = set(parse_fo_ban(fetch("nse.fo_ban")["text"]))
    except FeedUnavailable as exc:
        logger.info("F&O ban list unavailable: %s", exc)
        ban = None
    if not bands and ban is None:
        return out
    closes = _last_closes(symbols)
    for sym in symbols:
        facts: Dict[str, Any] = {}
        b = bands.get(sym)
        if b:
            facts["series"] = b["series"]
            if b["band_pct"] is not None:
                facts["band_pct"] = b["band_pct"]
                pc = closes.get(sym)
                if pc:
                    facts["band_upper"] = round(pc * (1 + b["band_pct"] / 100), 2)
                    facts["band_lower"] = round(pc * (1 - b["band_pct"] / 100), 2)
        if ban is not None:
            facts["fo_ban"] = sym in ban
        if facts:
            out[sym] = facts
    return out


def _last_closes(symbols: List[str]) -> Dict[str, float]:
    """Previous session's close per symbol from the scanner store's daily bars."""
    from apps.api.scanner.store import get_scanner_store

    from apps.api.desk.bars import today_ist

    df = get_scanner_store().load_bars("1d", limit=3)
    if df.empty:
        return {}
    today = today_ist().isoformat()
    out: Dict[str, float] = {}
    for sym, g in df[df["symbol"].isin(symbols)].groupby("symbol"):
        prev = g[g["ts"].str[:10] < today]
        if not prev.empty:
            out[sym] = float(prev.iloc[-1]["close"])
    return out
