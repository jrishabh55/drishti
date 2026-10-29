"""NSE/BSE market facts for the risk check (circuit bands, ASM/GSM, F&O ban,
results dates) — the seam the Phase 4 dataset feeds fill.

Until a dataset has a verified route (see scripts/desk_spike.py and the
spec's §3), ``market_facts`` returns {} for it and the risk check reports
those checks as "skipped: data unavailable" instead of silently passing.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List

FactsProvider = Callable[[List[str]], Dict[str, Dict[str, Any]]]
_providers: List[FactsProvider] = []


def register_facts_provider(fn: FactsProvider) -> None:
    if fn not in _providers:
        _providers.append(fn)


def clear_facts_providers_for_tests() -> None:
    _providers.clear()


def market_facts(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for fn in list(_providers):
        try:
            for sym, facts in (fn(symbols) or {}).items():
                out.setdefault(sym, {}).update(facts)
        except Exception:  # noqa: BLE001 — a broken feed degrades to "skipped"
            continue
    return out
