"""Zerodha Kite Connect integration: per-user connection, live bars, guarded execution.

Separate from desk/ on purpose — it's a broker integration other features can
use. Live market data here is licensed to the connecting user: it stays in
per-user memory (live.py) and is never written to shared stores.
"""
