# Weekend History Research Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to execute the bounded research utility tasks. Keep product trading behavior and live gates unchanged.

**Goal:** Download a durable, private rolling two-year daily/5-minute archive tonight and compare predefined Trade Desk strategy variants with a held-out final six months.
**Architecture:** A standalone, resumable research utility reuses the existing owner's encrypted Kite credentials, historical REST adapter and deterministic backtest engine. Store owner-scoped compressed chunks and a manifest on the persistent private volume. This is an operational research job: do not widen public API backtest limits, start live strategies, or add UI features. Results are versioned private JSON/CSV/report artifacts.
**Tech Stack:** Python stdlib SQLite/gzip, existing Kite adapter/Pydantic/backtest engine; no new packages.
**Spec:** User request to backfill the last couple of years over the weekend and evaluate historical strategies before Monday. Coordinator defaults below are implementation choices, not user-selected parameters.

## Global Constraints
- Owner-only data; never put Kite history into global Yahoo/scanner caches. Files/directories private. No secrets or raw owner IDs in reports/logs/source.
- Read-only broker calls only: instrument list + historical candles. Live order flag remains0 and no strategy is enabled.
- Freeze the current eligible NIFTY500 universe (497 at setup), disclose survivorship/renames. Rolling730 calendar days 5m, daily365-day warmup preceding that range, through the last completed session.
- Request 5m data in <=90-day chunks; one request/second, bounded retries/backoff and recheck current owner session before every network request. Do not compete with the initial API warmup: delay batch start until that workload has had a bounded head start. Pause on expiration/revocation and resume checkpoints after daily login.
- Atomic gzip chunk files with digests, checkpoint only after valid candles persisted. Reject malformed/nonfinite/future/off-grid bars; keep holes/empty periods explicit. At least5GiB free-space reserve, archive budget4GiB.
- One worker, low CPU/thread use. Research replay<=20 fixed symbols, one strategy at a time, no model calls.
- Predefine four ORB variants (15/30-minute range, range-mid/range-low stop, optional1.5x volume;2R target). Choose test symbols without inspecting strategy outcomes; freeze their list and selection method. Carry portfolio accounting continuously across the two-year run using the existing engine; do not sum reset-window equity as a single portfolio.
- Keep final183 calendar days held out; report fees, slippage, net P&L, drawdown, trade counts, coverage and quarter/month regimes. Also run doubled-slippage stress. Do not label any strategy profitable/ready without adequate out-of-sample coverage.

## Review Focus
- Cross-owner access and revoked/expired sessions must pause downloads without copying credentials.
- Interrupted downloads cannot be marked complete; retries do not corrupt files or silently fill gaps.
- Dataset chunk boundaries/time zones/deduplication must not introduce future bars or double entries.
- Offline replay must never invoke broker order APIs or modify actual paper/live ledgers.
- Long-range simulation must preserve the existing engine's continuous shared-account semantics and leave the interactive60-day guard intact.

## Tasks
- [x] Inspect resource/session status and probe historical request availability; record actual time bounds.
- [x] Implement/test archive manifests, atomic compressed chunks, validation, retry/session/disk bounds and single-worker lock.
- [x] Implement/test bounded offline replay on archive data with explicit extended research validation and owner checking; preserve public API limits.
- [x] Review the isolated patch and run relevant backend checks.
- [x] Install the reviewed utility on the private volume; start a persistent checkpointed worker without redeploying/restarting the API.
- [x] Verify real downloaded candles/progress, worker identity/resource use and absence of order/strategy changes. Document resume/status commands and research limitations.
- [ ] Produce the comparison automatically once required data is present; report actual results or a concrete paused/missing-data condition, never invented performance.

## Launch evidence (2026-10-31 IST)

- Separate container `drishti-weekend-research-20261031` running with source digest `4d10bb13b96587820edb0993e6db7e24d3a17382e3e0295e7b194eb6055db1ce` and existing API image `sha256:e4502a40b6ebce7e7b9c9676d62e90083b78a0895f692b5cb995bc011c27394e`. No API restart.
- Frozen universe:497 symbols,4970 chunks; skipped HEG, HFCL, ICICIPRULI. Intraday study2024-10-31 through2026-10-30; daily history starts2023-11-01.
- Real RELIANCE probes succeeded:743 daily candles (first2023-11-01,last2026-10-29) and4512 five-minute candles (2024-10-31 through2025-01-28); private chunk modes0600. These are observed rows, not a claim that every requested session exists.
- Worker metadata verified: read-only root,0.5CPU,1100MiB RAM,64PIDs,zero restarts; three application database files and source package mounted read-only. Writable containing directories permit WAL/SHM coordination; SQL reader has mode=ro, query_only, SELECT-only authorizer and explicit closure. This replaces the initial whole-volume-read-only plan because SQLite needs coordination sidecars even for record reads.
- Production saved strategies0, broker trade records0, live-order flag0 at verification.
-22 focused research tests and120 relevant combined backend tests pass. Independent reviewer cleared disk-pause, holdout boundary and WAL-reader corrections; no remaining blockers. Compile checks and git diff whitespace check pass.
- Automatic comparison is queued after the selected archive subset completes. Download/research completion and profitability are **not claimed** at launch; inspect sanitized status and output artifacts. No UI research screen was added.
- Operations and limitations: `docs/weekend-history-research.md`.
