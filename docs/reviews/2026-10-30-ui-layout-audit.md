# UI layout audit — 30 October 2026

## Scope

Twelve issue groups (S01–S12) were corrected and accepted in the available rendered states. This was a repair of existing page spacing, control alignment and responsive containment. Base `c2f337d`; frontend-only changes, with no dependencies, backend/API changes, state transitions or trading behavior changes. Existing branding and theme tokens retained.

A dedicated screenshot worker inspected the signed-in production app through native Chrome in a separate QA window, scrolling through forms and opening existing read-only records and unsaved dialogs. Code work was split between Desk/Kite and analysis/scanner/evaluation/Guide owners. An independent reviewer inspected the integrated diff. No orders, messages, credential changes, paid model calls or test trading records were created.

## Corrections

| Area | Observed issue / source cause | Correction |
|---|---|---|
| Messaging | Labels touch input borders; adjacent Telegram buttons touch | Explicit 6px label/control gaps; wrapping action row with separate gutters |
| Plans / alerts / portfolios | Inline and stacked fields mixed; action buttons shorter than inputs | Consistent stacked field groups; aligned 32px input/action rows |
| Entry rules / settings / plan exits | Missing label/control separation | Explicit field-group spacing; full-width select triggers |
| Shared navigation | Fixed-height nonwrapping topbar clips links/actions on narrow screens and beside Guide | Content-driven height; wrapping navigation/actions; compact-width rows |
| Advanced analysis | Fixed label column and fixed grids push controls outside 390px viewport | Single-column compact settings, shrinkable field groups and adaptive grids; intact submit labels |
| Scanners | Condition headers and data use different columns; tight dialog fields and rigid toolbars | Matching desktop tracks, wrapping smaller controls, explicit dialog gaps |
| Evaluation | Labels have ineffective text-node spacing; native select padding crowds text at matched height | Grid field groups and explicit local padding for 32px selects |
| Reports / Levels | Fixed multi-panel and input grids exceed narrow width; Markdown cell text runs together | Responsive panels/tabs/fields; shared static and streaming table renderer with padded cells and a local horizontal scroll container |
| Live report actions | Fixed 40px header cannot contain all actions on small screens | Local auto-height, wrapping report header; action order preserved |
| Guide / provider setup | Constrained controls/copy/actions; mixed heights; mobile dialog inherits centering translation after CSS optimization | Shrinkable/wrapping content, matched control sizes, explicit zero-translation mobile overrides |
| Desk navigation / Start | Portfolio picker minimum width and nonwrapping links strain compact layout | Bounded/wrapping picker and link rows |

## Source review

Independent review of `c2f337d..e1bbe54` found no backend, API, handler, validation, label-association or keyboard-order regression. Two P2 layout findings were fixed: evaluation select padding (`baf2da9`) and narrow active-report actions (`e1bbe54`). Coordinator subsequently corrected both P3 mixed-height rows in `2e97912` (Gemini save-key and selected-symbol Analyse), with no handler changes. Follow-up report-table correction `4dbc91a` passed independent source review; the one-line Guide translation fix `f96e077` was reviewed by the coordinator against actual emitted CSS and class merging. Residual scanner heading spacing was widened consistently in `cfb1bb0`.

## Verification

- Final combined TypeScript and existing frontend suite: 24 files / 97 tests passed at `cfb1bb0`.
- Node production build passed.
- Actual emitted zero-translation/table CSS and Guide class merging verified. Diff whitespace check passed. No class-string snapshot tests added for reversible geometry fixes.
- Existing nonblocking route code-splitting/bundle-size and jsdom scrollTo warnings remain.

## Visual coverage and deployment

Baseline captures C01–C59 cover the accessible signed-in page inventory and scrolled forms/dialogs in dark and light themes. See [baseline evidence](2026-10-30-ui-baseline.md). Source-only routes are listed explicitly in that ledger.

Release `7e20692` was fast-forwarded to `main`, pushed and deployed using Dokploy deployment `SKl7ucMRhAUWTiGZWwSmD`. That deployment succeeded and both containers passed health checks. At that stage, served `styles-BQQkTqWo.css` returned HTTP 200 and its SHA256 matched the verified local build. Seven critical API source files matched main; order enablement remained `0` and encryption was configured.

The after-fix pass found two additional issues (report-table spacing S11 and mobile Guide placement S12) and residual first-column heading spacing in S10. Follow-up release `cfb1bb0` was pushed and deployed via `IFnYD6rNuf3vZevI0-CkU`; deployment succeeded, both containers are healthy, and production `styles-BqTuNSt1.css` matches the final local build SHA256 exactly. API source fingerprints and configuration checks remain unchanged.

The after-fix pass covers every available route, including scrolled form sections; see [deployed visual evidence](2026-10-30-ui-after.md). Captures A01–A08 accept analysis/Topbar containment at 390, 700 and 1440px; Desk field gaps and control alignment are accepted in the route ledger. A88/A90 accept scanner headings on both creation and workbench editors. A92/A94/A96 accept report-table spacing and demonstrate independent horizontal movement to the rightmost Role column. A97/A98 accept fresh mobile Guide opening at 390px and the desktop dock at 1440px. A99–A101 additionally demonstrate Paper table movement from left headers to Entry/Exit/Gross/Charges/Net/R net at 390px, with the surrounding page stationary.

Direct root navigation A36–A37 confirms `/` redirects to `/scanners`; the baseline autocomplete observation was not proof of the root route. The representative light-theme check passed and dark was restored. Final worker/browser cleanup is recorded in the deployed ledger.

Screenshots are emitted and inspected in the screenshot worker transcript. Capture IDs identify observations; no image-file gallery is claimed because the native API has no documented file-save method. Primary CSS viewports: 1440×900 desktop and 390×900 compact, with representative theme checks.

## Coverage limits

- Saved scanner edit and evaluation detail routes had no existing owned record IDs. Their source/components were reviewed; screenshots are not claimed.
- Active/interrupted runs and the pinned live toolbar, computed Time Machine/ticket/levels, saved backtest results, populated paper trades and performance charts were unavailable. Separate Gemini setup sizing was source-reviewed only. No records or paid runs were created to manufacture those states.
- Narrow and theme coverage is representative, not every possible route/state/width combination. Some captures use native full desktop dimensions with CSS width unmeasured.
- The final scanner scan did not finish within the bounded after-check; the baseline has a genuine 38-match result capture, and its table component was unchanged.
- Source inspection does not prove a rendered dynamic state. Initial wheel/scrollbar attempts that did not move the tables are retained honestly in the ledger; later native swipe captures resolve the report and Paper table access checks.

## Completion

The screenshot worker completed C01–C59 baseline and A01–A101 after captures/observations; all twelve issue groups are accepted within the documented available states. Duplicate/loading/failed-input captures are explicitly excluded from proof in the ledger. QA windows are closed, original user browser tabs preserved and dark restored. All owned audit worker terminals were released and their transcripts archived. The baseline dispatch has a historical `ownership_transferred` retention marker because its terminal was reused by the after task; the owning after task terminal is now closed, leaving no active/reclaimable audit worker.
