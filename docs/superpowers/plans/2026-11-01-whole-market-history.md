# Whole Market History Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for the collector and independent review before launch.

**Goal:** Backfill available daily history since 2000 for the remaining NSE/BSE ordinary shares, including NSE SME, without changing or downloading existing historical records again.

**Architecture:** Freeze official NSE equity/SME, official BSE security-master and public Kite instrument catalogs. Keep venue-qualified series separate, with ISIN-based company grouping and explicit mapping exclusions. Reference existing owner-scoped provider archives and queue only new provider/listing series. A separate persistent low-resource worker runs one Kite and one Yahoo download loop, retains source validation/append-only files, bounded requests/retries/session expiry and a shared disk budget. No live orders, strategy enablement or application restart.

**Tech Stack:** Existing Python archive/Kite/Yahoo adapters, stdlib CSV/ZIP/SQLite/threading, current immutable API image; no new packages.

**Spec:** User request to fill the rest of Indian stocks; prior instructions preserve all existing history and use Yahoo/Kite separately from 2000 where available.

## Global Constraints
- Existing candle files/metadata remain immutable; completed/partial legacy provider archives are referenced, never reset or refreshed.
- Exclude indices, funds, debt, preferences, rights and uncertain identities. Preserve exchange/status/ISIN and explicit reasons for unavailable mappings. Listing dates do not establish earlier history availability.
- Both NSE and BSE venues remain separate, even when ISIN matches. Same-company grouping is for counts, never candle splicing.
- Historical request bound 1900 days, maximum 1 Kite request/second, maximum 1 Yahoo history request/2seconds. Pause Kite on expired/changed session, give login warmup 30 minutes. Yahoo proceeds independently.
- Finite current universe bounds; at least 5GiB disk reserve, 8GiB combined new-artifact budget, one worker per archive. Resource limits 0.5CPU/768MiB RAM/1024MiB RAM+swap.
- Owner and broker scope validated, read-only source DB files, credentials stay encrypted/in memory, no raw secret/owner IDs in logs or exported reports. No orders/ledger writes.
- Explicit chart identity checks for Yahoo (symbol/venue/currency/type). Keep actual invalid bars quarantined and source metadata; missing histories/aliases remain explicit, not fake-filled.
- Current/suspended securities can be catalogued if identity is corroborated; do not claim a complete universe of every stock delisted since 2000.

## Review Focus
- Cross-venue or symbol collisions must not overwrite/merge company records.
- Unverified Yahoo aliases, ETF/bond misclassification and instrument mismatches must not silently enter research data.
- Current 500 archives, failed historical chunks and empty source periods stay untouched and are not re-requested.
- Large task queues must use indexed single-row selection rather than repeatedly materializing every pending task.
- Session expiry,429/circuit pauses, interruption and disk exhaustion preserve checkpoints and valid historical bytes.

## Tasks
- [x] Freeze/test official universe parser, provider matching and bounded public downloads (`market_universe.py`, parser tests).
- [x] Test/implement owner-scoped existing-series references, new task plans and chart identity validation (`market_history.py`, worker tests).
- [x] Test/implement indexed archive queue selection and bounded two-provider worker/status/budget.
- [x] Add isolated deployment profile, review complete patch and run relevant checks.
- [x] Snapshot existing candle hashes, launch reviewed worker, verify actual downloads/reuse/progress and unchanged historical files. Record counts/limits and commit/push.

## Release evidence

- Classified 8842 venue candidates/5661 ISINs from frozen official source artifacts, with all non-equity/uncertain/inactive exclusions retained.
- Planned new 7652 Kite series/45912 date chunks and 8344 Yahoo series; old 497 Kite/498 Yahoo matching series are references, not downloads.
- Standalone worker `drishti-all-market-history-20261101` runs 0.5CPU/768MiB with source digest 5575adf2ce02cbf42cd37813accbe93d5c55106b2fa5d01aaea2d497ff885640. No API/web restart or orders enabled.
- Actual new rows were verified from both providers (Kite 145138, Yahoo 56795 at sample). Unavailable/identity failures remain explicit; no fallback merges.
- 8463 existing candle files/317412729 bytes checked: zero changed or missing.
- 186 relevant tests passed; independent final review cleared frozen setup recovery, Yahoo reason reporting, actual coverage report, fatal-worker restart and priority-only queue changes.
- Collection remains ongoing, with daily Kite login renewal and vendor history availability constraints. Current and suspended/delisted coverage are not conflated. See `docs/whole-market-history.md`.
