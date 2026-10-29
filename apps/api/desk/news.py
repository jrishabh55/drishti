"""Per-symbol / watchlist news for NSE stocks (the trader-news equivalent).

Official sources first: NSE and BSE corporate announcements (what the company
told the exchange), then Yahoo Finance headlines (unofficial, sparse for .NS).
The same filing usually lands on both exchanges within minutes; those are
merged into one item carrying both links. Times are IST; the window is whole
IST calendar days, today included.
"""
from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple

from apps.api.desk import datasets
from apps.api.desk.datasets import FeedUnavailable, clean_text, safe_link, status_of
from apps.api.desk.market.session import NSE

IST = NSE.tz
MAX_DAYS = 30
MERGE_WINDOW = timedelta(minutes=15)

# (tag, words): first matching words in category + title decide the tags.
_TAGS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("results", ("financial result", "results", "limited review", "integrated filing- financial")),
    ("dividend", ("dividend",)),
    ("board_meeting", ("board meeting", "outcome of board")),
    ("insider", ("insider trading", "sebi (pit)", "sast", "takeover", "pledge")),
    ("corporate_action", ("bonus", "split", "buyback", "buy back", "rights issue", "record date")),
    ("deal", ("acquisition", "amalgamation", "merger", "demerger", "restructuring",
              "allotment", "fund raising", "issue of securities")),
    ("rating", ("credit rating",)),
    ("people", ("appointment", "cessation", "resignation", "director", "key managerial",
                "change in management", "demise")),
    ("legal", ("litigation", "order", "penalty", "dispute", "show cause", "tax demand")),
    ("investor_meet", ("analyst", "investor meet", "con. call", "investor presentation",
                       "earnings call")),
)


def tags_for(*texts: str) -> List[str]:
    low = " ".join(t.lower() for t in texts if t)
    return [tag for tag, words in _TAGS if any(w in low for w in words)]


def window(days: int, now: Optional[datetime] = None) -> Tuple[date, date]:
    """Whole IST days: ``days=1`` is today only."""
    if not 1 <= days <= MAX_DAYS:
        raise FeedUnavailable("bad_params", f"days must be 1–{MAX_DAYS}")
    today = (now or datetime.now(timezone.utc)).astimezone(IST).date()
    return today - timedelta(days=days - 1), today


def _ist(dt: datetime) -> datetime:
    return (dt if dt.tzinfo else dt.replace(tzinfo=IST)).astimezone(IST)


def _item(source: str, title: str, published: datetime, url: Optional[str], *,
          category: str = "", detail: str = "", official: bool) -> Dict[str, Any]:
    title = clean_text(title, 300)
    detail = clean_text(detail, 600)
    return {"title": title, "detail": detail if detail != title else "",
            "category": clean_text(category, 80), "published_at": _ist(published).isoformat(),
            "official": official, "tags": tags_for(category, title, detail),
            "sources": [{"source": source, "url": url}]}


# ---------------------------------------------------------------- parsers

