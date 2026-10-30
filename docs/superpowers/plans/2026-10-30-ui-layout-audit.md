# UI Layout Audit and Correction Plan

> **For agentic workers:** Use supervised parallel agents for screenshots and disjoint page corrections. The coordinator owns shared layout and integration. User explicitly requested this audit and fixes; preserve the existing design.

**Goal:** Inspect every built page and repair inconsistent margins, label/control spacing, vertical alignment and responsive overflow.
**Architecture:** Fix shared layout causes centrally and page-specific field/toolbar wrappers locally. Keep all data, actions and trading behavior unchanged.
**Tech stack:** Existing TanStack/React, Tailwind v4, shadcn primitives and EasyStock theme tokens.
**Scope:** `apps/web/` and audit documentation only. Backend, upstream core and dependencies stay unchanged.

## Acceptance and constraints

- Capture actual pages and scrolled form sections; record route, state and viewport in a coverage matrix.
- Preserve branding, information hierarchy, both themes, accessible labels, keyboard order and all handlers/validation.
- Stacked labels need explicit separation from their fields. Inline labels and controls align centrally; adjacent field groups align their control rows deliberately.
- Keep intentional compact controls; match heights within a shared input/action row. Wrap action rows or stack fields at narrow widths without page-level horizontal clipping.
- Preserve user browser pages. No orders, arming, messages, paid AI calls, credential edits or production test experiments.
- Unavailable dynamic states are recorded honestly and inspected through existing components; do not invent records or claim screenshots of inaccessible pages.
- Visual before/after checks are the regression signal for reversible layout fixes. Run existing relevant tests, TypeScript and production build; add behavioral tests only if behavior actually changes.

## Tasks

- [ ] Screenshot agent: audit each route, accessible dialogs and representative dark/light/narrow layouts; publish incremental issue list and coverage.
- [x] Desk agent: fix confirmed issues in Desk routes/components and Kite forms; root owns DeskNav and Start here.
- [x] Product agent: fix confirmed issues in analysis, scanners, evaluation, model settings, reports and Guide.
- [x] Coordinator: fix shared navigation/layout causes and Start here; keep file ownership disjoint.
- [x] Integrate fixes, review for behavior preservation and run existing tests/typecheck/build.
- [ ] Verify every accessible page and affected form visually after integration; resolve remaining issues.
- [ ] Deploy reviewed corrections, verify source/health and final live layout, release all workers and record coverage/limitations.

## Ownership and evidence

Run `run_f8a4ad68c2ad`, base `c2f337d`. Working ledger, reports and screenshot observations are in `.superpowers/ui-audit/2026-10-30/` in the integration workspace. The screenshot worker has exclusive native browser ownership until handed back to the coordinator. Screenshots are emitted through the documented CUA API; files are claimed only if an API actually writes them.

Shared layout review focuses on the fixed-height, non-wrapping Topbar; the advanced form's fixed 220px label column; the Desk navigation/portfolio row; and link/action wrapping in Start here. Page workers own local label-field groups, mixed-height rows, table scroll containers and responsive panels. Confirm each visual symptom before declaring its fix complete.
