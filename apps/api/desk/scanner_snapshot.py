"""Read-only Trade Desk adapters over the scanner's published public panels."""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from apps.api.scanner.engine import get_engine

_FIELDS = ('open', 'high', 'low', 'close', 'volume')
_COLUMNS = ['symbol', 'ts', *_FIELDS]


def public_daily_bars(symbols: Iterable[str]) -> pd.DataFrame:
    """Newest 21 daily bars per selected symbol, ascending in exchange time.

    Resolve the shared public snapshot once so bar-version invalidation stays
    owned by ScanEngine. No selected-owner overlays or response caching apply.
    Find each symbol's own rows before taking its tail: a stale or sparse chart
    may have no bars on the latest 21 dates of the combined panel.
    """
    selected = set(symbols)
    if not selected:
        return pd.DataFrame(columns=_COLUMNS)
    panel = get_engine()._resolve_panels({'1d'})['1d']
    chunks = []
    for symbol in sorted(selected.intersection(panel.close.columns)):
        values = {field: getattr(panel, field)[symbol].to_numpy() for field in _FIELDS}
        # A null close/volume must still occupy its session when other OHLCV
        # fields exist; zero volume is a present bar, too.
        present = (panel.present[symbol].to_numpy(dtype=bool) if panel.present is not None
                   else np.logical_or.reduce([pd.notna(value) for value in values.values()]))
        positions = np.flatnonzero(present)[-21:]
        if not len(positions):
            continue
        chunks.append(pd.DataFrame({
            'symbol': symbol,
            'ts': [stamp.isoformat() for stamp in panel.close.index.take(positions)],
            **{field: value[positions] for field, value in values.items()},
        }))
    return pd.concat(chunks, ignore_index=True)[_COLUMNS] if chunks else pd.DataFrame(columns=_COLUMNS)
