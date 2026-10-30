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
