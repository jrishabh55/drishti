# Personal Trading Platform Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. User explicitly requested parallel Codex and Claude Code implementation; isolate overlapping file ownership in Orca worktrees, then integrate and review.

**Goal:** Finish the approved private strategy-to-paper-to-performance workflow and its guarded live extension.
**Architecture:** Extend existing product modules with selected-owner market-data adapters, shared deterministic strategy rules and immutable attribution. Independent workers use isolated branches; coordinator owns integration and research workflow.
**Tech Stack:** Python/FastAPI/SQLite, TanStack/React/TypeScript, existing Kite and Telegram clients.
**Spec:** docs/superpowers/specs/2026-10-30-personal-trading-platform-design.md

## Global Constraints
- apps/, tests/, deploy/, docs/ only; upstream tradingagents/ and cli/ read-only.
- Live orders remain disabled in production. No real orders, messages, paid analysis, key changes or account arming in tests/QA.
- Maintain per-user ownership and selected-owner data isolation; no private Kite candles in global scanner caches.
- Preserve legacy Default portfolio, tickets/fingerprints, paper trades and existing public delayed scans.
- No fabricated candles, fills, performance or exchange facts. Missing data is visible.
- shadcn controls and existing theme tokens; no package-lock.json.

## Review Focus
- Revoked or switched shared source during a blocking fetch must not return or cache owner data to an unauthorized account (Task1).
- Concurrent edits/kill/portfolio changes must not execute a stale strategy or move a bound ticket/trade (Task2).
- Historical simulation must exclude future bars and preserve entry/exit/cost rules; incomplete data cannot become a win (Task2).
- Analytics must not mix users, paper and live capital, or count stale/open marks as realized profits (Task3).
- Duplicate/replayed Telegram callbacks and worker restarts must not duplicate actions/messages or enable live orders (Task4).

### Task 1: Scoped Kite scanner data (Codex worker)
Files: apps/api/scanner/, apps/api/kite/live.py and new data modules, apps/api/desk/bars_source.py, ORB route block in apps/api/routes/desk.py, relevant scanner/connection frontend and tests.
Interface: preserve get_session_rows_universe's dict contract; add a source-aware universe result/helper and carry source/coverage into ORB. Expose a user-scoped scan runner that preserves general scanner response contracts with additive provenance fields. Other workers may call existing scanner route run_screener(sid,user_id) or the supplied new user-scoped runner; publish its exact signature in report.
- [x] Write and observe failing scoped-feed/coverage/revocation tests.
- [x] Implement bounded bootstrap,217/500-symbol coverage, source-aware general/ORB scans and UI progress/fallback labels.
- [x] Pass focused tests, publish interface notes and commit only owned files.
- [x] Independent task review and corrections.

### Task 2: Strategy execution, portfolio attribution and backtests (Claude Code worker)
Files: apps/api/kite/strategies.py/execution.py/store.py/routes/kite.py, new strategy evaluator/backtest modules/routes, apps/api/desk/paper.py and sizing/risk/ticket code as needed, apps/api/routes/desk.py strategy/ticket blocks, frontend strategy form/page/API and new backtest UI; tests.
Interface: implement the shared strategy/attribution/exit contracts in the spec. Strategy metadata must survive into paper/live trade records. Backtest API GET/POST /api/kite/strategy-backtests and GET /api/kite/strategy-backtests/{id}; owned strategy fields and immutable saved results. Add router object in new routes/strategy_backtests.py; coordinator wires app.py/navigation. Expose paper strategy creation via existing POST /kite/strategies with mode off by default. Publish all exact new API signatures.
- [x] Write and observe failing portfolio/version/freshness/exit/backtest/auto-gate tests.
- [x] Extend existing runtime and paper engine; add opt-in auto path through existing execution authority, never bypass gates.
- [x] Add deterministic bounded backtests and strategy controls/UI. No LLM-generated trades without explicit saved rule review.
- [x] Pass focused tests and commit owned files; independent task review and corrections.

### Task 3: Analytics and journal (Claude Code worker)
Files: apps/api/evaluation/performance.py and new analytics modules, apps/api/routes/evaluation.py, apps/web/src/routes/desk.performance.tsx and lib/evaluation-api.ts, new journal UI/routes, tests. Do not edit strategy implementation files; consume attribution fields defined in spec and preserve absent legacy metadata.
Interface: additive GET /api/evaluation/trading-performance?portfolio_id=all|default|<id>&strategy_id=<id>&mode=paper|live (paper and live remain separate); CSV GET /api/evaluation/trade-journal.csv with same filters; journal annotations on owned trades through a small additive route. Use real price/source freshness for open marks, explicit availability for benchmarks. Existing paper-performance endpoint unchanged.
- [x] Write and observe failing ownership/filtering/attribution/mark/CSV tests.
- [x] Implement strategy/version/portfolio results, realized vs open P&L, cross-paper overview, benchmark availability, annotations and export.
- [x] Extend performance UI without losing existing statistics/calibration; pass focused tests and commit owned files.
- [x] Independent task review and corrections.

### Task 4: Mobile supervision (next ready worker)
Files: apps/api/messaging/ and routes/messaging.py, apps/web/src/routes/desk.messages.tsx and its API types, tests. New helpers may read strategy records but must coordinate updates through existing strategy lock/routing.
Interface: verified commands/cards for status/performance/pause/resume, daily summary preferences and deduplicated delivery. Never arm live via Telegram. Retain exact-ticket live confirmation gates; use app review deep links if necessary. Coordinator wires any new loop hooks in app.py.
- [x] Write and observe failing sender/ownership/replay/action/daily-summary tests.
- [x] Implement commands, opt-in summaries, controls and visible channel status; retain paper approvals.
- [x] Pass focused tests and commit owned files; independent task review and corrections.

### Task 5: Research workflow and operational readiness (coordinator)
Files: new workflow router/component, analysis LevelsPanel, scanner results actions, Guide catalogs, shared app/navigation wiring and targeted tests.
Interface: create an OFF strategy draft from owned completed analysis levels or owned/prebuilt scanner selection; present editable review including portfolio, timeframe and rules, then explicit Paper enable. Readiness shows credentials/session/feed/worker/strategy blockers, not generic claims of readiness.
- [x] Write failing draft ownership/default-off/config-mapping tests.
- [x] Implement reviewable drafts, readiness/status connections and common wiring for worker routers.
- [x] Update Guide through catalog sync; run scoped tests and build.

### Task 6: Integrated review and release
- [x] Integrate worker commits, resolve contracts/conflicts, accept task reviews and fix material findings.
- [x] Independent whole-branch review with no unresolved blockers.
- [x] Full pytest, frontend tests, typecheck, Node build, catalog/whitespace checks.
- [ ] Preserve volume/encryption/media, verify live-orders0, push and deploy existing Dokploy app.
- [ ] Authenticated browser QA of scanner source/readiness, strategy draft/portfolio/backtests, analytics and Telegram controls without sending/trading.
- [ ] Record exact implemented outcomes and user-dependent Kite login/full-session trial; release every worker.

Review checkpoint: data/mobile/workflow and root follow-ups independently verified. Engine F1–F4 and analytics/backtest FWR-4 corrections independently verified through26174d1, with benchmark copybb84ecb reviewed. Final2650Python+103subtests,94web,typecheck,Nodebuild,catalog/whitespace checks pass. Controlled tests do not replace a connected full NSE session. Release verification follows below.
