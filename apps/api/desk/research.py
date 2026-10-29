"""Symbol research: the exchange facts a trader checks before news/results/
insiders — circuit band, F&O membership and ban, ASM/GSM surveillance — each
with the source that said so and how fresh it is. A source that can't be
read is reported as unavailable, never as "not on the list"."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from apps.api.desk import datasets
from apps.api.desk.datasets import FeedUnavailable, status_of


def symbol_facts(symbol: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    symbol = datasets._sym(symbol)
    facts: Dict[str, Any] = {}
    sources: List[Dict[str, Any]] = []

    def read(name: str, parse: Callable[[str], Any], apply: Callable[[Any], None]) -> None:
        try:
            got = datasets.fetch(name, now=now)
            parsed = parse(got["text"])
        except (FeedUnavailable, ValueError, AttributeError) as exc:
            sources.append(status_of(name, error=str(exc)))
            return
        apply(parsed)
        sources.append(status_of(name, got, count=len(parsed)))

    def band(b: Dict[str, Any]) -> None:
        row = b.get(symbol)
        facts["band_pct"] = row["band_pct"] if row else None
        facts["series"] = row["series"] if row else None
        facts["listed_in_band_file"] = bool(row)

    def lots(m: Dict[str, int]) -> None:
        facts["fno"] = symbol in m
        facts["fno_lot"] = m.get(symbol)

    read("nse.price_bands", datasets.parse_price_bands, band)
    read("nse.fo_lots", datasets.parse_fo_lots, lots)
    read("nse.fo_ban", datasets.parse_fo_ban, lambda ban: facts.__setitem__("fo_ban", symbol in ban))
    read("nse.asm", lambda t: datasets.parse_surveillance(t, None),
         lambda s: facts.__setitem__("asm", s.get(symbol, {}).get("asm")))
    read("nse.gsm", lambda t: datasets.parse_surveillance(None, t),
         lambda s: facts.__setitem__("gsm", s.get(symbol, {}).get("gsm")))
    bad = sum(s["status"] == "unavailable" for s in sources)
    return {"symbol": symbol, "facts": facts, "sources": sources,
            "status": "unavailable" if bad == len(sources) else "partial" if bad else "ok"}

