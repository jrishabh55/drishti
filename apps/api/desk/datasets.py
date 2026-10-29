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

Endpoints and response shapes were observed on 2026-10-29 and recorded in
tests/fixtures/desk/sources/ (see its README for what answered and what did
not). Parsers match header/field names, not positions, and are tested
against those recorded responses. Re-run the spike from the server before
enabling DESK_NSE_FEEDS there.
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
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Literal, Optional

logger = logging.getLogger(__name__)

Route = Literal["server", "helper"]
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
NEGATIVE_TTL_S = 600
TIMEOUT_S = 6
MAX_BYTES = 3_000_000


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
    kind: Literal["csv", "json", "xml"]
    ttl_s: int
    params: Dict[str, Callable[[Any], str]] = field(default_factory=dict)
    route: Route = "server"
    warmup: Optional[str] = None              # homepage to visit first for cookies
    headers: Dict[str, str] = field(default_factory=dict)
    used_by: str = ""


def _yyyymmdd(v: Any) -> str:
    _ddmmyyyy(v)
    return date.fromisoformat(str(v)).strftime("%Y%m%d")


def _choice(*allowed: str) -> Callable[[Any], str]:
    def check(v: Any) -> str:
        if v not in allowed:
            raise FeedUnavailable("bad_params", f"{v!r} is not one of {allowed}")
        return str(v)
    return check


def _pattern(rx: str, what: str) -> Callable[[Any], str]:
    compiled = re.compile(rx)

    def check(v: Any) -> str:
        if not compiled.fullmatch(str(v or "")):
            raise FeedUnavailable("bad_params", f"{v!r} is not {what}")
        return str(v)
    return check


def _query(v: Any) -> str:
    from urllib.parse import quote
    return quote(_sym(v), safe="")


_xbrl_file = _pattern(r"[A-Z0-9_]{8,120}_WEB\.xml", "an NSE XBRL filing name")
_bse_code = _pattern(r"[0-9]{6}", "a BSE scrip code")

NSE_HOME = "https://www.nseindia.com/"
NSE_API = "https://www.nseindia.com/api/"
BSE_API = "https://api.bseindia.com/BseIndiaAPI/api/"
BSE_HEADERS = {"Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com"}
# Yahoo's RSS answers 429 to a full browser UA string and 200 to a short one
# (observed 2026-10-29).
YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0"}

