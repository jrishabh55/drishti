"""Trade Desk: the day-trader-skill pack ported to NSE cash equities.

Design: docs/superpowers/specs/2026-10-29-trade-desk-design.md. Rule logic is
ported from https://github.com/Jakeschincariol/day-trader-skill (MIT) — see
THIRD_PARTY_NOTICES.md in this folder.

Layout:
  market/       NSE session clock, tick sizes, charge estimates (pure)
  bars.py       bar conversions + on-demand deep history (yfinance), cached
  bars_source   the get_intraday_bars / get_history_bars seam (delayed now,
                live providers register later — Kite Auto-trader spec)
  timemachine   Monte Carlo replays + history replay + verdict
  orb/entry/exit/sizing/risk/paper/ticket   the intraday flow
  store.py      data/desk.sqlite
"""
