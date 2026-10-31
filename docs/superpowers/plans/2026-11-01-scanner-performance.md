# Scanner performance repair

**Goal:** Avoid repeated full-universe database reads and pivots on scanner requests, including connected but stopped Kite feeds.

**Evidence:** Production authenticated daily preview: 37.574s. Controlled production read-only replay: public cold 14.811s, public warm 0.882s, stopped hub 30.278–37.055s. Scanner store contains 2,126 instruments and approximately 640k–680k bars per timeframe. API reports stopped Kite feed.

**Design:** Reuse the existing versioned public panels for every scoped scan. Create disposable completed-candle views and overlay only eligible selected-owner Kite columns. Resolve the public base timeframes once per scan. Aggregate weekly/monthly views after private daily overlay. Recheck access after evaluation as before. No private cache, persisted result cache, historical writes or provider downloads.

## Tasks

- [x] Add failing regressions: stopped/stale hubs do not reread bars; fresh private overlays reuse public panels; no repeated credential checks per symbol; overlay does not mutate public cache; completed candles, publication changes, owner revocation remain correct.
- [x] Implement scoped panel overlay and verify scanner/source/strategy regression suites. Measure baseline payload on production data using the candidate code in an isolated read-only process.
- [ ] Independently review, integrate and deploy API. Verify authenticated production preview/status timing and confirm background backfill remains running.

## Constraints and review focus

Preserve user-selected owner/grants, stale-feed fallback, source provenance, weekly/monthly completion, public snapshot version pinning, raw archive immutability and the existing no-order scanner contract. Do not cache scan results across users or sources. Browser UI changes only if backend measurements leave a demonstrated issue.

## Verification before deployment

- 162 scanner, scoped-data, sharing and strategy-candidate tests passed.
- Independent review found an empty/insufficient-history streak edge case; reproduced with four failing cases, then fixed by requiring N completed rows. Reviewer reran 73 relevant tests; no remaining blockers.
- Candidate isolated read-only replay against production data: daily cold 15.766s (one-time panel build), then 0.950s/0.839s. Five-minute cold 16.814s (new timeframe), then 0.743s/0.699s. Both retain 2,126-symbol universe and the same 926 matches as baseline. Initial historical archive backfill continues independently.
- Startup now warms existing data before network catch-up. The first cold cache build is still real work; normal repeated scans no longer rebuild panels.

- Full Python suite: 2,749 passed, 3 deselected, 103 subtests passed (42 existing model-name warnings), 55.54s.
