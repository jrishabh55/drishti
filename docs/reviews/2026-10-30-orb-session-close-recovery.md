# ORB closing-data gaps and entry-window recovery

## Production incident

At 17:26 IST on 2026-10-30 the deployed scanner returned zero usable F&O charts of 217. This was reproduced directly from the production scanner store and in signed-in Chrome. All charts had a 15:25 timestamp, but 151 were missing 15:20 and 66 were missing both 15:15 and 15:20. Fresh two-symbol Yahoo downloads for RELIANCE and ABB reproduced those omissions, so a cache restart alone could not repair the data.

The previous fix covered delayed contiguous prefixes during market hours. It deliberately required the whole session after close. That discarded valid earlier breakout observations because of unrelated bars after the configured 14:45 entry deadline. Its after-close warning only checked the newest timestamp, hiding internal gaps when 15:25 existed. The ingest loop also treated a positive write count as sufficient final-sweep success, and a global MAX timestamp suppressed further recovery.

## Change

- Automatic delayed scans cap the required observation window at the configured entry deadline. Every bar inside that window must exist, including the opening range. Holding back an unconfirmed cached last row cannot shorten a fixed deadline window. During an uncapped active session the existing fresh-prefix behavior remains.
- Explicit date/time replays, live sources and scans without an entry deadline keep strict requested-window coverage. No bars are interpolated, forward-filled or manufactured. This change does not make incomplete sessions eligible for the Time Machine or paper replay.
- `evaluation_as_of` records the required window end; `data_as_of` remains the newest bar actually evaluated. The web column is now “Last evaluated”; after the deadline its prices are historical window closes, not current quotes.
- Coverage diagnostics report missing bar times even when the final timestamp exists. The screen separates usable entry-window charts from complete full-session data.
- Final recovery checks coverage across the instrument universe, outside the event loop thread. Incomplete sessions retry at the existing ten-minute cadence from 16:00 until 18:00. Stale-data and failed-download paths obey the same daytime bound. Startup retains its single bounded catch-up attempt at any hour; no overnight intraday polling follows it.

## Verification

The captured production dataset, with unchanged missing bars and default price/liquidity filters, yields 213 usable charts, four illiquid charts and 101 breakout matches. An explicit full-session replay of that same dataset still rejects all 217, while reporting the missing 15:15/15:20 slots.

Regression tests first failed against the old behavior. Cases cover the closing transition, exact production gap pattern, holes before the entry deadline, late breakouts, off-grid deadlines, explicit/live/unlimited strictness, stale cached partial candles at 14:46/15:10/15:29/17:00, incomplete final-sweep retries, and failed downloads with current/old/missing caches after 18:00. Independent review found and prompted fixes to deadline shrinking and overnight retries; its final review reported no blockers and independently passed all 50 focused tests.

Final verification: 2,495 backend tests and 103 subtests passed (three integration tests deselected; 42 existing warnings), 81 frontend tests passed, TypeScript checking passed, production Node build passed, and git diff whitespace checks passed.


## Deployed verification

App commit `1324ef6` was pushed to main and released through Dokploy deployment `nlkwGYLUJle9rR6lNv5Sw` (done). Both containers became healthy. The deployed ORB and ingest files matched local SHA-256 values `b79adafa3b72d4cb7b850ce43021d458453e842850e0736a2d6ab021ba081e19` and `569b38dadb8b7cd450818ef7789d8cb63839d4ce35d6464e28c8c41368883009` respectively.

Read-only production evaluation at 17:36 IST returned 213 usable charts, four illiquid charts, zero unusable entry windows and 101 matches. The unchanged feed still had 217 incomplete full sessions: 66 missing 15:15 and all 217 missing 15:20. Explicit full-session evaluation continued to reject those charts. `KITE_ORDERS_ENABLED` remained `0`.

After reloading the signed-in Chrome page, native accessibility inspection confirmed “213 charts usable of 217 for the entry window”, the separate zero-of-217 full-session summary, explicit gap times, populated breakout rows, “LAST EVALUATED”, and the 14:40 IST evaluated-bar badge for the 14:45 entry deadline. The Default paper portfolio selector was present. Public page and all referenced JavaScript/CSS assets returned HTTP 200. No trades, grants, settings changes or messages were created during validation. The independent reviewer is complete; no review worker remains running.
