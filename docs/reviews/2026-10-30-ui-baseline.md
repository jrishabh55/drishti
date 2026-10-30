# Live screenshot audit — 2026-10-30

Coordinator clarification: initial root navigation below was affected by omnibox autocomplete. A36–A37 in the after-deployment ledger explicitly navigate the full root URL and confirm its actual redirect to `/scanners`.

Target: https://drishiti.codeation.io, expected production code 3de688d. Browser: signed-in native Google Chrome, separate regular QA window created through File → New window; original YouTube/lichess/Drishti tabs preserved. Initial theme dark. No production records or settings changed, apart from temporary theme inspection when noted below.

Evidence method: documented `cua_repl` native App APIs, screenshots emitted and visually inspected in the worker transcript. The API documents no screenshot file-save method, so no image files are claimed. Capture IDs below refer to emitted screenshot observations; route/section/viewport are recorded here. Initial native screenshot raster is 3840×2100 including browser chrome; CSS viewport dimensions initially unavailable. Native resize drags did not change the QA window; responsive checks used Chrome device toolbar, confirmed at 1440×900 and 390×900 CSS pixels.

## Coverage

| Route | Visual captures / sections | States / result |
|---|---|---|
| `/` | Not independently verified | Initial omnibox autocomplete selected `/desk/messages`; this is not evidence of a root redirect |
| `/desk/messages` | C01 dark top: heading/nav, WhatsApp and Telegram fields; C02 dark middle/bottom: both preference lists, test/disconnect controls, disclaimer. Native raster 3840×2100 | Existing WhatsApp disconnected / Telegram verified; all controls inspected without editing, testing, connecting or disconnecting |

## Issues

- **S01 — `/desk/messages`, connection fields:** Evolution server URL, Evolution API key and Telegram bot token labels sit almost directly against the input border, with roughly 0–3px visual separation. Suspected source pattern: stacked label/control groups lacking explicit gap/margin. Root/shared primitive or desk forms owner should assess.
- **S02 — `/desk/messages`, Telegram actions:** Create Telegram connection link and Refresh Telegram connection buttons visually touch with no horizontal gap. Suspected source pattern: adjacent inline buttons without wrapping flex gap. No action invoked.

## Scope

This is a baseline visual audit, not a full behavior test or deployment acceptance. Existing dynamic IDs were followed from listing links; unavailable and uninspected states are enumerated at the end.

## Additional captures

- C03 `/analyse`, dark 1440×900 CSS device viewport: compact form and both recent run rows, all visible; no desktop spacing defect.
- C04 `/analyse`, dark 1440×900: advanced top/middle (depth, analysts, model stack). C05 advanced bottom (custom models, reasoning, output, submit footer). Desktop labels have clear gaps.
- C06 `/analyse`, dark 390×900: advanced top/middle. C07 advanced bottom. Both genuine emitted screenshots show severe horizontal overflow.

- **S03 — `/analyse`, narrow advanced form:** at 390×900, fixed two-column settings layout leaves right-hand controls outside viewport; depth, analysts, model fields and output help are severely clipped. Main ticker/date/start row similarly extends off right. Bottom Start analysis label wraps outside its fixed height. Suspected source pattern: nonresponsive grid columns/min-width and nowrap form rows.
- **S04 — shared Topbar, 390×900:** desktop links remain horizontal; Backtests is partially clipped and Models/theme/user controls are offscreen. Suspected source pattern: no compact navigation/wrapping at small breakpoint. Applies beyond analysis, to be checked on other pages.

Explicit viewport setup succeeded through GUI Chrome DevTools device toolbar; values confirmed by AX and screenshots. No console code, CDP, or source inspection used for UI.

| Additional route/state | Captures | Findings |
|---|---|---|
| `/runs/a2de927d-d98e-437f-b4cd-5b37f336b799` | C08 desktop decision, full summary; C09/C10 Levels loading then settled, 1440×900 dark | Reached by recent RELIANCE Open; Sell/Rules say skip correctly means no chart/levels available for this run; no numeric fields edited |
| Shared Guide on report | C11 This screen; C12 Tours library top; C13 library bottom; C14 Ask blank form; C15 first tour step | 1440×900 dark. No Ask submission or AI call. Tour opened and paused at step 1; 0 lessons completed. Guide compresses report topbar enough to wrap badges/buttons and clip Guide/theme/user area; related to S04 |
| `/desk/start` | C16 full top/readiness and workflow cards; C17 scrolled bottom | 1440×900 dark. Ready-state warnings readable; cards align well. No drafts/records created |
| `/desk` | C18 full page top-to-footer; C19 Entry rules disclosure open | 1440×900 dark. Existing RELIANCE expired watchlist row. Opening-range scan remained loading at capture; no refresh or settings edits invoked |
| `/desk/research` | C20 full initial search/news/deals/footer; C21 RELIANCE News & filings | 1440×900 dark. Existing watchlist RELIANCE chosen. Company exchange facts and news appeared without LLM calls; page longer after results load |
| `/desk/plans` | C22 full list/form/footer | 1440×900 dark. Existing draft RELIANCE plan discovered; no new plan submitted |

