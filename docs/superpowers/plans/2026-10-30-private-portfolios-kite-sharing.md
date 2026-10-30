# Private portfolios and Kite sharing implementation plan

> Execute the authorized implementation continuously, using test-first changes and
> one independent whole-change review before production release.

**Goal:** Separate paper experiments while allowing explicit internal data sharing.
**Spec:** `docs/superpowers/specs/2026-10-30-private-portfolios-kite-sharing-design.md`.
**Stack:** existing FastAPI/SQLite, TanStack React/shadcn, Clerk, Dokploy.

## Task 1 — paper portfolios (Claude implementer)

Own `apps/api/desk/{store,profile,paper,ticket,performance,schemas}.py`, a new
`desk/portfolios.py`, `apps/api/routes/desk.py`, portfolio-specific tests and
necessary messaging portfolio propagation. Frontend ownership: `lib/api.ts`,
`lib/desk-types.ts`, paper/performance/settings/plan desk routes, DeskNav and new
portfolio components/provider plus their root mounting. Do not edit Kite files,
`desk/bars_source.py`, guide catalog, deployment or upstream core.

- [ ] Pin legacy ledger preservation and multi-portfolio isolation with RED tests.
- [ ] Add idempotent migration, portfolio CRUD/selection, capital and filtered
  ledger/performance; scope sizing/risk/tickets and updates to the bound portfolio.
- [ ] Build account-isolated portfolio selection and creation/editing controls;
  bind ticket UI to its portfolio and reject stale UI responses after switching.
- [ ] Verify focused backend/frontend tests and report the final API contract.

## Task 2 — explicit Kite market-data sharing (coordinator)

Own `apps/api/kite/{store,live,sharing}.py`, `apps/api/routes/kite.py`,
`apps/web/src/lib/kite-api.ts`, connection page/new sharing component, related
tests and guide/docs updates. Never redirect `auth.client_for` globally.

- [ ] RED tests for grant ownership, direct-only selection, revocation, session
  expiry, credential isolation, upstream-read revocation and unchanged order gates.
- [ ] Add owner grants and recipient selection; resolve owners only in read-only
  quote/bar paths, with post-read revalidation. Owner controls streams and secrets.
- [ ] Add account-ID copying, grant/revoke, received-source selection and clear
  owner/shared freshness UI. Shared recipients cannot start/stop owner's stream.
- [ ] Verify focused sharing tests and client tests/typecheck.

## Task 3 — review and release

- [ ] Inspect combined diff and independent Claude review; resolve blocking findings.
- [ ] Run full pytest, frontend tests/typecheck/production Node build, catalog sync.
- [ ] Merge/push reviewed changes, deploy via existing Dokploy workflow; verify
  health, live-order gate 0, existing guide/media, new portfolio/sharing UI.
- [ ] Do not create real shares/portfolios or use paid Kite credentials during QA;
  demonstrate those mutations through isolated fake-provider tests.
- [ ] Settle/release all workers, record limitations and usage instructions.
