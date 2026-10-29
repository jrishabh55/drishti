# Trade Desk: day-trader-skill ported to NSE (Design + Roadmap)

**Date:** 2026-10-29
**Status:** Phases 0–3 and 5 implemented (2026-10-29); Phase 4 partly. See "Implementation status" at the end — the code is authoritative where it differs from the sections below.
**Scope:** All of this goes in the web/product layer: `apps/api/`, `apps/web/`, `apps/helper/`, `tests/` and `docs/`. **No changes to `cli/` or `tradingagents/`.** We only import read-only helpers from the core, the same way `levels.py` does.
**Source:** [Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill) @ `3d0f4ef` (2026-10-20), MIT © 2026 Jake Schincariol. Ported files carry the MIT notice (see §9).

The upstream pack has 11 Claude skills for US day trading. Each is a stdlib Python CLI that hands state to the next through `trader/*.json`. This project ports the **logic** into Drishti as product features for NSE cash equities:

- New York time becomes IST and NSE sessions.
- Dollars become rupees, and Indian charges are added.
- US data sources become NSE/BSE.
- The JSON files become per-user SQLite rows.
- Alpaca is replaced by a paper ledger and an order ticket the user places at Zerodha (Kite Publisher button). **Everything Kite-API related (live data, sending orders, automatic trading, performance tracking) is a separate feature:** see `2026-10-29-kite-autotrader-design.md`. This spec only defines the seams that feature plugs into (§3A).

> **Architecture principle: server first.** Everything that *can* run on the server runs on the server. The Drishti helper is used **only** for what *must* happen on the user's machine.
>
> The test is: "does this fail or become wrong when done from a datacenter IP or by the server?" Today that's exactly one thing in this project: **fetching NSE/BSE website data** (nseindia.com and bseindia.com block datacenter IPs). The existing helper uses (the Codex subscription relay and Reddit/StockTwits fetches) predate this spec and stay as they are.
>
> **Parsing, caching, bar building, alerts, scheduling (and, in the Kite feature, broker connections and orders) are all server-side.** The helper stays a thin, dumb fetcher: it receives a named request, returns raw bytes, and holds no business logic. Any new helper capability needs a written reason why the server can't do it.

---

## 0. What we're building

**Trade Desk** is one place where an intraday or swing idea goes through this sequence:

```
scan → news/filings check → entry trigger → exit plan → Time Machine odds → size (₹, net of charges) → risk checks → ticket (paper or user-placed) → alerts
```

It also has side feeds: an **insider tracker** (the Pelosi replacement) and a **results analyst** (the EDGAR replacement).

**Non-goals for v1:**

- No order placement through broker APIs. That's the Kite Auto-trader feature.
- No F&O, options or short selling (long-only, the same as `levels.py`).
- No real-time data in this feature. Bars are delayed Yahoo 5-minute bars, and every screen labels the source. Live Kite bars arrive later through the `get_intraday_bars` seam (§3A) without any desk changes.

**Upstream skill → Drishti mapping**

| Upstream | Drishti module | Data source (India) | Phase |
|---|---|---|---|
| trader-timemachine | `desk/timemachine.py` (intraday **and** swing mode) | yfinance `.NS` 5m (60d) / 1d | 1 |
| trader-scanner (`orb`) | `desk/orb.py` | scanner store 5m bars (already ingested) | 2 |
| trader-scanner (`daily`) | **prebuilt scanner JSONs** (no new code) | scanner store 1d | 2 |
| trader-entry | `desk/entry.py` | scanner store 5m / on-demand fetch | 2 |
| trader-exit | `desk/exit.py` | — | 2 |
| trader-budget | `desk/sizing.py` + `desk/market/charges.py` | — | 3 |
| trader-risk | `desk/risk.py` | profile + paper ledger + surveillance datasets | 3 (surveillance checks go live in 4) |
| trader-broker | `desk/ticket.py` + `desk/paper.py` | in-app paper ledger; Kite Publisher basket / copyable ticket (API orders: Kite Auto-trader feature) | 3 |
| trader-news | `desk/news.py` | Yahoo `.NS` RSS (server) + NSE/BSE announcements (server, or helper if blocked) | 4 |
| trader-pelosi | `desk/insider.py` ("Insider tracker") | NSE PIT disclosures, bulk/block deals, SAST (server, or helper if blocked) | 4 |
| trader-analyst | `desk/results.py` | NSE financial results + XBRL (server, or helper if blocked); yfinance fallback | 4 |
| trader-alerts | `desk/alerts.py` | scanner store / yfinance quotes (server); live bars via the seam once the Kite feature exists | 5 |

---

## 1. Decisions to make first

