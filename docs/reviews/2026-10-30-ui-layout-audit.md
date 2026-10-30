# UI layout audit — 30 October 2026

## Scope

Repair of existing page spacing, control alignment and responsive containment. Base `c2f337d`; frontend-only changes, with no dependencies, backend/API changes, state transitions or trading behavior changes. Existing branding and theme tokens retained.

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
| Reports / Levels | Fixed multi-panel and input grids exceed narrow width | Responsive panel stacking, scrollable report tabs and adaptive level fields |
| Live report actions | Fixed 40px header cannot contain all actions on small screens | Local auto-height, wrapping report header; action order preserved |
| Guide / provider setup | Constrained controls and long copy/actions; mixed control heights | Shrinkable content, wrapping header/actions, matched local control sizes |
| Desk navigation / Start | Portfolio picker minimum width and nonwrapping links strain compact layout | Bounded/wrapping picker and link rows |

## Source review

Independent review of `c2f337d..e1bbe54` found no backend, API, handler, validation, label-association or keyboard-order regression. Two P2 layout findings were fixed: evaluation select padding (`baf2da9`) and narrow active-report actions (`e1bbe54`). Coordinator subsequently corrected both P3 mixed-height rows in `2e97912` (Gemini save-key and selected-symbol Analyse), with no handler changes.

## Verification

- TypeScript, existing frontend suite: 24 files / 97 tests passed at `e1bbe54`.
- Node production build passed.
- Final two local height corrections: TypeScript, symbol-search tests (6/6) and Node production build passed.
- Diff whitespace check passed. No class-string snapshot tests added for reversible geometry fixes.
- Existing nonblocking route code-splitting/bundle-size and jsdom scrollTo warnings remain.

## Visual coverage and deployment

Baseline capture inventory, after-fix visual acceptance and deployment evidence are being completed. The audit is not yet accepted as visually complete.

Screenshots are emitted and inspected in the screenshot worker transcript. Capture IDs identify observations; no image-file gallery is claimed because the native API has no documented file-save method. Primary CSS viewports: 1440×900 desktop and 390×900 compact, with representative theme checks.

Unavailable production states will be recorded explicitly. Source inspection of a component is not evidence of a rendered dynamic state.
