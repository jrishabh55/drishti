# Scanner performance repair

**Goal:** Avoid repeated full-universe database reads and pivots on scanner requests, including connected but stopped Kite feeds.

**Evidence:** Production authenticated daily preview: 37.574s. Controlled production read-only replay: public cold 14.811s, public warm 0.882s, stopped hub 30.278–37.055s. Scanner store contains 2,126 instruments and approximately 640k–680k bars per timeframe. API reports stopped Kite feed.

**Design:** Reuse the existing versioned public panels for every scoped scan. Create disposable completed-candle views and overlay only eligible selected-owner Kite columns. Resolve the public base timeframes once per scan. Aggregate weekly/monthly views after private daily overlay. Recheck access after evaluation as before. No private cache, persisted result cache, historical writes or provider downloads.

## Tasks

- [x] Add failing regressions: stopped/stale hubs do not reread bars; fresh private overlays reuse public panels; no repeated credential checks per symbol; overlay does not mutate public cache; completed candles, publication changes, owner revocation remain correct.
- [x] Implement scoped panel overlay and verify scanner/source/strategy regression suites. Measure baseline payload on production data using the candidate code in an isolated read-only process.
- [x] Independently review, integrate and deploy API. Verify authenticated production preview/status timing and confirm background backfill remains running.

## Constraints and review focus

Preserve user-selected owner/grants, stale-feed fallback, source provenance, weekly/monthly completion, public snapshot version pinning, raw archive immutability and the existing no-order scanner contract. Do not cache scan results across users or sources. Browser UI changes only if backend measurements leave a demonstrated issue.

## Verification before deployment

- 162 scanner, scoped-data, sharing and strategy-candidate tests passed.
- Independent review found an empty/insufficient-history streak edge case; reproduced with four failing cases, then fixed by requiring N completed rows. Reviewer reran 73 relevant tests; no remaining blockers.
- Candidate isolated read-only replay against production data: daily cold 15.766s (one-time panel build), then 0.950s/0.839s. Five-minute cold 16.814s (new timeframe), then 0.743s/0.699s. Both retain 2,126-symbol universe and the same 926 matches as baseline. Initial historical archive backfill continues independently.
- Startup now warms existing data before network catch-up. The first cold cache build is still real work; normal repeated scans no longer rebuild panels.

- Full Python suite: 2,749 passed, 3 deselected, 103 subtests passed (42 existing model-name warnings), 55.54s.

## Live first deployment and remaining Trade Desk path

Commit `31dc6ce` deployed through Dokploy; runtime scanner file hashes verified. Authenticated public URL timings after startup: daily scan 0.918–0.965s, 20-day closing breakout 1.112–1.269s (42 matches), 5m scan 1.120–1.302s. Initial cold requests during cache warm took 28.009s daily and 35.717s for 5m; these are not hidden in the warm figures.

A separately measured Trade Desk closed-session ORB request still took 17.138s. Follow-up replaces its daily liquidity SQL read with an adapter over public prepared panels, selecting each symbol's own last 21 persisted rows. Exact row-presence metadata preserves even fully-null stored sessions. No response/private cache or archive writes.

Follow-up also prepares panels before atomically publishing their bar version, keeps concurrent published writes invalidated instead of labeling an older view current, reuses daily charts for weekly/monthly warmup, and retains version invalidation if preparation fails. Regression tests reproduce each cache-publication race before fixing it.

Final follow-up verification: 205 focused tests and 2,762 full-suite tests passed (3 deselected, 103 subtests, 42 known-model warnings); independent final review reran 123 relevant tests and reported no remaining blockers.

## Final production verification

- Runtime code commit `d8a2f01`; API image `sha256:7232efb237e8b645e5a7c9730b1fc464a7cc47a64eddf941adf19f0599918cfa`. All seven changed runtime files match local SHA-256 hashes. Dokploy deployment reports done; API and web are healthy.
- Authenticated public HTTP after startup: daily baseline 1.262s/1.254s (926 matches, 2,126-symbol universe), 20-day closing breakout 1.639s/2.627s (42 matches), five-minute baseline 1.377s/1.994s (926 matches). Internal initial requests were 2.351s–3.585s before settling to 1.161s–1.473s. Cache preparation and background provider work still consume real startup time.
- Trade Desk Friday ORB replay: internal 2.102s then 1.229s; public 1.605s/1.805s, versus 17.138s before the adapter. Coverage remains exactly 0 complete/217 incomplete, with the same missing timestamps; performance changes do not fill or ignore missing candles. Sunday/default-today requests return expected HTTP 422 for a non-trading session; benchmark uses explicit Friday replay.
- Historical all-market worker remains running, zero restarts. No raw historical archive changes are introduced by this patch.
