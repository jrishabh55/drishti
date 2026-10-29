"""Insider / promoter trades and bulk-block deals (the trader-pelosi equivalent
for NSE: who inside the company, or which big client, traded the stock).

Sources: SEBI PIT disclosures as NSE publishes them (promoters, directors,
KMP, designated employees and relatives must report trades over the
threshold within two trading days), and NSE's bulk/block deal reports.
Every row links its filing; nothing here is a prediction.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from apps.api.desk import datasets
from apps.api.desk.datasets import FeedUnavailable, clean_text, safe_link, status_of
from apps.api.desk.market.session import NSE

IST = NSE.tz
MAX_DAYS = 365
#: NSE writes "Promoters", "Promoter Group", "Promoter & Director"…: all start so.
PROMOTER_PREFIX = "promoter"


def _d(v: Any, fmt: str = "%d-%b-%Y") -> Optional[date]:
    try:
        return datetime.strptime(str(v or "").strip()[:11], fmt).date()
    except ValueError:
        return None


def _num(v: Any) -> Optional[float]:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_pit(text: str) -> List[Dict[str, Any]]:
    """corporates-pit {"data": [...]} -> one row per disclosed transaction."""
    out = []
    for r in (json.loads(text) or {}).get("data") or []:
        traded_from, traded_to = _d(r.get("acqfromDt")), _d(r.get("acqtoDt"))
        disclosed = _d(r.get("date"))
        category = clean_text(r.get("personCategory"), 60)
        qty, value = _num(r.get("secAcq")), _num(r.get("secVal"))
        out.append({
            "symbol": str(r.get("symbol") or "").upper(),
            "person": clean_text(r.get("acqName"), 120),
            "category": category,
            "promoter": category.lower().startswith(PROMOTER_PREFIX),
            "transaction": clean_text(r.get("tdpTransactionType"), 40),
            "mode": clean_text(r.get("acqMode"), 60),
            "security": clean_text(r.get("secType"), 60),
            "qty": int(qty) if qty is not None else None,
            "value_inr": value,
            "avg_price": round(value / qty, 2) if value and qty else None,
            "holding_before_pct": _num(r.get("befAcqSharesPer")),
            "holding_after_pct": _num(r.get("afterAcqSharesPer")),
            "traded_from": traded_from.isoformat() if traded_from else None,
            "traded_to": traded_to.isoformat() if traded_to else None,
            "disclosed_on": disclosed.isoformat() if disclosed else None,
            "disclosure_lag_days": (disclosed - traded_to).days if disclosed and traded_to else None,
            "regulation": clean_text(r.get("anex"), 20),
            "filing_url": safe_link(r.get("xbrl"), "nse"),
        })
    return out


def parse_deals_history(text: str, kind: str) -> List[Dict[str, Any]]:
    """historicalOR/bulk-block-short-deals {"data": [{BD_DT_DATE, BD_SYMBOL, …}]}."""
    out = []
    for r in (json.loads(text) or {}).get("data") or []:
        qty, px = _num(r.get("BD_QTY_TRD")), _num(r.get("BD_TP_WATP"))
        day = _d(r.get("BD_DT_DATE"), "%d-%b-%Y")
        if qty is None or px is None or day is None:
            continue
        out.append({"kind": kind, "date": day.isoformat(),
                    "symbol": str(r.get("BD_SYMBOL") or "").upper(),
                    "client": clean_text(r.get("BD_CLIENT_NAME"), 120),
                    "side": clean_text(r.get("BD_BUY_SELL"), 8).upper(),
                    "qty": int(qty), "price": px, "value_cr": round(qty * px / 1e7, 2)})
    return out


def _window(days: int, now: Optional[datetime]) -> tuple:
    if not 1 <= days <= MAX_DAYS:
        raise FeedUnavailable("bad_params", f"days must be 1–{MAX_DAYS}")
    today = (now or datetime.now(timezone.utc)).astimezone(IST).date()
    return today - timedelta(days=days - 1), today


def symbol_insiders(symbol: str, *, days: int = 90, now: Optional[datetime] = None
                    ) -> Dict[str, Any]:
    symbol = datasets._sym(symbol)
    lo, hi = _window(days, now)
    span = {"from": lo.isoformat(), "to": hi.isoformat()}
    sources: List[Dict[str, Any]] = []
    pit: List[Dict[str, Any]] = []
    try:
        got = datasets.fetch("nse.insider_pit", {"symbol": symbol, **span}, now=now)
        # the API filters on the disclosure date; keep the window honest on our side too
        pit = [r for r in parse_pit(got["text"])
               if r["symbol"] == symbol and r["disclosed_on"]
               and span["from"] <= r["disclosed_on"] <= span["to"]]
        sources.append(status_of("nse.insider_pit", got, count=len(pit)))
    except (FeedUnavailable, ValueError, AttributeError) as exc:
        sources.append(status_of("nse.insider_pit", error=str(exc)))
    deals: List[Dict[str, Any]] = []
    for kind in ("bulk_deals", "block_deals"):
        try:
            got = datasets.fetch("nse.deals_history",
                                 {"kind": kind, "symbol": symbol, **span}, now=now)
            rows = [r for r in parse_deals_history(got["text"], kind.split("_")[0])
                    if r["symbol"] == symbol and span["from"] <= r["date"] <= span["to"]]
            deals += rows
            sources.append(dict(status_of("nse.deals_history", got, count=len(rows)),
                                label=f"NSE {kind.split('_')[0]} deals"))
        except (FeedUnavailable, ValueError, AttributeError) as exc:
            sources.append(dict(status_of("nse.deals_history", error=str(exc)),
                                label=f"NSE {kind.split('_')[0]} deals"))
    pit.sort(key=lambda r: (r["disclosed_on"] or "", r["traded_to"] or ""), reverse=True)
    deals.sort(key=lambda r: r["date"], reverse=True)
    bad = sum(s["status"] == "unavailable" for s in sources)
    return {"symbol": symbol, "window": span, "pit": pit, "deals": deals,
            "summary": summarize(pit), "sources": sources,
            "status": "unavailable" if bad == len(sources) else "partial" if bad else "ok"}


def summarize(pit: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Market buys/sells by insiders and promoters; pledges, gifts and
    off-market transfers are listed but not netted as buying or selling."""
    def net(rows):
        buy = sum(r["value_inr"] or 0 for r in rows if r["transaction"].lower() == "buy")
        sell = sum(r["value_inr"] or 0 for r in rows if r["transaction"].lower() == "sell")
        return {"buy_inr": buy, "sell_inr": sell, "net_inr": buy - sell}

    market = [r for r in pit if r["mode"].lower().startswith("market")]
    return {"market_trades": net(market),
            "promoter_market_trades": net([r for r in market if r["promoter"]]),
            "pledge_events": sum("pledge" in r["transaction"].lower() for r in pit),
            "disclosures": len(pit)}


