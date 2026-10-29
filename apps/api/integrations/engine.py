"""Compatibility identity for product caches and graph checkpoints."""

import tradingagents


def engine_identity() -> dict:
    # Bump when the product's graph/state adapter changes incompatibly.
    return {"core_version": tradingagents.__version__, "stream_contract": 1}
