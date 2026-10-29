# Complete source integrations for the Indian-market product

> Execution: coordinator implementation plus supervised Claude workers, as requested by the user. Existing Trade Desk and Kite designs supply the feature requirements; this plan records the approved audit follow-through.

## Outcome
Make the audited missing features usable in the existing FastAPI/TanStack application. Preserve existing paper accounts, analysis history, per-user ownership, credit accounting and upstream mergeability. Extend apps/, tests/ and deployment wiring; upstream tradingagents/ and cli/ remain read-only.

## Approach
Use existing core public APIs through product adapters, rather than duplicating their algorithms. Implement independent subsystems in isolated branches and integrate into one tested release. No replacement application. Real-data failures remain explicit; never fabricate prices, filings, fills or backtest scores. Credentials are configured by the user through encrypted settings; missing external credentials must produce an actionable connection state.

## Contracts and work ownership
1. Evaluation worker: finish web-run memory recording, per-user paper-portfolio context and cache fingerprinting, bounded user-confirmed backtest batches using the existing analysis/credits pipeline, results scoring and paper performance/calibration UI, HTML report download. Own jobs/runner.py, integrations/graph_factory.py, schemas.py, new evaluation modules/routes/frontend screens/tests. Do not edit common app.py, Topbar, DeskNav or web lib/api.ts; supply wiring instructions. Root merges shared changes.
2. Indian research worker: complete symbol/watchlist news, NSE/BSE announcements, financial-results comparisons, PIT disclosures and bulk/block tracker; verify source shapes with fixtures, integrate available surveillance/F&O feeds and truthful availability. Own desk research modules/datasets/feeds and new research router/screen/tests. No fake output or arbitrary-URL proxy. Supply common wiring instructions.
3. Kite worker: encrypted per-user Kite connection, daily auth/state validation, quotes/history/live 1m/5m bars, reconnect/disconnect and connection screen; then protected confirmed execution, strategy paper/confirm modes, reconciliation/guardrails and performance. Real orders never run in tests or deployment checks. Supply wiring instructions; own kite package/router and new connection/strategy screens/tests.
4. Coordinator: provider/model-tier selection and safe credential configuration, external alert delivery, expose remaining entry/exit controls; shared app/navigation/API wiring; review, conflict resolution, meaningful regression and integration tests, production build and live deployment verification.

## Acceptance
Each subsystem has behavioral tests including user isolation and unavailable-source/credential states. Existing 2088 Python and 32 frontend tests remain green (baseline Oct29). Frontend typecheck and WEB_TARGET=node build pass. Claude performs an independent final review; fix material findings. Deploy only reviewed and passing code. Verify live screens, data labels and account preservation. No real orders, broker activation or paid AI runs during verification. Document any external credential/setup dependency precisely.

## Work sequence
For each independently testable task: write regression/contract tests, verify failure, implement adapter/UI, run focused tests, commit. Start the three independent workers before waiting. Coordinator handles shared features while workers progress. Integrate completed commits, run broad suite and production build, ask Claude to review complete branch, resolve findings, merge/push/deploy and verify.

## Integration notes

- Model connections live at `/settings/models`. Personal provider keys are encrypted per user and never returned. Extra providers require the user's key; Azure/Bedrock need explicit administrator setup. Quick/deep model tiers may use separate providers. No user key is put in a run config or global SDK environment.
- `/desk/research` provides symbol/watchlist news, NSE/BSE announcements, quarterly comparisons, PIT disclosures, bulk/block trades and exchange facts. Each source reports availability, age and stale-cache status. HTTP-200 block pages are rejected before caching. Stale research copies cannot clear today's risk checks. F&O membership refreshes at startup and daily, retaining a disclosed bundled fallback.
- `/evaluation` runs bounded historical batches through normal validation and billing, with an explicit maximum-credit quote. Scores wait for real closes and compare with the instrument's regional benchmark. Evaluation memory is isolated from ordinary analysis. Paper-book context is optional and frozen at submission. Reports can be exported as standalone HTML.
- `/desk/performance` shows paper outcomes, equity/drawdown and available replay comparisons. Replay matching includes price levels, direction, interval, entry time and exit horizon.
- One-minute and five-minute entry rules, volume confirmation, custom levels and fixed-price targets are exposed in the Desk. Yahoo's one-minute history is short: real fetched bars accumulate, and deeper Kite history is available after connection. The 20-session replay minimum remains intact.
- The reported RELIANCE zero-session error was reproduced with 2,927 actual Yahoo five-minute bars. Replay now requires a complete window from the open through the requested exit, without demanding unused closing-auction bars. The recorded fixture yields 38 complete historical windows; an interior gap is still excluded. No prices are interpolated.
- Telegram delivery is opt-in, encrypted and deduplicated. Failed recent sends retry without a page open; notifications older than 15 minutes stay in the app.
- `/desk/connection` and `/desk/strategies` support private Kite market data and off/paper/confirm strategy modes. Live execution is cash-equity MIS buys with stop/target monitoring, typed confirmation and daily arming; it stays disabled by default. It requires both `KITE_ORDERS_ENABLED=1` and an explicit `AUTOTRADE_USER_IDS=<app-user-id>:<Zerodha-client-id>` binding. No broker credentials are bundled. CAS timing and actual broker instrument ticks are checked before submission. Kite MIS has no atomic broker OCO: software monitoring cannot promise that concurrent stop/target fills never occur.

## Operations and external setup

The API runs as one process/replica; process-local execution locks are not a distributed trading lock. Persistent databases and encrypted credentials use the existing named volume. Preserve the Dokploy `WEBAPP_KEY_ENCRYPTION_SECRET` when restoring or moving that volume. Live orders must remain off during deployment validation.

Before enabling `DESK_NSE_FEEDS`, verify the exchange datasets from the deployed host. Research screens also expose the server's actual source availability. A blocked source must not be described as an empty feed or a clean risk check. Never test a release by placing a real order, spending analysis credits, or sending an external Telegram message.