def latest_deals(watch: List[str], *, only_watchlist: bool, now: Optional[datetime] = None
                 ) -> Dict[str, Any]:
    """The latest session's market-wide bulk + block deals (NSE archives CSV)."""
    sources: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []
    watched = set(watch)
    for name, kind in (("nse.bulk_deals", "bulk"), ("nse.block_deals", "block")):
        try:
            got = datasets.fetch(name, now=now)
            found = [dict(r, kind=kind, on_watchlist=r["symbol"] in watched,
                          client=clean_text(r["client"], 120), name=clean_text(r["name"], 120))
                     for r in datasets.parse_deals(got["text"])]
            if only_watchlist:
                found = [r for r in found if r["on_watchlist"]]
            rows += found
            sources.append(status_of(name, got, count=len(found)))
        except (FeedUnavailable, ValueError) as exc:
            sources.append(status_of(name, error=str(exc)))
    for r in rows:   # CSV dates are "28-OCT-2026"
        d = _d(str(r.get("date") or "").title())
        r["date"] = d.isoformat() if d else None
    rows.sort(key=lambda r: r["value_cr"], reverse=True)
    bad = sum(s["status"] == "unavailable" for s in sources)
    return {"deals": rows, "sources": sources, "only_watchlist": only_watchlist,
            "status": "unavailable" if bad == len(sources) else "partial" if bad else "ok"}
