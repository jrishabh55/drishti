"""Fork benchmark compatibility helpers."""
from tradingagents.dataflows.symbols import safe_ticker_component

# Yahoo Finance exchange-suffix → broad-market index ticker.
# Used for the post-trade alpha calculation in the reflection memory log so
# that an Indian stock is benchmarked against the Nifty rather than the S&P.
_BENCHMARK_BY_SUFFIX = {
    ".NS": "^NSEI",     # NSE → Nifty 50
    ".BO": "^BSESN",    # BSE → Sensex
    ".TO": "^GSPTSE",   # TSX → S&P/TSX Composite
    ".T":  "^N225",     # Tokyo → Nikkei 225
    ".HK": "^HSI",      # Hong Kong → Hang Seng
    ".L":  "^FTSE",     # London → FTSE 100
    ".PA": "^FCHI",     # Paris → CAC 40
    ".DE": "^GDAXI",    # Frankfurt → DAX
    ".AX": "^AXJO",     # Australia → ASX 200
    ".SS": "000001.SS", # Shanghai → SSE Composite
    ".SZ": "399001.SZ", # Shenzhen → SZSE Component
}

DEFAULT_BENCHMARK = "SPY"


def benchmark_for(ticker: str) -> str:
    """Return the broad-market benchmark to compare ``ticker`` against.

    Maps Yahoo Finance exchange suffixes (``.NS``, ``.TO``, …) to the
    corresponding index symbol. Tickers without a known suffix fall back to
    SPY, which preserves prior behavior for US equities.
    """
    if not isinstance(ticker, str) or "." not in ticker:
        return DEFAULT_BENCHMARK
    suffix = "." + ticker.rsplit(".", 1)[1].upper()
    return _BENCHMARK_BY_SUFFIX.get(suffix, DEFAULT_BENCHMARK)
