# Long daily history extension

User scope: combine Yahoo and Zerodha where available, requesting daily history from 2000 onward for crash/regime research. Preserve the running two-year intraday download and its tests. Live probes already confirmed RELIANCE daily candles in January 2000 from both providers, and January 2008 close differences. These are observed examples, not a completeness guarantee.

Implementation choices: independent owner-scoped provider archives; daily-only 1900-calendar-day chunks for Kite, bounded full-span Yahoo daily series; explicit Yahoo auto_adjust=False/back_adjust=False/repair=False with adjusted-close/dividends/splits preserved separately. Keep all adjustments identifiable. Compare overlaps, never splice inconsistent candles or silently manufacture missing data. Current-universe survivorship remains disclosed. No new product screen, public API widening or broker/ledger writes. This extension collects daily data; it cannot make 2008 intraday strategies testable from daily candles.

Tasks:
- Add tested optional daily-only/chunk/provider metadata support to Archive, retaining legacy defaults.
- Add tested Yahoo normalization and source comparison with coverage and mismatch reports.
- Run a resumable daily collector from 2000 through the original frozen end date, using read-only owner credentials for Kite and public Yahoo data, with the existing budget/session/validation guards.
- Add a separate bounded launcher profile; retain original worker/image/source and app containers unchanged.
- Independently review, run relevant tests, launch and verify real old candles/progress and order flags.
- Record actual results/limits and source versions; commit and push after checks.

## Verification and release

All tasks above implemented/reviewed and operationally launched; historical collection remains in progress. Final reviewed source digest: `971c1f5abf2b5b6296b973971ca670d60b1d2477df9e482432d7d1d2c4df9d9e`, using the existing immutable API image. Active container `drishti-long-daily-history-20261031`; current archive base `long-daily-20261031-v2`. No application restart or order enablement.

Initial import diagnosis: Yahoo returned 6039 rows for 3MINDIA, with only 2026-10-30 invalid because Close was NaN. The importer originally rejected the entire series. A failing regression test reproduced that behavior. The corrected implementation preserves valid rows without changing prices and records each invalid bar with date, reason and sanitized values in checksummed provider metadata. Out-of-range dates and conflicting valid duplicates still reject. The initial worker was stopped/renamed and its data preserved, while the corrected version starts a fresh source-pinned archive. The original two-year worker was untouched.

Independent review cleared the final fix.30 focused tests and 128 relevant combined backend tests pass, plus compile/whitespace checks. Real worker verification: 6 Kite chunks / 2020 candles and 5 Yahoo series / 16440 candles completed at the sample, with zero failures in the corrected worker. Yahoo's incomplete latest-day bar was explicitly present in metadata, excluded from valid rows. Private candle mode 0600; saved production strategies 0, broker trade records 0, live-order flag 0. The original worker entered research after 677 chunks / 1085870 candles, with its comparison results still pending.

Requested daily coverage is 2000-01-01 through 2026-10-30; actual coverage depends on each provider/symbol. Source disagreement does not prove which source is correct. Historical symbol identity, delisted constituents, corporate actions and survivorship still require review before long-horizon backtests. This collection extension does not change intraday replay bounds or create synthetic intraday bars.