- **S05 — `/desk/plans`, New plan row:** Symbol label and Entry ₹ label sit directly against the adjacent input border with zero horizontal separation. Suspected source: inline label/input without gap; aligns with brief's label-control spacing concern.
- **S06 — `/desk`, expanded Entry rules:** minimum-volume label sits against numeric input border while surrounding stacked controls have larger spacing. Suspected missing field-group label gap.

| Additional route/state | Captures | Findings / limits |
|---|---|---|
| `/desk/plans/0dd96a0746b24264a69320bdd4d22df3` | C23 top entry/target/stop, Time Machine and size controls; C24 scrolled exit form/footer | 1440×900 dark. Exit labels have minimal gap above controls. No stored Time Machine/ticket rendered; Run the Time Machine and Make a ticket were not invoked because they produce stored replay/ticket state. No recompute/alerts/delete |
| `/desk/paper` | C25 desktop complete page; C26 narrow 390×900 complete page | Empty ledger, no trades. Narrow DeskNav wraps and portfolio row stays visible. Table visibly truncates into its own container; horizontal scroll gesture not yet verified. Global topbar overflows, making the body appear small with a large unused region to the right in responsive mobile rendering (related S04) |
| `/desk/performance` | C27 desktop top/middle; C28 scrolled journal/footer | 1440×900 dark, empty results. Cards and desktop filters align; chart/calibration unavailable without closed trades |
| `/desk/connection` | C29 desktop top 4 connection sections; C30/C31 scrolled shared-market-data form and footer | 1440×900 dark. No login, keys, stream, arm, sharing or refresh invoked. Incoming connections empty; credentials blank. Shared-account labels have small but visible vertical gap |
| `/desk/strategies` | C32 full listing/arm/ticket/live empty states; C33 new strategy top; C34/C35 middle and advanced exits/footer; C36 narrow 390×900 unsaved form | No strategies existed, so saved backtest form/results unavailable. New strategy opened and cancelled without field edits or Save. Desktop row controls center-align, but vary in starting x position because they are right-aligned. Narrow form wraps within card; global topbar overflow still distorts mobile layout |

- **S07 — owned plan, Adjust the exit plan:** Stop rule, Target rule, Target R and Flat-by labels sit almost against their control top borders. Same missing stacked-field gap pattern as S01/S06; no values edited.

| Additional route/state | Captures | Findings / limits |
|---|---|---|
| `/desk/alerts` | C37 complete desktop page incl. create row, empty Rules/Rang, Telegram fields/footer; C38 alert-type select opened then dismissed | 1440×900 dark. No alerts added, marked seen or Telegram edits; all content fits desktop viewport |
| `/desk/settings` | C39 top Money/Losses/Clock/Broker plan; C40 bottom ORB/Rules/Save/Paper portfolios/footer | 1440×900 dark. Every section inspected; no values changed or saved; only Default portfolio exists |

- **S08 — `/desk/alerts`, Add alert row:** Rings when label is stacked above its select, whereas Symbol and ₹ price labels are inline and touch the input left border. Mixed field directions produce visibly inconsistent alignment. Telegram Bot token/Chat ID labels also have minimal vertical gap.
- **S09 — `/desk/settings`, Rules and portfolio create:** Blocked symbols and New portfolio/Starting capital labels sit nearly against input top borders. Create button is visually shorter than its neighboring fields. Suspected field group gap and inconsistent compact-button height.

| Additional route/state | Captures | Findings / limits |
|---|---|---|
| `/scanners` | C41 initial page/footer; C42 prebuilt dropdown; C43 prebuilt scanning state; C44 raw advanced filter editor; C45 light editor; C46 light scrolled footer/loading | 1440×900 CSS dark/light. Loaded 20-day closing breakout, then Volume spike (progress 42 of 2126); scanning slow; results subsequently captured as C57. Manage my scanners count is 0, so owned edit route unavailable |
| `/scanners/new` | C47 native full desktop light, whole form through Preview/Create | Builder headings and controls misalign (S10). No generation, preview, creation or input edits. New QA tab opened while first scans; current native input subsequently interrupted by user activity |

- **S10 — `/scanners/new`, condition builder header:** TIMEFRAME and LEFT OPERAND visually touch, while OPERATOR/RIGHT OPERAND/STREAK headers distribute across the full width independently of the controls, which cluster toward the left. Suspected header-grid versus flex-row mismatch. Name/Description labels touch their input tops. Confirmed in light desktop screenshot.

