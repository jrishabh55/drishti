"""Quarterly results from the exchange filings (the trader-analyst equivalent).

Since 2025 Indian listed companies file quarterly results as SEBI
"Integrated Filing - Financials"; NSE lists them per symbol with the XBRL
instance the company filed. Each instance carries one quarter (context
``OneD``), so QoQ and YoY need the earlier quarters' filings too. Figures
are what the company filed; nothing is estimated. When NSE cannot be read,
Yahoo's quarterly statements are offered instead and labelled unofficial.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from apps.api.desk import datasets
from apps.api.desk.datasets import FeedUnavailable, clean_text, safe_link, status_of
from apps.api.desk.market.session import NSE

IST = NSE.tz
QUARTERS = 5   # the latest + four back: enough for QoQ and YoY on the latest

# metric -> filed XBRL elements, first present wins (Ind-AS companies, then banks)
METRICS: Dict[str, tuple] = {
    "revenue": ("RevenueFromOperations", "InterestEarned"),
    "total_income": ("Income",),
    "expenses": ("Expenses", "ExpenditureExcludingProvisionsAndContingencies"),
    "pbt": ("ProfitBeforeTax", "ProfitLossFromOrdinaryActivitiesBeforeTax"),
    "pat": ("ProfitLossForPeriod", "ProfitLossForThePeriod"),
    "pat_owners": ("ProfitOrLossAttributableToOwnersOfParent",
                   "ProfitLossAfterTaxesMinorityInterestAndShareOfProfitLossOfAssociates"),
    "eps_diluted": ("DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
                    "DilutedEarningsLossPerShareFromContinuingOperations",
                    "DilutedEarningsPerShareAfterExtraordinaryItems"),
    "gross_npa_pct": ("PercentageOfGrossNpa",),
}
AMOUNTS = ("revenue", "total_income", "expenses", "pbt", "pat", "pat_owners")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_xbrl(text: str) -> Dict[str, Any]:
    """One integrated-filing instance -> the quarter it reports and its figures.
    Amounts in ₹ crore; EPS in ₹; ratios as filed."""
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise ValueError("refusing an XBRL document with a DTD")
    root = ET.fromstring(text)
    periods: Dict[str, tuple] = {}
    for ctx in root.iter("{http://www.xbrl.org/2003/instance}context"):
        if ctx.find(".//{http://www.xbrl.org/2003/instance}scenario") is not None:
            continue
        s = ctx.findtext(".//{http://www.xbrl.org/2003/instance}startDate")
        e = ctx.findtext(".//{http://www.xbrl.org/2003/instance}endDate")
        if s and e:
            periods[ctx.get("id", "")] = (s.strip(), e.strip())
    facts: Dict[str, Dict[str, str]] = {}
    for el in root:
        ref = el.get("contextRef")
        if ref and el.text is not None:
            facts.setdefault(ref, {})[_local(el.tag)] = el.text.strip()
    one = facts.get("OneD") or {}
    start, end = one.get("DateOfStartOfReportingPeriod"), one.get("DateOfEndOfReportingPeriod")
    if not end or periods.get("OneD") != (start, end):
        raise ValueError("filing has no current-quarter context")
    if one.get("TypeOfReportingPeriod", "Quarterly").lower() != "quarterly":
        raise ValueError(f"not a quarterly filing: {one.get('TypeOfReportingPeriod')}")
    out: Dict[str, Any] = {}
    for key, tags in METRICS.items():
        for t in tags:
            v = one.get(t)
            if v not in (None, ""):
                try:
                    num = float(v)
                except ValueError:
                    continue
                out[key] = round(num / 1e7, 2) if key in AMOUNTS else num
                break
    return {"period_start": start, "period_end": end,
            "quarter": clean_text(one.get("ReportingQuarter"), 30),
            "basis": clean_text(one.get("NatureOfReportStandaloneConsolidated"), 20).lower(),
            "audited": clean_text(one.get("WhetherResultsAreAuditedOrUnaudited"), 20),
            "board_meeting": one.get("DateOfEndOfBoardMeeting"),
            "bank": "InterestEarned" in one, "figures": out}


def parse_filing_list(text: str) -> List[Dict[str, Any]]:
    """integrated-filing-results {"data": [...]} -> the newest filing per
    (quarter end, basis); a revision replaces the original."""
    best: Dict[tuple, Dict[str, Any]] = {}
    for r in (json.loads(text) or {}).get("data") or []:
        if "financial" not in str(r.get("type") or "").lower():
            continue
        try:
            qe = datetime.strptime(str(r.get("qe_Date")).title(), "%d-%b-%Y").date()
            sent = datetime.strptime(str(r.get("broadcast_Date")), "%d-%b-%Y %H:%M:%S")
        except ValueError:
            continue
        xbrl = safe_link(r.get("xbrl"), "nse")
        if not xbrl or "/corporate/xbrl/" not in xbrl:
            continue
        basis = str(r.get("consolidated") or "").lower()
        row = {"quarter_end": qe.isoformat(), "basis": basis, "filed_at": sent.isoformat(),
               "audited": clean_text(r.get("audited"), 20), "xbrl": xbrl,
               "xbrl_file": xbrl.rsplit("/", 1)[1], "revision": clean_text(r.get("type_Sub"), 20),
               "filing_url": safe_link(r.get("ixbrl"), "nse")}
        k = (row["quarter_end"], basis)
        if k not in best or row["filed_at"] > best[k]["filed_at"]:
            best[k] = row
    return sorted(best.values(), key=lambda r: r["quarter_end"], reverse=True)


def _qindex(d: str) -> int:
    x = date.fromisoformat(d)
    return x.year * 4 + (x.month - 1) // 3


def _pct(now_v: Optional[float], then_v: Optional[float]) -> Optional[float]:
    if now_v is None or then_v in (None, 0):
        return None
    return round((now_v - then_v) / abs(then_v) * 100, 1)


def compare(quarters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Adds margins and QoQ / YoY (vs the quarter before and the same quarter
    a year earlier, same basis only) to each quarter, newest first."""
    by_q = {_qindex(q["period_end"]): q for q in quarters}
    out = []
    for q in sorted(quarters, key=lambda q: q["period_end"], reverse=True):
        f = q["figures"]
        rev = f.get("revenue")
        margins = {}
        if rev:
            for k in ("pbt", "pat"):
                if f.get(k) is not None:
                    margins[f"{k}_margin_pct"] = round(f[k] / rev * 100, 1)
        row = dict(q, margins=margins, qoq={}, yoy={})
        for label, back in (("qoq", 1), ("yoy", 4)):
            prev = by_q.get(_qindex(q["period_end"]) - back)
            if not prev or prev.get("basis") != q.get("basis"):
                continue
            row[f"{label}_period_end"] = prev["period_end"]
            for k, v in f.items():
                ch = _pct(v, prev["figures"].get(k))
                if ch is not None and k != "gross_npa_pct":
                    row[label][k] = ch
        out.append(row)
    return out