# Observed 2026-10-29 from a residential Indian IP (scripts/desk_spike.py and
# tests/fixtures/desk/sources/README.md). NOT yet observed from the deployed
# server: DESK_NSE_FEEDS stays off for the risk check until the spike passes there.
CATALOG: Dict[str, Dataset] = {d.name: d for d in (
    Dataset("nse.fo_ban", "https://nsearchives.nseindia.com/content/fo/fo_secban.csv",
            "csv", 6 * 3600, used_by="risk (warn), research facts"),
    Dataset("nse.price_bands", "https://nsearchives.nseindia.com/content/equities/sec_list.csv",
            "csv", 6 * 3600, used_by="risk (circuit headroom), Time Machine clamp"),
    Dataset("nse.fo_lots", "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv",
            "csv", 24 * 3600, used_by="F&O universe, research facts"),
    Dataset("nse.bulk_deals", "https://nsearchives.nseindia.com/content/equities/bulk.csv",
            "csv", 3600, used_by="deals tracker (latest session)"),
    Dataset("nse.block_deals", "https://nsearchives.nseindia.com/content/equities/block.csv",
            "csv", 3600, used_by="deals tracker (latest session)"),
    Dataset("nse.deals_history",
            NSE_API + "historicalOR/bulk-block-short-deals?optionType={kind}&symbol={symbol}"
            "&from={from}&to={to}", "json", 3600,
            {"kind": _choice("bulk_deals", "block_deals"), "symbol": _query,
             "from": _ddmmyyyy, "to": _ddmmyyyy}, warmup=NSE_HOME, used_by="insider tracker"),
    Dataset("nse.announcements",
            NSE_API + "corporate-announcements?index=equities&symbol={symbol}"
            "&from_date={from}&to_date={to}", "json", 300,
            {"symbol": _query, "from": _ddmmyyyy, "to": _ddmmyyyy}, warmup=NSE_HOME,
            used_by="news"),
    # Market-wide PIT (no symbol) answered an empty list on every probe; per symbol works.
    Dataset("nse.insider_pit",
            NSE_API + "corporates-pit?index=equities&symbol={symbol}&from_date={from}"
            "&to_date={to}", "json", 1800,
            {"symbol": _query, "from": _ddmmyyyy, "to": _ddmmyyyy}, warmup=NSE_HOME,
            used_by="insider tracker"),
    # corporates-financial-results stops at Dec 2024: SEBI's integrated filing
    # (2025-) carries the quarterly results now.
    Dataset("nse.integrated_results",
            NSE_API + "integrated-filing-results?index=equities&symbol={symbol}"
            "&type=Integrated%20Filing-%20Financials", "json", 6 * 3600,
            {"symbol": _query}, warmup=NSE_HOME, used_by="results analyst"),
    Dataset("nse.xbrl", "https://nsearchives.nseindia.com/corporate/xbrl/{file}", "xml",
            30 * 24 * 3600, {"file": _xbrl_file}, used_by="results analyst (filed figures)"),
    Dataset("nse.event_calendar", NSE_API + "event-calendar?index=equities", "json",
            3 * 3600, warmup=NSE_HOME, used_by="results-week warning"),
    Dataset("nse.event_calendar_symbol",
            NSE_API + "event-calendar?index=equities&symbol={symbol}", "json", 3 * 3600,
            {"symbol": _query}, warmup=NSE_HOME, used_by="results date"),
    Dataset("nse.asm", NSE_API + "reportASM", "json", 6 * 3600,
            warmup=NSE_HOME, used_by="risk (block), research facts"),
    Dataset("nse.gsm", NSE_API + "reportGSM", "json", 6 * 3600,
            warmup=NSE_HOME, used_by="risk (block), research facts"),
    Dataset("nse.holidays", NSE_API + "holiday-master?type=trading",
            "json", 24 * 3600, warmup=NSE_HOME, used_by="session calendar"),
    Dataset("bse.scrip_search", BSE_API + "PeerSmartSearch/w?Type=SS&text={symbol}", "json",
            7 * 24 * 3600, {"symbol": _query}, headers=BSE_HEADERS,
            used_by="NSE symbol -> BSE scrip code"),
    Dataset("bse.announcements",
            BSE_API + "AnnSubCategoryGetData/w?pageno=1&strCat=-1&strPrevDate={from}"
            "&strScrip={code}&strSearch=P&strToDate={to}&strType=C&subcategory=-1", "json",
            300, {"code": _bse_code, "from": _yyyymmdd, "to": _yyyymmdd},
            headers=BSE_HEADERS, used_by="news"),
    Dataset("yahoo.rss", "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}.NS"
            "&region=IN&lang=en-IN", "xml", 600, {"symbol": _query}, headers=YAHOO_HEADERS,
            used_by="news (unofficial headlines)"),
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
    headers = dict(ds.headers)
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
    if len(r.content) > MAX_BYTES:   # a cut-off body would parse as garbage, not as less data
        raise FeedUnavailable("source_error", f"{ds.name}: response over {MAX_BYTES} bytes")
    return r.content


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


# ---------------------------------------------------------------- parsers (recorded formats)

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


def parse_fo_lots(text: str) -> Dict[str, int]:
    """fo_mktlots.csv: index rows (NIFTY, BANKNIFTY…), then a
    "Derivatives on Individual Securities,Symbol,…" divider, then one row per
    stock underlying with its lot size per expiry. Returns {SYMBOL: lot of the
    nearest expiry} for the stock underlyings only."""
    out: Dict[str, int] = {}
    stocks = False
    for row in csv.reader(io.StringIO(text)):
        cells = [c.strip() for c in row]
        if len(cells) < 3:
            continue
        if cells[0].lower().startswith("derivatives on individual securities"):
            stocks = True
            continue
        sym = cells[1].upper()
        if not stocks or not _SYMBOL.match(sym):
            continue
        lot = next((c for c in cells[2:] if c.isdigit()), None)
        if lot:
            out[sym] = int(lot)
    return out


def parse_surveillance(asm_text: Optional[str], gsm_text: Optional[str]
                       ) -> Dict[str, Dict[str, str]]:
    """reportASM {"longterm": {"data": [...]}, "shortterm": {...}} and reportGSM
    [...] -> {SYMBOL: {"asm": "LTASM - I (13)"} | {"gsm": "..."}}; the stage
    string is NSE's survCode verbatim."""
    out: Dict[str, Dict[str, str]] = {}
    if asm_text:
        doc = json.loads(asm_text)
        for term in ("longterm", "shortterm"):
            for r in (doc.get(term) or {}).get("data") or []:
                sym = str(r.get("symbol") or "").upper()
                if _SYMBOL.match(sym):
                    out.setdefault(sym, {})["asm"] = str(
                        r.get("survCode") or r.get("asmSurvIndicator") or "ASM")[:60]
    if gsm_text:
        for r in json.loads(gsm_text) or []:
            sym = str(r.get("symbol") or "").upper()
            if _SYMBOL.match(sym):
                out.setdefault(sym, {})["gsm"] = str(
                    r.get("survCode") or r.get("gsmStage") or "GSM")[:60]
    return out


def parse_results_calendar(text: str) -> Dict[str, List[str]]:
    """event-calendar [{symbol, purpose, date "30-Oct-2026"}] -> {SYMBOL:
    [ISO dates of meetings whose purpose names financial results]}, sorted."""
    out: Dict[str, List[str]] = {}
    for r in json.loads(text) or []:
        sym = str(r.get("symbol") or "").upper()
        if not _SYMBOL.match(sym) or "result" not in str(r.get("purpose") or "").lower():
            continue
        try:
            d = datetime.strptime(str(r.get("date")), "%d-%b-%Y").date().isoformat()
        except ValueError:
            continue
        out.setdefault(sym, []).append(d)
    return {k: sorted(set(v)) for k, v in out.items()}


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
    surv: Dict[str, Dict[str, str]] = {}
    try:
        surv = parse_surveillance(fetch("nse.asm")["text"], fetch("nse.gsm")["text"])
    except (FeedUnavailable, ValueError) as exc:
        logger.info("ASM/GSM lists unavailable: %s", exc)
    results: Dict[str, List[str]] = {}
    try:
        results = parse_results_calendar(fetch("nse.event_calendar")["text"])
    except (FeedUnavailable, ValueError) as exc:
        logger.info("results calendar unavailable: %s", exc)
    if not bands and ban is None and not surv and not results:
        return out
    from apps.api.desk.bars import today_ist

    today = today_ist()
    week_end = (today + timedelta(days=7)).isoformat()
    closes = _last_closes(symbols) if bands else {}
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
        facts.update(surv.get(sym, {}))
        upcoming = [d for d in results.get(sym, []) if today.isoformat() <= d <= week_end]
        if upcoming:
            facts["results_date"] = upcoming[0]
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


# ---------------------------------------------------------------- research helpers
# Vendor fields are untrusted text: they are cleaned, length-capped and shown
# as plain text, never interpreted (no markdown/HTML rendering, never fed to
# a model as instructions). Links survive only as https on the source's own hosts.

_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
LINK_HOSTS = {
    "nse": ("www.nseindia.com", "nsearchives.nseindia.com", "archives.nseindia.com"),
    "bse": ("www.bseindia.com",),
    "yahoo": ("finance.yahoo.com", "uk.finance.yahoo.com", "in.finance.yahoo.com"),
}
LABELS = {
    "nse.announcements": "NSE corporate announcements",
    "bse.announcements": "BSE corporate announcements",
    "bse.scrip_search": "BSE scrip lookup",
    "yahoo.rss": "Yahoo Finance headlines (unofficial)",
    "nse.insider_pit": "NSE insider trading (SEBI PIT) disclosures",
    "nse.deals_history": "NSE bulk/block deals",
    "nse.bulk_deals": "NSE bulk deals (latest session)",
    "nse.block_deals": "NSE block deals (latest session)",
    "nse.integrated_results": "NSE integrated filing — financials",
    "nse.xbrl": "NSE XBRL results filings",
    "nse.event_calendar_symbol": "NSE event calendar",
    "nse.price_bands": "NSE price bands",
    "nse.fo_ban": "NSE F&O ban list",
    "nse.fo_lots": "NSE F&O market lots",
    "nse.asm": "NSE ASM list",
    "nse.gsm": "NSE GSM list",
    "yahoo.financials": "Yahoo Finance quarterly statements (unofficial)",
}


def clean_text(v: Any, limit: int = 600) -> str:
    s = re.sub(r"\s+", " ", _CTRL.sub(" ", str(v if v is not None else ""))).strip()
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


def safe_link(url: Any, source: str) -> Optional[str]:
    from urllib.parse import urlsplit

    u = str(url or "").strip()
    try:
        parts = urlsplit(u)
    except ValueError:
        return None
    if parts.scheme != "https" or parts.hostname not in LINK_HOSTS.get(source, ()):
        return None
    return u


def status_of(name: str, got: Optional[Dict[str, Any]] = None, *, error: Optional[str] = None,
              count: Optional[int] = None) -> Dict[str, Any]:
    """One source line for the UI: ok | empty | unavailable, plus freshness."""
    st: Dict[str, Any] = {"source": name, "label": LABELS.get(name, name)}
    if error is not None:
        st.update(status="unavailable", error=clean_text(error, 300))
    else:
        got = got or {}
        st.update(status="ok" if count else "empty", fetched_at=got.get("fetched_at"),
                  cached=bool(got.get("cached")), stale=bool(got.get("stale")))
        if count is not None:
            st["count"] = count
    return st
