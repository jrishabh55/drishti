# Private paper portfolios and Kite data sharing

## User-visible behavior

- Named paper portfolios let one signed-in account run independent experiments with separate starting capital, cash, risk counts, tickets, trades and performance. The original ledger remains Default. Plans, watchlist and risk policy remain account-wide.
- Settings → Paper portfolios creates and manages named portfolios; the Trade Desk selector chooses the book for new tickets, ledger and performance. Existing tickets stay bound to their original book. Named portfolio starting capital is fixed after its first paper trade; a different experiment should use a new portfolio.
- Zerodha connection → Shared market-data connections displays the account ID, explicit outgoing grants, incoming grants and source selection. The owner grants an exact recipient ID; the recipient deliberately chooses that connection. Revocation removes the selection and denies subsequent shared reads, including checks after blocking quote/history requests.
- Quotes and supported session/history bar reads can use the selected owner's connection. The owner must log in daily and controls subscribed live symbols. The full-universe opening-range scan continues to use the public delayed source. No Kite market data is written to the global scanner cache.
- Keys, broker session details, positions, order history, live-order gates and stream management remain owner-scoped. Replacing or forgetting keys removes outgoing grants. Sharing requires distinct signed-in identities and is one hop only.

## Validation

Final combined validation: 2,479 Python tests passed, 103 subtests passed, 3 deselected, 42 existing model-catalog warnings (40.59s). Frontend: 81 tests across 19 files passed, TypeScript check passed, and the production Node bundle built successfully. Guide catalog export and git diff whitespace checks passed. Sharing tests include revocation during quote/history/status reads and a history fetch crossing daily session expiry. Portfolio tests include legacy migration, independent cash/risk/performance, ownership, bound tickets, background and messaging approval, and serialized creation. Independent Claude Opus 5.5 review found no release blockers. Review follow-ups were fixed and covered by regression tests: Today now follows and labels the selected portfolio, analysis explicitly names Default, recipients do not receive owner-private grant labels, and recipients may stop their own stream without stopping the selected owner’s stream. An end-to-end fake-broker test opens every broker gate and proves a named ticket is refused without sending or changing its ready state. The full tests, typecheck and production build passed again after these follow-ups. Production checks cover health, schema migration, disabled live ordering, the portfolio controls, sharing controls and existing Guide media without creating user portfolios or grants.

## Deployment and QA boundaries

Production live orders must remain disabled (`KITE_ORDERS_ENABLED=0`). QA does not create actual grants or paper portfolios, send bot messages, or call paid Kite endpoints. Provider-backed grants, revocation and portfolio mutations are exercised using isolated test stores and fake providers. Actual account recipients and any paid Kite connection setup remain the user's choices.

## Operations and known boundaries

Shared reads consume the owner’s Kite API quota; no separate per-recipient throttle is introduced for this internal deployment. The optional analysis paper-book snapshot uses Default only and explicitly says so in the UI. Named portfolios drive Today, sizing/tickets, ledger and performance.

Do not roll back to a pre-schema-2 application against a database containing named-portfolio trades: old readers ignore portfolio_id and would combine the books. Prefer a forward fix. A rollback across this boundary requires a matching database backup (which excludes later writes) or a reviewed compatibility migration that preserves portfolio filtering. A consistent pre-release desk backup was saved in persistent data/backups/desk-before-portfolios-20261030T074011Z.sqlite with mode 0600.
