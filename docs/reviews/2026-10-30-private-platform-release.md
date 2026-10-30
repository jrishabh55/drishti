# Private platform release — 2026-10-30

Implementation and final scanner UI correction reviewed through `3de688d`; deployed and browser-verified on 2026-10-30.

## Implemented

- Selected-owner Kite feeds for full F&O/NIFTY500 scanner universes, bounded private history bootstrap, completed-candle evaluation, explicit source/coverage and delayed fallback.
- Versioned strategies bound to owned paper portfolios; price/ORB rules, volume filters, partial exits, breakeven and trailing paper policies. Auto live is opt-in per version and retains every broker gate; this deployment keeps live sending disabled.
- Immutable historical strategy tests, cross-symbol cash reservation and risk checks, date-cutoff technical scanner evaluation on a frozen universe, train/test comparisons and visible limitations.
- Strategy/version/portfolio analytics, fresh remaining-position marks, chronological realized exit legs and drawdown, journal notes/tags/CSV; Default-only analysis snapshots. Paper and live remain separate.
- Telegram supervision, version-bound two-step pause/resume, deduplicated opt-in daily summaries and app-review live links.
- Reviewed research/scanner drafts saved Off, operational readiness, Start here workflow and synchronized Guide content.

## Review and verification

Independent Codex reviews covered data, analytics, messaging, workflow and engine scopes. Claude Code implemented strategy/accounting work and corrections. Original data/bootstrap/notification/workflow findings were corrected and rereviewed. Subsequent stale concurrent quota, future-entry-input, impossible scale-out, symbol-override and drawdown chronology findings were reproduced, fixed and independently checked. The final reviewer found no remaining reportable defect in the corrected scopes.

Final integrated verification: **2,650 Python tests plus 103 subtests passed** (3 deselected); **97 frontend tests passed**; TypeScript, Node production build, Guide catalog sync and whitespace checks passed. Additional reviewer probes exercised concurrent Paper/Confirm/Auto scans against FakeKite and verified actual HTTP accounting, portfolio isolation and the corrected ₹60.62 drawdown reproduction. No real order, outgoing provider test message or paid model call was made.

Online SQLite backup and quick integrity checks completed before release at `/home/appuser/.tradingagents/backups/platform-before-20261030T130327Z`. Data/encryption/media volumes remain in place. The pre-release server reported `KITE_ORDERS_ENABLED=0` and credential encryption configured.

## Operational limits and next validation

Use the existing single API process: private stream hubs, request budgets and strategy locks are process-local. A crash after a reserved signal deliberately consumes that day's slot rather than risking a repeated order. Restart the stream after a server restart and complete Kite's daily login. Universe history takes several minutes; inspect coverage.

Advanced exits remain paper/backtest-only; live brackets use fixed stop/target. Historical scanner replay supports daily technical rules over a frozen universe, with survivorship and unavailable historical exchange facts disclosed. Charges and bar-based fills remain estimates. Legacy closed-only charts are labelled; new analytics and backtests preserve booked exit-leg chronology.

A real connected Kite trial across a full NSE session remains required. That must verify provider coverage and the actual paper signal→fill→exit→performance workflow. Automated tests and an after-hours browser check cannot substitute for that trial. Live order enabling/arming is a separate deliberate rollout.

See [operation guide](../personal-trading-workflow.md) and [market-data details](../scoped-kite-market-data.md).

## Production verification

Final source **`3de688d`** is on `origin/main`. Dokploy deployment **`ibypmY-rVQtBCQw_lwRjI`** completed successfully; API and web containers are healthy. Seven critical backend source hashes match the verified checkout, all four new workflow/backtest/performance routes are present, credential encryption remains configured, and **`KITE_ORDERS_ENABLED=0`**. The data and media mount is preserved.

Authenticated browser QA verified Start here and its readiness blockers, RELIANCE symbol search, portfolio/rule controls in an Off-only draft, strategy/broker gates, performance filters/journal/CSV link and honest empty results, existing verified Telegram pairing and opt-in switches, and the Kite universe controls. No Kite app/session/feed or enabled Paper strategy is configured for the checked account yet.

A real prebuilt 20-day closing-breakout scan returned **42 liquid matches** over the public snapshot, with explicit **0 Kite / 2,126 delayed** coverage. Browser QA caught a whole-scanner draft action hidden by a pretty-versus-canonical JSON comparison; `3de688d` fixes it using the filter editor’s canonical definition check. Three regression tests observed RED then GREEN, all 97 web tests/typecheck/build pass, and an independent reviewer approved the fix with 12 additional identity/normalization probes. On the deployed correction, the **Draft scanner strategy** button is visible and opens the dynamic saved-scanner preview with its current names and Off-only save. Both tested previews were cancelled: no strategy, trade, portfolio or backtest experiment was created. The temporary QA window was closed and the original browser page preserved.

No real broker order, account arming, paid analysis or outbound messaging test was performed. Saved backtest execution and nonempty journal/P&L behavior were verified with controlled tests, rather than adding immutable test runs or fake positions to the user’s production account. The connected full-market-session trial remains the next external validation. All supervised workers settled and were released; no active or reclaimable worker remains.