def _next_results(symbol: str, now: Optional[datetime], sources: List[Dict[str, Any]]
                  ) -> Optional[str]:
    try:
        got = datasets.fetch("nse.event_calendar_symbol", {"symbol": symbol}, now=now)
        dates = datasets.parse_results_calendar(got["text"]).get(symbol, [])
    except (FeedUnavailable, ValueError, AttributeError) as exc:
        sources.append(status_of("nse.event_calendar_symbol", error=str(exc)))
        return None
    today = (now or datetime.now(timezone.utc)).astimezone(IST).date().isoformat()
    upcoming = [d for d in dates if d >= today]
    sources.append(status_of("nse.event_calendar_symbol", got, count=len(upcoming)))
    return upcoming[0] if upcoming else None


def symbol_results(symbol: str, *, now: Optional[datetime] = None,
                   yahoo: Optional[Callable[[str], List[Dict[str, Any]]]] = None
                   ) -> Dict[str, Any]:
    symbol = datasets._sym(symbol)
    sources: List[Dict[str, Any]] = []
    notes: List[str] = []
    quarters: List[Dict[str, Any]] = []
    source = "nse"
    try:
        got = datasets.fetch("nse.integrated_results", {"symbol": symbol}, now=now)
        filings = parse_filing_list(got["text"])
        sources.append(status_of("nse.integrated_results", got, count=len(filings)))
    except (FeedUnavailable, ValueError, AttributeError) as exc:
        filings = []
        sources.append(status_of("nse.integrated_results", error=str(exc)))
    if filings:
        # one basis for the whole table: consolidated when the latest quarter has it
        latest_q = filings[0]["quarter_end"]
        bases = {f["basis"] for f in filings if f["quarter_end"] == latest_q}
        basis = "consolidated" if "consolidated" in bases else next(iter(bases))
        picked = [f for f in filings if f["basis"] == basis][:QUARTERS]
        failed, last = 0, {}
        for f in picked:
            try:
                last = datasets.fetch("nse.xbrl", {"file": f["xbrl_file"]}, now=now)
                q = parse_xbrl(last["text"])
            except (FeedUnavailable, ValueError, ET.ParseError) as exc:
                failed += 1
                notes.append(f"{f['quarter_end']} filing could not be read: {clean_text(exc, 200)}")
                continue
            if q["period_end"] != f["quarter_end"]:
                failed += 1
                notes.append(f"{f['quarter_end']} filing reports {q['period_end']}; skipped")
                continue
            quarters.append(dict(q, filed_at=f["filed_at"], filing_url=f["filing_url"] or f["xbrl"],
                                 xbrl_url=f["xbrl"], revision=f["revision"]))
        sources.append(status_of("nse.xbrl", last, count=len(quarters)) if quarters else
                       status_of("nse.xbrl", error=f"{failed} filing(s) could not be read"))
    if not quarters and yahoo is not None:
        try:
            quarters = yahoo(symbol)
            source = "yahoo"
            sources.append(status_of("yahoo.financials", {}, count=len(quarters)))
            if quarters:
                notes.append("NSE filings could not be read, so these figures are Yahoo "
                             "Finance's quarterly statements: unofficial, and they can lag or "
                             "differ from what the company filed.")
        except Exception as exc:  # noqa: BLE001 — any Yahoo failure is just "unavailable"
            sources.append(status_of("yahoo.financials", error=f"{type(exc).__name__}: {exc}"))
    next_date = _next_results(symbol, now, sources)
    rows = compare(quarters)
    if rows and not rows[0]["yoy"]:
        notes.append("No same-basis filing a year earlier, so there is no YoY comparison.")
    return {"symbol": symbol, "source": source, "quarters": rows,
            "next_results_date": next_date, "notes": notes, "sources": sources,
            "status": "ok" if rows else "unavailable"}


def yahoo_quarters(symbol: str) -> List[Dict[str, Any]]:  # pragma: no cover - network
    """Fallback: Yahoo's quarterly income statement, in the same shape."""
    import yfinance as yf

    df = yf.Ticker(f"{symbol}.NS").quarterly_income_stmt
    if df is None or df.empty:
        return []
    rows = {"revenue": "Total Revenue", "pbt": "Pretax Income", "pat": "Net Income",
            "eps_diluted": "Diluted EPS"}
    out = []
    for col in list(df.columns)[:QUARTERS]:
        figs = {}
        for k, label in rows.items():
            if label in df.index:
                v = df.at[label, col]
                if v == v and v is not None:   # NaN-safe
                    figs[k] = round(float(v) / 1e7, 2) if k != "eps_diluted" else float(v)
        end = col.date()
        out.append({"period_start": None, "period_end": end.isoformat(), "quarter": "",
                    "basis": "yahoo", "audited": "", "board_meeting": None, "bank": False,
                    "figures": figs, "filed_at": None, "filing_url": None, "xbrl_url": None,
                    "revision": ""})
    return out
