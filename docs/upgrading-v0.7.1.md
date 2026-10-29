# Product upgrade to TradingAgents v0.7.1

Rebuild the API environment when updating from v0.3.1. Hot reload updates Python
source but does not install the new pandas, LangChain, LangGraph or yfinance
requirements. Python 3.11 or newer is required; the product API images use 3.12.

For the development stack:

```sh
docker compose -f docker-compose.dev.yml build api
docker compose -f docker-compose.dev.yml up -d api
```

For a native environment, use `uv sync --locked --extra dev` and install the API
extras with `uv pip install -r apps/api/requirements.txt`. The lockfile is updated
for the merged package. Production builds install dependencies from the updated
package metadata; rebuild the API image through the normal deployment process.

## Existing results and interrupted runs

Old saved reports and completed runs remain readable. Result cache keys include
the core version and the product stream contract, so an older report is not
silently reused as the output of the new engine.

Checkpoint context records the same identity. A resume from an older or untagged
checkpoint returns HTTP 409 with instructions to start a new run, before claiming
it or scheduling model calls. No checkpoint files or historical results are
deleted. Checkpoints created with the current identity can still resume.

## Historical fundamentals

Past-dated runs intentionally withhold live Yahoo company profiles, undated
statements and insider transactions. SEC EDGAR provides dated filings for US
filers. It does not generally replace historical fundamentals for NSE/BSE
companies; those reports can therefore have less fundamental data. Source
messages in the reports explain what was withheld. Do not substitute today's
fundamentals into a historical run. A current-date analysis still uses the
configured live sources.

## Product adapter behavior

The API consumes `stream_run()` so nested analyst tool calls remain observable,
price-data failures stop before research, and each run retains its own data
configuration. Research and portfolio decisions come from `investment_plan` and
`final_trade_decision`, with legacy replay fields still accepted. All selected
analysts are marked active together and repeated reports are deduplicated.

Unreadable (`REVIEW`), missing and unknown ratings provide no actionable levels
or position size. CLI menu placeholders such as `custom` are excluded from the
web model list and rejected before queuing; actual provider model IDs remain
supported. Residential fetch credentials follow the run context into parallel
analyst tasks and are revoked when the run finishes.