## Native interruption checkpoint

After C47, coordinate input returned `noWindowsAvailable`, AX clicks/shortcuts had no visible effect, and a subsequent fresh AX observation reported user-changed Chrome with a user-created Meshy workspace tab beside the two QA Drishti tabs. GUI actions paused and coordinator notified; do not close this window wholesale while the user tab is present. This checkpoint was temporary; recovery and final restoration are recorded below. No screenshot of unrelated user app is used as audit evidence.


## Final captures after bounded window recovery

A fresh regular QA window recovered input. Coordinator confirmed the deployment was still building and the evaluation/models captures remained old runtime 3de688d baseline. C48–C55 therefore extend the baseline rather than constitute after-fix acceptance. C56–C59 are existing-document observations with no reload during rollout; their asset version was not independently checked, so do not use them to prove corrected deployment behavior.

| Route/state | Captures / viewport | Findings / limits |
|---|---|---|
| `/evaluation` | C48 light native full desktop, whole form and listing; C49 after Check cost | Form and No batches yet inspected. Check cost was clicked, but a returned quote was not visually verified; no batch launched, no model spend. No owned batch link exists for detail route |
| `/settings/models` | C50 light top; C51 middle; C52 bottom; C53 dark top; native full desktop raster 3840×2100 | All 16 provider cards inspected including Azure OpenAI/AWS admin-required states. Inputs blank; no credentials edited/tested/saved. Theme toggle restored dark in this QA window |
| `/scanners` simple editor | C54 native full dark New blank filter; C55 Save filter dialog | Blank filter opened, save dialog Name/Description and disabled Save inspected, dismissed without edits/save. Stacked dialog labels share the minimal gap pattern in S10 |
| `/scanners/new` | C56 native full light JSON tab | JSON editor state visible, no field edits/preview/create. Then theme restored to dark in that tab |
| `/scanners` Volume spike | C57 light 1440×900 completed result list, 38 matches, table and footer; additional scrolled table/footer observation | Genuine nonempty result state. Wide metric columns clipped within table container; horizontal scroll gesture and all 38 rows not exhaustively checked |
| `/scanners` strategy draft | C58 light 1440×900 dialog top/middle; C59 scrolled advanced exits/footer | Source/preview/portfolio/rules visible. Bottom Save strategy and Cancel visible after internal scroll. Cancelled without edits/save; no strategy created |

Evaluation labels and Save filter dialog labels have tight vertical separation, consistent with the existing shared field-gap findings. Model credential fields and Save key buttons have differing heights, a minor consistency concern rather than an observed obstruction. No additional blocking issue number is assigned.

## Final coverage limits

- Direct `/` root landing was not independently inspected; the initial navigation was affected by omnibox autocomplete.
- `/scanners/$id/edit` unavailable because Manage my scanners is 0. No scanner was created to obtain an ID.
- `/evaluation/$id` unavailable because there are no batches. No batch was launched.
- The owned plan had no stored Time Machine or ticket result. Computed chart, sizing/risk result and ticket states were not generated, avoiding persistent production records.
- The owned completed report is Sell / Rules say skip, so a plotted levels/chart state was unavailable. Other report tabs beyond Decision/Levels were not exhaustively inspected.
- No saved strategies or trades existed: strategy backtest results, nonempty paper positions/ledger, performance equity/calibration/journal details remain unverified.
- Research loaded RELIANCE News & filings, but the loaded news bottom and Results/Insiders & deals tab states were not captured.
- Narrow 390×900 coverage is representative only: analysis advanced form, paper page, unsaved strategy form. Every route at narrow width remains unverified. Other routes used explicit 1440×900 or native full desktop; native CSS dimensions were unavailable.
- Alert-type selection was opened/dismissed, but a changed Time alert draft was not verified. Credential/auth, live-order, arming, message test/send, sharing, persistent edits, paid Ask AI and model-run states were intentionally excluded.
- Screenshot bytes were emitted for visual inspection; the documented API exposes no file-save operation. This Markdown capture ledger and transcript are the evidence artifacts, not PNG files.

## Final browser and production state

The temporary recovery QA window was closed first. The original QA window later contained only its two Drishti tabs, with no Meshy tab, and was closed using Close window after the scanner draft was cancelled. Immediately beforehand dark theme was visually verified on `/scanners`, with AX help reading “Switch to light theme”. The currently exposed Chrome window is the original user window with Drishti, lichess.org and the selected YouTube chess video tab; these tabs were preserved. No further native input was sent.

No code changed, no orders/arming/messages/model calls or production records were created, and no credentials edited. The Guide tour was opened and paused at step 1 with zero lessons completed, as allowed. Ten numbered baseline visual issues S01–S10 were reported incrementally to the coordinator. Fix deployment and after-fix acceptance are outside this settled baseline dispatch.