def parse_nse_announcements(text: str) -> List[Dict[str, Any]]:
    out = []
    for r in json.loads(text) or []:
        try:
            when = datetime.strptime(str(r.get("an_dt") or r.get("sort_date")), "%d-%b-%Y %H:%M:%S")
        except ValueError:
            try:
                when = datetime.strptime(str(r.get("sort_date")), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
        body = str(r.get("attchmntText") or "")
        out.append(_item("nse", body or str(r.get("desc") or ""), when,
                         safe_link(r.get("attchmntFile"), "nse"),
                         category=str(r.get("desc") or ""), official=True))
    return out


def parse_bse_announcements(text: str) -> List[Dict[str, Any]]:
    out = []
    for r in (json.loads(text) or {}).get("Table") or []:
        try:
            when = datetime.fromisoformat(str(r.get("NEWS_DT") or r.get("DT_TM"))[:19])
        except ValueError:
            continue
        att = str(r.get("ATTACHMENTNAME") or "").strip()
        url = (safe_link(f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{att}", "bse")
               if re.fullmatch(r"[A-Za-z0-9._-]{1,120}", att) else None)
        head, sub = str(r.get("HEADLINE") or ""), str(r.get("NEWSSUB") or "")
        # some headlines are just "Enclosed": the subject line says more then
        out.append(_item("bse", head if len(head.split()) >= 4 else sub or head, when, url,
                         category=str(r.get("SUBCATNAME") or r.get("CATEGORYNAME") or ""),
                         detail=sub, official=True))
    return out


def parse_yahoo_rss(text: str) -> List[Dict[str, Any]]:
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise ValueError("refusing an RSS document with a DTD")
    out = []
    for it in ET.fromstring(text).iter("item"):
        try:
            when = parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            continue
        out.append(_item("yahoo", html.unescape(it.findtext("title") or ""), when,
                         safe_link(it.findtext("link"), "yahoo"),
                         detail=html.unescape(it.findtext("description") or ""), official=False))
    return out


def parse_bse_scrip_search(text: str, symbol: str) -> Optional[str]:
    """PeerSmartSearch answers a JSON string of <li> HTML; each holds
    ``SYMBOL&nbsp;…ISIN&nbsp;…CODE``. Returns the code whose symbol is exactly ours."""
    snippet = json.loads(text)
    if not isinstance(snippet, str):
        return None
    for li in re.findall(r"<li.*?</li>", snippet, re.S):
        span = re.search(r"<span>(.*?)</span>", li, re.S)
        if not span:
            continue
        parts = [p for p in html.unescape(re.sub(r"<[^>]+>", "", span.group(1))
                                          ).replace("\xa0", " ").split() if p]
        if len(parts) >= 3 and parts[0].upper() == symbol and re.fullmatch(r"\d{6}", parts[-1]):
            return parts[-1]
    return None


# ---------------------------------------------------------------- dedup

def _words(s: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2}


def _same_story(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    ta = datetime.fromisoformat(a["published_at"])
    tb = datetime.fromisoformat(b["published_at"])
    if abs(ta - tb) > MERGE_WINDOW:
        return False
    if {s["source"] for s in a["sources"]} & {s["source"] for s in b["sources"]}:
        return False     # two filings on one exchange are two filings
    wa, wb = _words(a["title"] + " " + a["detail"]), _words(b["title"] + " " + b["detail"])
    small = min(wa, wb, key=len)
    if a["official"] != b["official"]:   # a headline only merges with a filing on the same words
        return a["title"].lower() == b["title"].lower()
    return len(small) >= 3 and len(wa & wb) / len(small) >= 0.8


def dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in sorted(items, key=lambda x: x["published_at"]):
        twin = next((o for o in out if o.get("symbol") == it.get("symbol")
                     and _same_story(o, it)), None)
        if twin is None:
            out.append(dict(it, sources=list(it["sources"])))
            continue
        have = {s["source"] for s in twin["sources"]}
        twin["sources"] += [s for s in it["sources"] if s["source"] not in have]
        twin["tags"] = sorted(set(twin["tags"]) | set(it["tags"]))
        if len(it["detail"]) > len(twin["detail"]):
            twin["detail"] = it["detail"]
    out.sort(key=lambda x: x["published_at"], reverse=True)
    return out


# ---------------------------------------------------------------- per symbol

def _in_window(items: List[Dict[str, Any]], lo: date, hi: date) -> List[Dict[str, Any]]:
    return [i for i in items if lo <= datetime.fromisoformat(i["published_at"]).date() <= hi]


def symbol_news(symbol: str, *, days: int = 7, now: Optional[datetime] = None
                ) -> Dict[str, Any]:
    symbol = datasets._sym(symbol)
    lo, hi = window(days, now)
    span = {"from": lo.isoformat(), "to": hi.isoformat()}
    items: List[Dict[str, Any]] = []
    sources: List[Dict[str, Any]] = []

    def pull(name: str, params: Dict[str, Any], parse) -> None:
        try:
            got = datasets.fetch(name, params, now=now)
            found = _in_window(parse(got["text"]), lo, hi)
        except (FeedUnavailable, ValueError, ET.ParseError, AttributeError) as exc:
            sources.append(status_of(name, error=str(exc)))
            return
        items.extend(found)
        sources.append(status_of(name, got, count=len(found)))

    pull("nse.announcements", {"symbol": symbol, **span}, parse_nse_announcements)
    try:
        code = parse_bse_scrip_search(
            datasets.fetch("bse.scrip_search", {"symbol": symbol}, now=now)["text"], symbol)
    except (FeedUnavailable, ValueError) as exc:
        sources.append(status_of("bse.announcements", error=f"BSE scrip lookup failed: {exc}"))
    else:
        if code:
            pull("bse.announcements", {"code": code, **span}, parse_bse_announcements)
        else:
            sources.append(status_of("bse.announcements",
                                     error=f"{symbol} has no BSE listing with that symbol"))
    pull("yahoo.rss", {"symbol": symbol}, parse_yahoo_rss)
    for it in items:
        it["symbol"] = symbol
    merged = dedupe(items)
    return {"symbol": symbol, "window": span, "items": merged, "sources": sources,
            "status": _overall(sources)}


def _overall(sources: List[Dict[str, Any]]) -> str:
    bad = sum(1 for s in sources if s["status"] == "unavailable")
    return "unavailable" if bad == len(sources) else "partial" if bad else "ok"


WATCHLIST_CAP = 10
#: Symbols fetched at once. Each host is still paced to 1 req/s by the shared
#: throttle; this only stops one slow host from serialising the others.
WATCHLIST_WORKERS = 4
#: The whole watchlist answers within this; symbols still fetching are
#: reported unavailable rather than holding the page (their fetches finish in
#: the background and warm the cache for the next look).
WATCHLIST_DEADLINE_S = 20.0


def watchlist_news(symbols: List[str], *, days: int = 3, now: Optional[datetime] = None,
                   deadline_s: float = WATCHLIST_DEADLINE_S) -> Dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor, wait

    window(days, now)    # bad params fail before any fetch
    picked = symbols[:WATCHLIST_CAP]
    pool = ThreadPoolExecutor(max_workers=WATCHLIST_WORKERS, thread_name_prefix="desk-news")
    try:
        futs = {s: pool.submit(symbol_news, s, days=days, now=now) for s in picked}
        wait(list(futs.values()), timeout=deadline_s)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    per = []
    for s, f in futs.items():
        if f.done() and not f.cancelled() and f.exception() is None:
            per.append(f.result())
            continue
        err = (f"{type(f.exception()).__name__}: {f.exception()}" if f.done() and not f.cancelled()
               and f.exception() is not None else f"no answer within {deadline_s:.0f}s")
        per.append({"symbol": s, "window": None, "items": [],
                    "sources": [status_of("nse.announcements", error=err)]})
    items = sorted((i for p in per for i in p["items"]), key=lambda x: x["published_at"],
                   reverse=True)
    sources = [dict(s, symbol=p["symbol"]) for p in per for s in p["sources"]]
    notes = []
    if len(symbols) > WATCHLIST_CAP:
        notes.append(f"Showing news for the first {WATCHLIST_CAP} of {len(symbols)} "
                     "watchlist symbols.")
    status = "empty" if not picked else _overall(sources) if sources else "ok"
    span = next((p["window"] for p in per if p["window"]), None)
    return {"symbols": picked, "window": span, "items": items,
            "sources": sources, "status": status, "notes": notes}
