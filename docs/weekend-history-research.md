# Private weekend historical research

The standalone worker downloads a frozen current NIFTY500 universe into the private persistent volume. It uses the connected owner's encrypted Kite session and never submits orders, enables strategies, or writes to a trading ledger. The web application is not restarted to launch it.

## Scope and sequencing

- Rolling 730 calendar days of five-minute candles; daily candles include 365 extra calendar days for pre-study liquidity selection.
- Frozen current constituents and instrument tokens, with unavailable symbols recorded. This introduces survivorship bias; it does not reconstruct historical index membership or delisted companies.
- Start with one old daily request and one old 90-day intraday request. Empty/invalid probes stop the job explicitly.
- One historical request/second, bounded retries, atomic compressed chunks and checksum verification. Daily data comes first; then the 20 most liquid symbols using median traded value over 60 pre-study sessions (minimum 40), then the remaining archive.
- Once the selected 20 series are complete, run four predefined long ORB strategies: 15-minute/mid-stop, 30-minute/mid-stop, 15-minute/low-stop, and15-minute/mid-stop with 1.5x volume. Each targets 2R and has a higher-slippage comparison. There are eight runs, not an unconstrained parameter search.
- The existing Trade Desk fill, fee, sizing and risk engine carries one shared account continuously across the full period. The last 183 calendar days are held out. No winner is automatically deployed.

Research outputs are private JSON trade details, coverage, train/test, monthly/quarterly slices, `comparison.csv` and `comparison.md`. They are operational artifacts, **not currently a new screen in the app**. Download completion does not imply complete exchange-session coverage: missing bars stay missing and incomplete sessions are excluded. The existing calendar only enumerates 2026 holidays, so earlier exchange closures may appear as no-data days. Historical corporate actions and exchange/broker facts are not reconstructed, and costs are estimates. Results need coverage, trade-count and out-of-sample review before any paper/live deployment.

## Operations

From the repository:

```sh
python3 deploy/weekend-research.py launch
python3 deploy/weekend-research.py status
```

The launcher uses SSH host `codeation-dev` and fixed container `drishti-weekend-research-20261031`. Repeated launch is a no-op for identical source, and refuses to silently replace another version. The immutable existing API image plus the research source digest identify the engine. The private archive is under `/home/appuser/.tradingagents/research-archives/weekend-20261031` in the application data volume. An owner-hash directory contains the manifest, chunks and outputs; no public route serves them.

The worker is limited to 0.5CPU, 1100MiB RAM, 1400MiB RAM plus swap, 64processes and single-thread numeric libraries. Logs rotate at 5MiB×2. Download budget is 4GiB with at least 5GiB free-space reserve. The container root and three application database files are read-only mounts. Their containing volume permits SQLite WAL/SHM coordination; source connections additionally enforce `mode=ro`, query-only SQL and a SELECT-only authorizer. All source connections close explicitly. Only four necessary configuration/credential variables are passed internally on the host through stdin; no secrets are copied into the archive or console.

Kite login is not refreshed automatically. On expiry, revocation or changed credentials, the worker pauses and rechecks after normal manual connection renewal. Checkpoints survive process/API/host restarts, and completed data is verified on restart. Offline research can run after session expiry if its selected data has already arrived. A new login also gives the application's initial warmup 30 minutes of head start before batch downloads resume. The live-order environment flag stays disabled.

A stopped completed worker need not be restarted. To resume an interrupted existing job, inspect its sanitized status and restart the same container; it reuses the immutable configuration and verified checkpoints. Failed chunks remain explicitly recorded rather than being silently treated as valid data. Investigate failures before resetting them.

## Daily archive extension from 2000

The separate `long-daily` profile requests daily history from 2000-01-01 through the original job's frozen end date. It leaves the two-year intraday worker and its immutable source untouched:

```sh
python3 deploy/weekend-research.py launch --profile long-daily
python3 deploy/weekend-research.py status --profile long-daily
```

Container: `drishti-long-daily-history-20261031`; archive base: `/home/appuser/.tradingagents/research-archives/long-daily-20261031-v2`. Limits: 0.25CPU, 512MiB RAM, 768MiB RAM+swap. Each Kite request is spaced at least two seconds after the previous request completes, alongside the original worker's one request/second. Source data uses the same private owner and broker-account guard, source DB files stay read-only, and no app/API restart or order enablement is required.

Each owner directory contains separate `kite` and `yahoo` archives, with checksummed source-stamped candles, manifests and retry/empty/failure counts. Kite daily requests span at most 1900 calendar days, comfortably under its published 2000-day request limit. Yahoo receives one bounded full-period daily request per symbol. The Yahoo universe includes the current 500 names; Kite uses the 497 matched at initial connection. Missing/pre-listing periods remain absent. Actual earliest/latest dates are recorded; the target start is not a guarantee of coverage.

Yahoo uses explicit `auto_adjust=False`, `back_adjust=False`, `repair=False` and `actions=True`. Its per-chunk private metadata preserves adjusted close, dividends and stock splits with a digest checked on restart. Provider-supplied OHLC is preserved; these settings do not establish historical as-traded prices. No custom adjustment or silent price repair is applied.