| # | Decision | Recommendation | Why |
|---|---|---|---|
| D1 | Order execution | **Decided:** in this feature, a Kite Publisher basket button (the user confirms on Zerodha; static-IP rules don't apply to Publisher orders) or a copyable ticket, plus the in-app paper ledger. API orders, manual and automatic, belong to the **Kite Auto-trader** feature, owner-only. | Keeps the day-trader port free of broker credentials and of the SEBI/Zerodha API-order rules. |
| D2 | Sharing NSE/BSE data | **Cache public NSE/BSE data globally**, however it was fetched. A helper-routed dataset is fetched only by a user's *own* request through their *own* helper. | The data is public and identical for everyone, and sharing it cuts the load on each helper. No background job ever borrows another user's helper. |
| D3 | Background feeds (insider and announcement polling) | **On-demand only in v1.** Option: a "house helper" (an always-on helper on an operator machine, bound to a service account) used only for shared feeds. Alternative to evaluate: a paid **residential/Indian proxy** for the server, which would move NSE fetching server-side entirely and fits the server-first principle better. | Without one of these, feeds refresh only when someone with a helper online opens them. |
| D4 | Alert delivery | **In-app (bell + toast) plus Telegram bot** in Phase 5. Web Push later. | Telegram reaches a phone with no service-worker or PWA work. Web Push is more work for less reach in India. |
| D5 | Default intraday universe | **F&O stocks** (`instruments.fno`, ~200 names). NIFTY500 is optional. | Liquidity and tight spreads. ORB on illiquid names is noise. |
| D6 | Time Machine for analysis runs | **Yes: swing mode** on `/runs/$id` using the existing `levels.py` entry, stop and target. | This is the highest-value use, and it reuses what already exists. |
| D7 | Chart library | **Add the shadcn `chart` component (recharts)** for the fan chart and histogram. | This is a new frontend dependency, so confirm it. The alternative is hand-written SVG. |
| D8 | Wording and compliance | The UI says "your plan", "simulation" and "rules check", never "buy/sell call". Drishti is not a SEBI-registered RA. **Get a legal review before any public launch.** | Specific trade calls to the public can count as research-analyst activity. |
| D10 | Live data | **Moved to the Kite Auto-trader feature** (server-side, from each user's own Kite app). The desk only exposes the seam (§3A). | Lets the day-trader port ship on delayed data first. |
| D11 | Helper scope | **Decided: the helper does only what can't be done from the server.** In this project that means NSE/BSE website fetches (§3). | Keeps the helper small, rarely re-released, and free of business logic. |
| D9 | Data licensing | Fine for a private beta. **Before a paid launch, check NSE's website terms** on automated access and redistribution, and consider licensed data. | Scraping nseindia.com and redistributing the data inside a commercial product is a real risk. |

---

## 2. Architecture

```
                       ┌──────────────── apps/web ────────────────┐
                       │ /desk  /desk/plan/$id  /desk/insiders     │
                       │ /desk/alerts   LevelsPanel ▸ "Replay odds"│
                       └───────────────┬──────────────────────────┘
                                       │ REST (Clerk auth)
┌──────────────────────── apps/api ────┴───────────────────────────────────┐
│ routes/desk.py                                                            │
│ desk/                                                                     │
│   market/  session.py (NSE clock, 75×5m) · ticks.py · charges.py          │
│   bars.py        on-demand 60d 5m / 2y 1d via yfinance, cached per day    │
│   orb.py entry.py exit.py timemachine.py sizing.py risk.py ticket.py      │
│   paper.py alerts.py news.py insider.py results.py                        │
│   bars_source.py  get_intraday_bars() seam (delayed now; live later)     │
│   feeds/ client.py ── server-direct fetch, else                           │
│                       relay registry.dispatch(user, "__dataset__", …)     │
│          parsers/nse.py bse.py                                            │
│   store.py  (data/desk.sqlite: per-user + shared feed cache)              │
│ scanner/ (existing: 5m/15m/1h/1d bar store, calendar, universe)           │
└───────────────────────────────────────────────┬───────────────────────────┘
                                                │ relay websocket (existing)
                                     ┌──────────┴────────── apps/helper ─────────────┐
                                     │ fetcher.py   "__fetch__"   (unchanged)         │
                                     │ datasets.py  "__dataset__" (NEW, the ONLY new  │
                                     │   helper work): named NSE/BSE datasets, pinned │
                                     │   URLs, local cookie jar, rate limit, raw bytes│
                                     └────────────────────────────────────────────────┘
```

- The package is **`apps/api/desk/`**, with its own DB file **`data/desk.sqlite`** (env `DESK_DB_PATH`). It follows the scanner pattern: WAL, per-call connections, and a `get_desk_store()` / `reset_desk_store_for_tests()` singleton. It never shares the runs DB or the scanner DB.
- **Reuse, don't duplicate:** `scanner/calendar.py` (holidays, `IST`), `scanner/store.py` (`load_bars("5m")` holds roughly the last 4 sessions, enough for today's ORB and entry checks), `scanner/universe.py` (the `fno` flag), and `integrations/levels.py` (swing-mode inputs).
- `bars.py` adds an **on-demand** deep-history fetch (60 days of 5m, 2 years of 1d) for one symbol, because the scanner store keeps only 320 bars. It caches per `(symbol, interval, as_of_date)` in `desk.sqlite` and uses the same clamped-`start` yfinance call as `scanner/ingest.py` (see the "aged young listings" fix in commit `8803630`).
- **Server-direct sources:** Yahoo (yfinance, RSS), which the scanner ingest already proves works from the server. **Helper sources:** only the NSE/BSE datasets that the Phase 0 spike shows fail from the server (§3.4).

### Upstream file → Drishti storage

| upstream `trader/…` | Drishti table (desk.sqlite) | scope |
|---|---|---|
| `config.json` | `desk_profiles` (risk limits, capital, flat_by, broker charge plan) | per user |
| `watchlist.json` / `portfolio.json` | `desk_watchlist`, `paper_positions` | per user |
| `plan.json` | `desk_plans` (setup, entry, stop, target, rule, OR, flat_by, status) | per user |
| `timemachine.json` | `desk_tm_results` (keyed by plan hash + data date + seed) | per user |
| `orders.json` + `state/risk.json` | `desk_tickets` (order, risk verdict, fingerprint) | per user |
| `state/broker.json` | `paper_fills`, `paper_ledger` | per user |
| `alerts.json` + `state/alerts.json` | `desk_alerts`, `desk_alert_events` | per user |
| — | `feed_cache` (dataset, params_hash, fetched_at, ttl, payload), `filings` (normalized announcements/PIT/deals) | **shared** (D2) |

---

## 3. NSE/BSE datasets (the only new helper work)

Each dataset carries a `route: "server" | "helper"` flag in the server-side catalog. **The default is `server`.** A dataset is switched to `helper` only when the Phase 0 spike shows the server's IP gets blocked or bot-challenged for it. Some `nsearchives.nseindia.com` CSVs may well work server-direct. If the D3 proxy option is adopted later, the flags flip back to `server` without any other code changes.

### 3.1 Why a new pseudo-provider rather than widening `__fetch__`

`__fetch__` is deliberately identity-free. It sends no cookies, has an exact host allowlist, is GET-only, and filters headers. That model doesn't fit NSE or BSE:

- **`www.nseindia.com/api/*` needs session cookies** that it sets when you visit the homepage, plus browser-like headers.
- **`api.bseindia.com` needs a `Referer: https://www.bseindia.com/`** header, and `__fetch__` strips it.
- Sending raw URLs and headers from the server would also weaken the "the server can't turn the helper into a proxy" guarantee in `fetcher.py`.

So we add **`apps/helper/datasets.py`** with pseudo-provider **`__dataset__`**. The server sends `{"dataset": "nse.insider_pit", "params": {...}}`, never a URL. The helper owns:

1. **A dataset catalog** that pins each dataset to a URL template on a fixed host and path, with typed params:
   - `symbol`: `^[A-Z0-9&\-]{1,20}$`
   - `from` and `to`: ISO dates, span ≤ 90d
   - `index`: an enum

   Unknown datasets or bad params are rejected **on the helper**.
2. **A cookie jar per host, in memory and on this machine only.** Cookies are never returned to the server and never logged. The helper warms up by visiting the homepage on first use, when the cookie is older than 10 minutes, or on a 401 or 403, and then retries once.
3. **Fixed headers per host**: Referer, Accept and a browser User-Agent. These are set by the helper, not supplied by the server.
4. **A per-host rate limit**, as a token bucket with defaults of 1 request per second for NSE and BSE and a small burst. It also has a backoff on 429.
5. **The same caps as `fetcher.py`**: 2 MB body, 20 s timeout, no redirects off the pinned host, and HTTPS only.
6. **It returns raw bytes** (`body_b64` plus status). Parsing stays on the server, so fixing a parser never needs a new helper build.

Widening the catalog is a helper code change and a release. The docstring states this, as `fetcher.py` already does.

### 3.2 Versioning and capability gating

- Bump `apps/helper/version.py` to **0.2.0**. The `hello` frame adds `"datasets:v1"` to `providers`, and the server already stores `conn.providers` and `conn.helper_version`.
- `desk/feeds/client.py` checks for the capability before dispatching. Typed failures map to UI states:
  - `FeedUnavailable("helper_offline")` → "Connect the Drishti helper to load NSE filings".
  - `"helper_outdated"` → "Update the helper".
  - `"source_error"` → "NSE didn't answer, try again".

### 3.3 Server client

```python
# apps/api/desk/feeds/client.py
async def fetch_dataset(user_id: str, name: str, params: dict) -> FeedResult:
    # 1. shared feed_cache hit within TTL → return (D2)
    # 2. route == "server": fetch directly (same URL template + headers, server-side cookie jar)
    # 3. route == "helper": capability check on the user's own relay connection, then
    #    get_relay_registry().dispatch(user_id, "__dataset__", {"dataset": name, "params": params}, timeout_s=60)
    # 4. parsers.<source>.parse_<dataset>(bytes) → normalized rows; write feed_cache (+ filings)
```

Dispatch already has single-flight and a short result cache per `(user, provider, body)`. The shared `feed_cache` sits on top of that. TTLs are set per dataset:

- Announcements: 5 minutes during market hours.
- PIT: 30 minutes.
- Bulk and block deals: once after 18:00 IST.
- Price bands, ASM, GSM and F&O ban: daily.

### 3.4 Dataset catalog: **every row is UNVERIFIED until the Phase 0 spike**

For each row the spike confirms the URL, whether cookies or Referer are needed, the response shape and the rate limit, and saves a fixture.

| Dataset | Probable source | Used by |
|---|---|---|
| `nse.announcements` | `www.nseindia.com/api/corporate-announcements?index=equities[&symbol=]` | news |
| `nse.board_meetings` | `www.nseindia.com/api/corporate-board-meetings?index=equities` | "results this week" warning in news, risk and Time Machine |
| `nse.insider_pit` | `www.nseindia.com/api/corporates-pit?index=equities&from_date=&to_date=` | insider tracker |
| `nse.sast` | `www.nseindia.com/api/corporate-sast-reg29?...` | insider tracker |
| `nse.bulk_deals`, `nse.block_deals` | `nsearchives.nseindia.com/content/equities/bulk.csv`, `block.csv` (or the large-deals API) | insider tracker |
| `nse.financial_results` | `www.nseindia.com/api/corporates-financial-results?index=equities&period=Quarterly&symbol=` and the XBRL file link on `nsearchives` | results analyst |
| `nse.price_bands` | `nsearchives.nseindia.com/content/equities/sec_list.csv` | risk (circuit headroom), Time Machine clamp |
| `nse.fo_ban` | `nsearchives.nseindia.com/content/fo/fo_secban.csv` | risk (warn) |
| `nse.asm`, `nse.gsm` | `www.nseindia.com/api/reportASM`, `reportGSM` | risk (block) |
| `nse.holidays` | `www.nseindia.com/api/holiday-master?type=trading` | **replaces the hand-maintained list in `scanner/calendar.py`** (keep the static list as a fallback) |
| `bse.announcements` | `api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?...` (Referer required) | news (BSE-only filers and duplicates) |

**Spike procedure:** for each dataset, **first try from the server**. Mark it `server` if it returns 200 three runs in a row, ≥10 minutes apart. Only otherwise try it through the helper and mark it `helper`.

**Spike exit criteria:** every dataset has a working route. If Akamai still blocks the non-browser client, the fallback is a helper option to run the warm-up through a headless browser profile. That's heavy, so we decide it only if we need it.

---

## 3A. Seams for the Kite Auto-trader feature

The Kite feature (`2026-10-29-kite-autotrader-design.md`) builds on the desk without changing it. Build these seams in Phases 0–3:

1. **`desk/bars_source.py`: `get_intraday_bars(user_id, symbol, interval) -> Bars`**, with `source` (`"delayed:yahoo"` now) and `data_as_of`. Every desk module (ORB, entry, alerts, sizing's fresh price, Time Machine's pool) reads bars **only** through this function. The Kite feature registers a per-user live provider that takes priority.
2. **Pure, reusable rule functions:** `orb.evaluate`, `entry.evaluate`, `exit.compute`, `sizing.size`, `risk.check(orders, profile, state)` and `charges.estimate` have no I/O and no request objects, so the auto-trader's engine calls them unchanged.
3. **`paper.py` fill model** (next-bar fill + slippage + charges) as a function of bars. The auto-trader's Paper/Shadow mode reuses it on live bars.
4. **Ticket schema** (`desk_tickets`: order + verdict + fingerprint) is broker-neutral. The Kite feature adds a "Send to Zerodha" executor on top of it.
5. **`desk_profiles`** holds the risk limits. The auto-trader's guardrails extend them; they don't duplicate them.

---

## 4. Shared market primitives (`desk/market/`)

- **`session.py`**:
  - NSE regular session 09:15–15:30 IST, so **75 five-minute bars**. Bar `k` starts at `09:15 + 5k`.
  - The pre-open (09:00–09:15) is excluded.
  - Reuse `scanner.calendar.is_trading_day` and `IST`.
  - Special sessions (Muhurat, budget-day weekend sessions) and short days are detected from the data: any session without exactly 75 bars is "not full" and is left out of pools. This is the same rule as upstream.
  - **Phase 0 checks** that yfinance `.NS` 5m timestamps align to 09:15. We also measure the **data delay**, which the scanner spec assumes is 15–20 minutes.
- **`ticks.py`**: tick-size rounding. A stop is rounded **away** from entry (down for a long), which ports upstream's `cents_away`. NSE uses ₹0.01 below ₹250 (since 10 Jun 2024) and price-band-based ticks above that (revised Apr 2025). Ship it as a table with an `as_of` date and verify it against the current NSE circular in Phase 0.
- **`charges.py`**: an estimate of round-trip intraday equity charges, set up as a **versioned table** (`effective_from`) and not as constants:

  | Charge | Rate |
  |---|---|
  | STT | 0.025% on the sell side |
  | Exchange transaction | ≈0.003% (verify) |
  | SEBI fee | ₹10/crore |
  | Stamp duty | 0.003% on the buy side |
  | GST | 18% on brokerage + exchange + SEBI |
  | Brokerage | from the user's profile (default `min(₹20, 0.03%)`) |

  There is also a delivery profile for swing mode. Sizing and Time Machine output report **R gross and R net of charges**.

---

## 5. Phases

Each phase ends with a working, tested slice, and each gets its own task-by-task plan in `docs/superpowers/plans/` (same format as the scanner plan) when we start it. Estimates are rough and assume one person working with Claude.

### Phase 0: Foundations and spike (≈2–3 d)

1. **Spike script** `scripts/desk_spike.py`, run **first, from the deployed server**. It tries each NSE/BSE dataset server-direct and records the route verdict. Datasets that fail are retried through a locally running helper. It saves **fixtures** to `tests/fixtures/desk/<dataset>/…`.
2. **Helper `__dataset__`** (§3), **only if the spike leaves any dataset on the `helper` route.** The catalog is limited to those datasets, with the version bump to 0.2.0 and the capability in `hello`.
   - **Tests:** unknown dataset rejected; param regex; host and path pinning; cookies never appear in a payload; Referer injected only for the BSE host; rate limiter; no redirect follow.
3. **Bar fixtures:** the spike script also records `RELIANCE.NS`, `TATAMOTORS.NS` and `SBIN.NS` 60d 5m charts as `.json.gz`, then notes the Yahoo delay and the 09:15 alignment.
4. **`desk/market/{session,ticks,charges}.py`** and the **`desk/bars_source.py`** seam (§3A), plus tests.
5. **`desk/store.py`** with the schema for every table in §2 (migrations: a `meta` key/value table like the scanner store's, with a `schema_version` key) and ownership helpers.
6. **`desk/feeds/client.py`** with per-dataset routing (server or helper), cache, gating and typed errors. Tests use a fake relay connection.
7. **Attribution:** add `apps/api/desk/THIRD_PARTY_NOTICES.md` with the upstream MIT text and the pinned commit.

**Exit criteria:** every dataset has a verified route (server or helper), or is dropped with the reason written down; fixtures are committed; the primitives are green.

### Phase 1: Time Machine (≈3–4 d)

**Port** `simulate()`, `history_replay()` and the verdict logic from `timemachine.py`, keeping the same draws and order so upstream's seed-19 numbers can be reproduced on the TSLA fixture as a **port-correctness test**.

**Intraday mode (India changes):**

- The pool is full NSE sessions (75 bars) from the ~59 before the plan date. Each bar becomes its O/H/L/C ratios to the previous close, and draws come from the same slot ±`window` (default 2).
- `until` defaults to the profile's **`flat_by` = 15:15**, before the typical broker MIS auto square-off (around 15:20, which varies by broker). The user can configure it.
- **Circuit clamp:** when `nse.price_bands` is cached, the simulated high, low and close are clamped to the stock's band around the previous close, and the result reports "% of replays that hit the circuit". If the band is unknown, the clamp is skipped and a note says so.
- Stops round by tick (§4). R is reported both gross and net of charges.
- It refuses, as upstream does, when: there are fewer than 20 full sessions, the entry bar is missing or not closed yet, or the plan is incoherent.
- **Seed is always shown.** Default seed 19 and 10,000 replays.

**Swing mode (new, D6):**

- The pool is ratios from the last ~500 daily bars.
- The horizon is N sessions (default 10, max 40).
- Entry, stop and target come from `LevelsResponse` (entry = the close on `analysis_date`).
- It reports a **gap-through-stop %** (the open is already below the stop), because daily gaps matter more than intraday ones.
- **History:** re-run the `levels.py` rule (structure stop + R target) on each of the past ~250 sessions using the same constants, imported read-only. Keep it vectorized or bounded so the endpoint returns in under 3 s.

**API:**

```
POST /api/desk/timemachine
  {symbol, mode: "intraday"|"swing", date, entry, stop, target,
   entry_time?, until?, horizon?, replays?=10000, seed?=19, window?=2}
  → TimeMachineResult {plan, mc:{p_target,p_stop,p_neither,p_green,median_close,
     p5_close,p95_close,avg_R,avg_R_net,bands[],sample_paths[≤160],hist},
     history:{sessions,triggered,target,stop,close,avg_R,trades[]},
     verdict, notes[], seed, data_as_of, pct_circuit?, pct_gap_through_stop?}
GET  /api/runs/{run_id}/timemachine?horizon=10   # swing mode from the run's levels
```

The work runs in a threadpool (`asyncio.to_thread`). Results are cached in `desk_tm_results` keyed by `(plan_hash, data_date, seed, replays)`. 10,000 × 75 pure-Python iterations is about 1 s, and the numpy path is optional.

**UI:**

- `LevelsPanel.tsx` gains a **"Replay odds"** card with the four odds, the verdict line, the history line and the seed, plus a fan chart and close histogram (D7).
- `/desk/plan/$id` shows the same component in intraday mode.
- The copy is ported from the upstream "Rules" section: never round a coin flip up into an edge, and say once that this is a simulation and not a prediction.

**Tests:**

- Determinism: same seed gives the same numbers.
- The upstream TSLA fixture reproduces 29.4 / 25.5 / 45.1%.
- NSE fixture: a 75-bar filter; a half-session excluded.
- Circuit clamp; tick rounding; gap-through-stop.
- Refusal messages.
- Ownership on `/runs/{id}/timemachine` follows `test_run_cache_ownership.py`.

### Phase 2: Intraday flow: ORB scan, entry timer, exit planner (≈3 d)

- **`orb.py`** ports `orb_candidate`/`orb_hit` onto the scanner store's 5m bars:
  - The opening range is the first `or_minutes` (15 by default) from 09:15.
  - A hit is the **first completed bar that closes above the OR high**.
  - The universe is D5. Default liquidity filters are price ≥ ₹50 and 20-day average traded value ≥ ₹10 crore; both are configurable.
  - The header shows "data as of HH:MM (delayed)".
  - Bars still forming are never used.
- **Daily setups** (`breakout_20d`, `bull_flag`, `reclaim_ma20`, `oversold`, `oversold_bounce`) become **prebuilt scanner JSONs** in `scanner/prebuilt/`, written in the existing AST. Where a setup can't be expressed in the AST (bull_flag's 10-day run + 5-day range), add the missing operand to the scanner schema instead of special-casing it.
- **`entry.py`** ports `evaluate()` for the `orb` and `level` setups and returns `waiting | triggered | expired`. The window ends at the profile's `no_entries_after` (default 14:45). The UI polls every 60 s while the market is open; there's no SSE in v1.
- **`exit.py`** ports `compute()`:
  - Stop rules: `or_mid` (the ORB default), `or_low`, `price:X`, `pct:X`, `atr:K` (ATR from today's 5m bars).
  - Targets: `R` or `price`.
  - `flat_by` is checked against the session close and the profile's square-off time.
  - Tick rounding applies.
  - It writes a `desk_plans` row.
- **API:** `GET /api/desk/orb?or_minutes=15`, `GET /api/desk/entry/{symbol}?setup=orb|level&level=`, `POST /api/desk/plans`, `GET/PUT/DELETE /api/desk/plans/{id}`.
- **UI:** `/desk` (today's ORB table with "Plan →" per row, and watchlist entry statuses) and `/desk/plan/$id` (plan editor → Time Machine). The scanner results table gets a "Plan" action beside the existing "Analyse".
- **Tests:** OR boundaries at 09:15/09:30; a bar that is still forming is excluded; each stop rule; tick rounding; plan ownership.

### Phase 3: Sizing, risk, ticket, paper ledger (≈4–5 d)

**`sizing.py`** ports `size.py`:

- `shares = floor(max_loss_inr / risk_per_share)`, capped by `max_position_pct` and the cash buffer.
- **No MIS leverage in v1.** It's broker-specific, and we flag it as a later option.
- Output: shares, position value, max loss at the stop (gross and net), estimated charges, and "charges = X% of your risk". A plan where charges exceed ~25% of risk gets a viability note.

**`risk.py`** ports `check.py`'s check table. Exits are never blocked (upstream's rule: getting out must always be possible).

| check | India version |
|---|---|
| format / asset | EQ series only. BE/BZ (trade-for-trade) are **blocked for intraday**. No F&O symbols. |
| short | sell > held → block (no short in v1) |
| blocked, size, position, cash, stop, loss, reward:risk (**net of charges**), daily loss, trades/day, duplicate, daily order limit | ported as-is, in ₹ |
| window | `no_entries_before` (default 09:30, after a 15m OR) / `no_entries_after` (14:45); trading day by the NSE calendar |
| square-off | `flat_by` must be before the profile's broker square-off time |
| circuit headroom | target beyond the upper band → block; a stock in a 2%/5% band → warn |
| surveillance | ASM/GSM listed → block; F&O ban → warn only (ban limits F&O positions, not cash intraday) |
| results week | board meeting for results within the plan's horizon → warn (from `nse.board_meetings`) |
| ~~pdt~~ | removed (US-only) |

Surveillance, circuit and results-week checks report **"skipped: data unavailable"** until the Phase 4 feeds are cached. They never silently pass.

**Tickets and paper trading:**

- **`ticket.py`** builds an order with a fingerprint (order + verdict), the same as upstream. The user's explicit **"Confirm"** is required, per ticket.
- Placing it:
  - **Paper:** `paper.py` fills at the next bar's open plus a configurable slippage, then tracks the stop and target bar by bar and closes at `flat_by`. It records charges.
  - **Live:** a "Place on Zerodha" **Kite Publisher basket** button (the user confirms on Zerodha's side), plus a copyable ticket for other brokers. Sending orders through the API is the Kite Auto-trader feature.
- A paper ledger and journal record P&L, R and charges. This is the feedback loop that makes the Time Machine honest over time ("your realized R vs the replayed R").

**API:** `GET/PUT /api/desk/profile`, `POST /api/desk/size`, `POST /api/desk/risk`, `POST /api/desk/tickets`, `POST /api/desk/tickets/{id}/confirm`, `GET /api/desk/paper`.

**Tests:** every check, both pass and block; exit-never-blocked; batch accumulation (upstream's "as if each passed order had filled"); charge math against a hand-worked example; paper fill and stop/target ordering (a bar touching both counts as the stop).

**Open item:** confirm Kite Publisher's current terms and whether an API key is required for basket links.

### Phase 4: NSE/BSE feeds: news, insider tracker, results analyst (≈6–8 d)

**`news.py`** (port of `news.py`):

- Sources: Yahoo `.NS` headlines (server-direct), `nse.announcements` + `bse.announcements` (helper), dedupe, and IST timestamps.
- **Indian keyword tags** replace the US list: results, dividend, bonus, split, buyback, order win/contract, fund raising/QIP/rights, pledge, SAST/open offer, credit rating, auditor resignation, management change, regulatory/SEBI order.
- It shows "today's move" from the scanner store and a "results this week" flag.

**`insider.py`** replaces the Pelosi tracker:

- Sources: SEBI PIT disclosures, bulk and block deals, and SAST Reg 29.
- Normalized rows: who (promoter / director / KMP / other), buy or sell, quantity, value in ₹, mode. Off-market, ESOP and gift rows are filtered out of "conviction" views.
- Views: latest, per symbol, and **cluster buys** (≥2 distinct insiders buying in the market within 10 trading days).
- Insider disclosures are public, and the copy says so. This mirrors upstream's "public, not inside information" fine print.

**`results.py`** replaces the EDGAR analyst:

- It reads `nse.financial_results` and the XBRL instance for each quarter, then outputs revenue from operations, total income, PBT, PAT, basic EPS and margins, with QoQ and YoY.
- Phase 0's fixtures tell us the Ind-AS XBRL element names. yfinance quarterly financials are the fallback.
- **Off-limits note:** this does **not** feed the TradingAgents analysts, because that would be a core edit. If we want it there later, it goes through a wrapper and a `FORK_PATCHES.md` entry.

**Rest of the phase:**

- **Risk lights up** surveillance, circuit and results-week checks from cached datasets. The Time Machine gets the circuit clamp.
- **UI:** a news drawer on `/desk/plan/$id` and the run page, `/desk/insiders`, and a results tab in the symbol view. For helper-routed datasets only, each has a "connect helper" or "update helper" empty state.
- **Tests:** parser tests on the Phase 0 fixtures; keyword tagging; dedupe; cluster detection; XBRL extraction; feed-gating states.

### Phase 5: Alerts (≈3 d)

- **`alerts.py`** ports the rule types: `above/target`, `below/stop`, `pct_move`, `time` (IST), and `opening_bell` (09:15 IST), the last replacing NYSE's. `add --from-plan` becomes "Add alerts from plan" (stop, target, flat_by).
- **Evaluator:** an async loop in the API process, like `ingest_loop`. It runs every 60 s while the market is open and reads the newest 5m close from the scanner store, plus a yfinance quote for symbols not in the universe. Each rule rings once, with `rearm` support. Results go to `desk_alert_events`.
- **Delivery:** in-app (bell and toast) plus Telegram (D4). Linking Telegram is a `/start <code>` deep link that binds a chat to the user. The bot token lives in server env.
- **Tests:** each rule type, ring-once, date scoping, and the time rule on a trading day.

### Phase 6: Optional, decided later

- **Kite Auto-trader** (live data, manual and automatic API orders, performance tracking): separate spec.
- Short-side support for both `levels.py` and the desk, and MIS leverage in sizing.

---

## 6. Frontend summary

- Routes: `/desk`, `/desk/plan/$id`, `/desk/insiders`, `/desk/alerts`, `/desk/settings` (profile). Add "Desk" to the top bar.
- Components in `components/desk/`: `ReplayOdds.tsx` (also used inside `LevelsPanel`), `OrbTable.tsx`, `PlanEditor.tsx`, `TicketCard.tsx`, `NewsDrawer.tsx`, `InsiderTable.tsx`, `HelperGate.tsx` (helper offline or outdated states).
- shadcn components to add: `chart` (D7), `sonner` (toasts), `alert`, and `progress`.
- Types go in `lib/desk-types.ts` and the API methods in `lib/api.ts`.

## 7. Testing approach

- **Offline only**, like upstream: every test runs on recorded fixtures (`tests/fixtures/desk/`). There's no network in CI.
- A **port-correctness** test per ported algorithm reproduces the upstream README numbers on upstream fixtures, which are copied in with attribution. A matching **NSE** test runs on our own fixtures.
- Helper security tests sit next to `tests/test_helper_fetcher.py`. Ownership and auth tests follow `tests/test_run_cache_ownership.py`.
- Run with `pytest tests/test_desk_*.py tests/test_helper_datasets.py -q`. Frontend uses vitest in `apps/web/`.

## 8. Risks

| Risk | Mitigation |
|---|---|
| NSE bot protection (Akamai) blocks the server, and maybe even the helper from a residential IP | Spike tries server first, then helper; cookie warm-up; low rate; D3 proxy option; headless-browser fallback decided only if needed |
| Yahoo `.NS` intraday is delayed or patchy | Show data-as-of everywhere; refuse when bars are missing; live Kite bars via the §3A seam (Kite feature) |
| Helper adoption: helper-routed feeds only work for users running it | Server-first means only blocked NSE/BSE datasets need it; everything else (Phases 1–3, 5) needs **no** helper |
| NSE endpoints change shape | Parsers live server-side (no helper release needed); fixture tests catch drift |
| Charges, tick sizes or holidays go stale | Versioned tables with `as_of`; `nse.holidays` dataset; a checklist item each January |
| Regulatory or licensing (D1, D8, D9) | No order placement, careful wording, legal review before launch |

## 9. Attribution

Each ported module starts with:

```python
# Ported from day-trader-skill (https://github.com/Jakeschincariol/day-trader-skill,
# commit 3d0f4ef), MIT License, Copyright (c) 2026 Jake Schincariol.
# Adapted for NSE (IST sessions, INR, Indian charges) by QuberTech.
```

The full MIT text goes in `apps/api/desk/THIRD_PARTY_NOTICES.md`.

## 10. Order of work

```
P0 foundations+spike ─┬─> P1 Time Machine (swing on runs, intraday on desk)
                      ├─> P2 ORB/entry/exit ──> P3 sizing/risk/ticket/paper ──> P5 alerts
                      └─> P4 NSE/BSE feeds (news, insider, results) ──> risk surveillance checks light up
```

P1 and P2 can run in parallel after P0. Everything is built against the `get_intraday_bars` seam (§3A), so the Kite feature can add live data later without changing desk code. **The Kite Auto-trader starts after P3.** P4 depends only on P0's dataset routes.


---

## Implementation status (2026-10-29)

**Built** (backend `apps/api/desk/` + `apps/api/routes/desk.py`; frontend `/desk/*`):

| Phase | What shipped |
|---|---|
| P0 | `market/session.py` (NSE 75×5m, plus an NYSE spec used only by the port tests), `market/ticks.py`, `market/charges.py` (versioned), `store.py` (`data/desk.sqlite`), `bars.py` (deep history cached in `history_cache`), `bars_source.py` (the §3A seam), `THIRD_PARTY_NOTICES.md`, `scripts/desk_spike.py` |
| P1 | `timemachine.py`: intraday mode **reproduces upstream's README numbers exactly** on the upstream TSLA fixture (29.4 / 25.5 / 45.1 %, history 33 → 13/15/5); NSE ticks, circuit clamp, gap-through-stop; swing mode with the `levels.py` rule history. `POST /api/desk/timemachine`. The run page's LevelsPanel has a "Replay this plan" (swing) section |
| P2 | `orb.py`, `entry.py`, `exit_plan.py`, plans CRUD; five daily setups added as prebuilt scanners (`scanner/prebuilt/daytrader_*.json`) |
| P3 | `sizing.py` (gross + net of charges), `risk.py` (the India check table), `ticket.py` (fingerprint, plain words, Kite Publisher basket), `paper.py` (bar-based fills + ledger stats) |
| P5 | `alerts.py` + a background `loop.py` (paper fills + alerts every 60 s while NSE is open); in-app delivery |
| P4 (part) | `datasets.py`: the pinned catalog with `route="server"` everywhere, cached fetch client with a negative cache, CSV parsers (F&O ban, price bands, bulk/block deals) and `nse_facts` feeding the risk check — **behind `DESK_NSE_FEEDS=1`, off by default** until the spike passes |

**Deviations from the sections above:**

- Plan pages are `/desk/plans` and `/desk/plans/$id` (not `/desk/plan/$id`).
- Charts are hand-written SVG (fan chart + histogram), so no new frontend dependency (D7 avoided).
- `GET /api/runs/{id}/timemachine` wasn't added: the LevelsPanel calls `POST /api/desk/timemachine` in swing mode with the run's levels, so `routes/runs.py` is untouched.
- Telegram delivery (D4) isn't built yet; alerts are in-app (Today tab + Alerts page).
- The helper `__dataset__` provider isn't built: by the server-first rule it waits on the spike. A dataset switched to `route="helper"` raises `helper_route_not_built` with a pointer here.
- The F&O universe falls back to NIFTY500 + the liquidity filter, because the scanner's universe CSV has no F&O flags yet. A `nse.fo_list` dataset (e.g. `fo_mktlots.csv`) would fix that.

**Not built yet:** P4's news / insider-tracker / results UI and the parsers for the JSON APIs (announcements, PIT, SAST, results, ASM/GSM, holidays). These need the spike's real fixtures first. Telegram alerts. The helper `__dataset__` provider (only if the spike says so).

**Next steps:**

1. Run `python scripts/desk_spike.py --yahoo RELIANCE,SBIN,TATAMOTORS` on the deployed server during market hours.
2. Commit the fixtures, flip any blocked dataset to `route="helper"`, and set `DESK_NSE_FEEDS=1` if the CSVs work.
3. Verify the tick table (`market/ticks.py`) and the exchange charge (`market/charges.py`) against the current NSE circulars, and the Kite Publisher basket endpoint in `TicketCard.tsx`.
