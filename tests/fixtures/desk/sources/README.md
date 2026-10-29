# Recorded source responses (2026-10-29)

Real responses fetched on 2026-10-29 (IST afternoon) from a residential
Indian connection with the same client the app uses (`datasets._http_get`:
browser UA, NSE homepage cookie warm-up, BSE Referer+Origin, short UA for
Yahoo). Sanitised only by trimming long lists to a few rows; field names,
values and formats are untouched. XBRL instances are gzipped verbatim.
Public exchange disclosures — names in PIT / deal rows are as NSE publishes them.

| dataset | URL (pinned template in `apps/api/desk/datasets.py`) | observed |
| --- | --- | --- |
| nse.fo_ban | nsearchives …/content/fo/fo_secban.csv | 200 CSV |
| nse.price_bands | nsearchives …/content/equities/sec_list.csv | 200 CSV (trimmed) |
| nse.fo_lots | nsearchives …/content/fo/fo_mktlots.csv | 200 CSV, 218 stock underlyings |
| nse.bulk_deals / block_deals | nsearchives …/content/equities/bulk.csv, block.csv | 200 CSV, latest session |
| nse.deals_history | /api/historicalOR/bulk-block-short-deals?optionType=&symbol=&from=&to= | 200 JSON |
| nse.announcements | /api/corporate-announcements?index=equities&symbol=&from_date=&to_date= | 200 JSON. Without dates it returns the company's whole history (2.9 MB for RELIANCE) |
| nse.insider_pit | /api/corporates-pit?index=equities&symbol=&from_date=&to_date= | 200 JSON per symbol. **Market-wide (no symbol) returned `{"data": []}` on every probe** |
| nse.integrated_results | /api/integrated-filing-results?index=equities&symbol=&type=Integrated Filing- Financials | 200 JSON, quarters from Mar 2025 |
| nse.xbrl | nsearchives …/corporate/xbrl/<file>_WEB.xml | 200 XML (one quarter per instance, context `OneD`) |
| nse.event_calendar(_symbol) | /api/event-calendar?index=equities[&symbol=] | 200 JSON |
| nse.asm / nse.gsm | /api/reportASM, /api/reportGSM | 200 JSON |
| bse.scrip_search | api.bseindia.com …/PeerSmartSearch/w?Type=SS&text= | 200 (JSON string of HTML); **403 without Origin header** |
| bse.announcements | api.bseindia.com …/AnnSubCategoryGetData/w?… | 200 JSON; one later request **timed out** (BSE throttles bursts) |
| yahoo.rss | feeds.finance.yahoo.com/rss/2.0/headline?s=<SYM>.NS | 200 with UA `Mozilla/5.0`; **429 with a full Chrome UA**. Many .NS symbols (RELIANCE, HDFCBANK) return an empty feed |

Not used, and why:
- `/api/corporates-financial-results` (legacy results) — stops at Dec 2024; SEBI's integrated filing replaced it.
- `/api/results-comparision` — 200 but its five quarters end Dec 2024 (stale).
- `/api/equity-stockIndices?index=SECURITIES IN F&O` — 404.
- yfinance `Ticker.news` / `Search.news` for .NS — returned 0 items.

Not yet observed from the deployed server (datacenter IP). Run
`python scripts/desk_spike.py` there before setting `DESK_NSE_FEEDS=1`.

`yahoo_RELIANCE_5m_20261029.json.gz`: unmodified serialized OHLCV rows fetched
through yfinance on 2026-10-29. Reproduces the reported zero-full-sessions bug:
Yahoo omits closing-auction bars. 38 earlier sessions have every actual bar
through the plan's 15:15 exit; the 2026-10-15 interior gap remains excluded.
No missing bar is interpolated.
