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