`source-comparison.json` is generated after collection resolves. Existing interim reports retain their earlier snapshot date; current download counts are in `status.json`. It reports actual date coverage, per-year candle counts for completed overlapping series, source-only dates, OHLC differences above 0.5% (minimum ₹0.01), volume differences above 5% (minimum 1 unit), and sample discrepancies. These are review thresholds, not proof that smaller differences are harmless. Incomplete/failed series remain explicit. **Automatic merging is disabled:** neither source's bars are inserted into the other source's series or fed to new backtests. Reconciled source selection is required before future long-horizon strategy tests. The existing eight intraday comparisons continue using their original Kite-only archive and date bounds.

Live availability probes on 2026-10-31 IST returned RELIANCE daily candles from January 2000 on both sources. For 2008-01-01, Kite close was ₹339.40, Yahoo close ₹325.531006 and Yahoo adjusted close ₹284.750122. That demonstrates why identical exchange/symbol/date does not guarantee comparable price bases; it does not establish which adjustment methodology caused that particular difference. Yahoo's NIFTY index probe only began 2007-09-17, illustrating symbol-specific coverage limits.

The first live import exposed an incomplete latest Yahoo daily candle with a missing close. The corrected importer preserves valid candles and records invalid ones in `rejected_rows` (date, reason, finite values or null) in the checksummed provider metadata. It never fills or repairs those bars. `source-comparison.json` counts rejected Yahoo rows once both full provider series are available. Date-bound violations and conflicting valid duplicates still reject a series. The initial attempt and its archive are preserved in a stopped `drishti-long-daily-history-20261031-rejected-import` container; the active importer uses the `-v2` base above. The original two-year worker was not stopped or changed.

## Resuming after the report scheduling correction

The daily worker previously rebuilt the entire growing source comparison every five minutes. Once that report took longer than the interval, it ran again after almost every new download and slowed collection. Expensive comparison generation now runs once after runnable downloads resolve; progress status remains cheap and updates during collection. The separate two-year archive and its eight research comparisons completed before this correction.

A controlled daily-worker upgrade retains the existing dataset engine version and archive path while recording the actual new runtime source version in each manifest and new Yahoo sidecar metadata. It validates the existing image, dataset contract and owner hashes before disabling the old worker's restart policy, stopping/renaming it and starting its replacement. Existing candle files and metadata remain unchanged.

```sh
python3 deploy/weekend-research.py upgrade --profile long-daily
```

Use only a reviewed, compatible report/worker update. The old container is retained for diagnosis/rollback; it must stay stopped while the replacement owns the archive. This does not renew Kite authentication. Normal manual Kite reconnection remains necessary before expired-token downloads resume.

Verified 2026-10-31: the original two-year archive completed all 4970 chunks without failures and produced all 8 research comparisons. Long daily collection remained incomplete when the daily Kite session expired. The user reconnected at 10:24 IST; the source account/session were verified, and the existing 30-minute API warmup grace ends at 10:54 IST. The worker's internal `awaiting_kite_login` phase also covers this initial warmup grace; a valid session during that interval requires no further login. Yahoo continues independently. One old ASHOKLEY daily chunk (2000-01-01 through 2005-03-14) remains explicitly failed for invalid candles and needs source review; it was not silently accepted.

The report scheduling upgrade was verified live using the same source archives and original dataset engine, with the new runtime recorded. Yahoo advanced from 438 completed histories before the update to 440 during verification; old chunk data and source provenance were retained. 130 relevant backend tests passed, including the download-loop regression and controlled upgrade/mount/environment tests.

## Existing history stays immutable

Saved historical candle files are append-only. Normal reconnects and restarts skip completed batches. The worker refuses to replace a saved candle file or a finalized Yahoo metadata file. A missing/corrupt checkpointed artifact pauses for explicit review rather than automatically downloading a replacement. Valid candle files left by an interrupted manifest commit are checkpointed locally without HTTP or changing their bytes. Metadata-only interrupted Yahoo captures also pause for review; saved sidecars are preserved.

Long daily Kite collection now reuses verified daily chunks from the original private two-year research archive. Owner, broker account, instrument token, provider, dates, source metadata and SHA must match. Cached values and their known holes remain unchanged. Only date intervals outside saved coverage reach Kite. Each new composed Kite chunk stores the cached source SHA/version and newly fetched intervals inside the same checksummed candle file. Parent archives are never repaired or written by the cache. Yahoo and Kite remain separate sources.

Live verification after the controlled update: all 5864 previously saved candle files (289147709 bytes) retained their original SHA, size and modification timestamp; zero changed or missing. A production RELIANCE request for 2026-01-01 through 2026-10-30 returned 204 existing daily candles with **zero HTTP calls**, recording one cached source segment and no network segments. The active worker retained the same v2 archive/configuration and source account. The updated source digest is `83559e4ce758ace55cc8599fcd0c778f67397716ed1d2d19b6b90d5534620b4a`.

156 relevant tests pass on the combined final patch, including overwrite refusal, orphan recovery without credentials/network, incomplete metadata preservation, source-account/instrument checks, cached/prefix-only requests, changed sessions and atomic provenance. A separate broad regression run before final wiring passed 2700 tests and 103 subtests; the final focused checks above cover the completed wiring and guards. Yahoo completed 500 of 500 histories (1990170 daily provider records). Kite remains incomplete until the warmup grace ends, with one explicitly failed ASHOKLEY source chunk retained for review. These counts are provider records, not a deduplicated count of every exchange candle since 2000.
