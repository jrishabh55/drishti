# NSE/BSE historical daily archive

The whole-market expansion adds the rest of the current official ordinary-share universe to the existing private research archive. It requests daily data from 2000-01-01 through the existing frozen end date 2026-10-30. It does not replace existing candles or enable trades.

## Universe and identity

The frozen sources are NSE main-board equity and SME lists, BSE's public security-master ZIP, and public NSE/BSE Kite instrument CSVs. The observed catalog has 8842 exchange listings (NSE 3198, BSE 5644), representing 5661 distinct ordinary-equity ISINs. These are securities and venue listings, not a reconstructed history of all companies that existed since 2000.

Classification requires a valid ISIN checksum, company issuer prefix INE and ordinary-equity security code 01, supported NSE series or active BSE EQ/E flags. Funds, debt, rights, preferences, ambiguous identities and unsupported statuses are recorded in explicit exclusion/uncertainty lists. 6106 suspended BSE records are excluded. ISIN grouping never combines different venues' prices. Same-company NSE/BSE candles are separate series.

Kite mappings require exact official symbol/series or BSE security-code matching. Yahoo suffix candidates require runtime chart metadata to match requested symbol, exchange, INR currency and EQUITY type. Unavailable histories and identity mismatches retain distinct failure reasons. Listing dates are kept as metadata rather than assumed to establish the first available historical candle.

## Existing data

The existing owner-scoped long-history archives are verified and referenced. Both completed and partially completed legacy series are skipped: this expansion does not reset the eight historical Kite validation failures or fetch old 500 records again. Changed identities and missing source manifests fail safely. Venue-qualified keys prevent BSE records from colliding with saved NSE records.

All new archives use append-only candle files, create-only source metadata and the existing interruption/integrity guards. Frozen catalog source artifacts and plans are reused after restart; partial setup is recoverable without replacing saved snapshots. The old application and research containers are not restarted.

## Worker and operation

```sh
python3 deploy/weekend-research.py launch --profile all-market
python3 deploy/weekend-research.py status --profile all-market
```

Container: `drishti-all-market-history-20261101`. Base: `/home/appuser/.tradingagents/research-archives/all-market-20261101`. Existing archive references point to `long-daily-20261031-v2`; owner and broker scope must match the original `weekend-20261031` connection.

One Kite and one Yahoo loop run independently. Kite uses at most one historical request/second in 1900-day chunks and pauses on session expiry/changed credentials, with a 30-minute grace after daily login for application warmup. Yahoo makes at most one history call per two seconds, uses bounded retries and a circuit pause after repeated transient failures. Missing data is not filled or manufactured. Fatal unexpected worker errors stop the peer and exit nonzero for bounded Docker restart; login, disk and manual integrity-review pauses remain deliberate states.

Limits: 0.5 CPU, 768MiB RAM, 1024MiB RAM+swap, 64 PIDs, 8GiB shared new-artifact budget and at least 5GiB disk reserve. Credentials stay on the host/inside the process; source DB files and mounted research code are read-only. No order endpoint, ledger mutation, strategy enablement or external notification is used.

Progress is in `status.json`; frozen universe and exclusions in `catalog.json`; planned new series and references in `plan.json`. Final `coverage.json` reports per-listing candle/chunk counts, actual observed first/last dates, error-reason counts and any unknown coverage metadata. Provider counts overlap and must not be summed as unique exchange candles. This is a private archive job; it does not add a new app screen or make 2008 intraday simulations possible from daily candles.

## Sources

- [NSE securities available for trading](https://www.nseindia.com/static/market-data/securities-available-for-trading)
- [Official BSE public security master](https://www.bseindia.com/downloads/Help/file/scrip.zip)
- [NSDL security/ISIN guidance](https://nsdl.co.in/downloadables/Core%20Services.pdf)
- [Kite instrument reference](https://kite.trade/docs/connect/v3/market-quotes/)

## Verified launch

Launched on 2026-11-01 IST with the existing immutable API image and no app restart. Current worker source digest: `5575adf2ce02cbf42cd37813accbe93d5c55106b2fa5d01aaea2d497ff885640`. The initial ordering was adjusted to prioritize NSE, then Kite-corroborated BSE, before unsupported broker candidates; this changed no mappings, archive configuration, saved candles or failed-task statuses.

The frozen plan schedules 7652 new Kite venue series (45912 date chunks) and 8344 new Yahoo histories. It references 497 existing Kite and 498 existing Yahoo listings that match this conservative catalog; the two legacy Yahoo names outside the catalog remain saved and untouched. Both complete and partial old provider datasets are retained. Neither old validation failures nor failed new lookup attempts are reset by restarts or reprioritization.

At the live sample, new collection had 213 completed Kite chunks / 145138 candles and 24 completed Yahoo histories / 56795 candles, including verified NSE:20MICRONS records from both providers. These are provider records and overlap; they are not a unique candle total. Yahoo's initial unmatched BSE candidates and some NSE SME histories were explicitly unavailable or failed chart identity; no alternate ticker or cross-venue price was silently substituted. New source errors remain visible for review.

A before/after hash inventory verified all 8463 previously saved candle files (317412729 bytes) retained their exact SHA, size and modification time. No previously saved candles were replaced. 186 relevant tests passed, including catalog classification, source reuse, queue/restart ordering, provider isolation, disk accounting, fatal-worker restart, chart identity and launch/upgrade constraints. Independent reviewers cleared the final patch. The run is still in progress; current counts are in status.json, and provider availability is not a guarantee of every exchange session since 2000.
