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

## Production verification

Released app commit `4f9d71bae0b56c743a3d87d99c1e41dce698d408` through Dokploy deployment `EvCliWXg2E3i3n4AVkkcW` (done). The server checkout matched that commit and the deployed sharing module matched the local SHA-256 `461d645192b8e57cf9eac4628ad0805b14c4d08d50098301c659d6b5b23037cd`. Both API and web containers were healthy.

Read-only production checks confirmed schema 2 and portfolio columns on trades/tickets. Existing paper/trade counts remained zero, with zero named portfolios, grants or source selections; no user data was created during QA. `KITE_ORDERS_ENABLED` remained `0`. Unauthenticated requests to both new settings APIs returned 401.

Signed-in Chrome checks confirmed the Default selector opens, Settings shows the portfolio creation controls, Paper ledger and Performance name Default and display their expected empty states, and the sharing page shows account-ID copying, grant controls and incoming-source selection. The Guide displays the new sharing FAQs. Manifest, intro and walkthrough URLs all returned HTTP 200.

The account had no saved Kite app or daily session. Actual paid Kite connectivity and an owner-to-recipient live feed were not exercised in production; those require the user's app keys/login and chosen recipient. Grant/revoke, quotes, live/history access and secret boundaries were verified using fake providers and isolated test stores. Both implementation/review workers were released; no worker resources remained reclaimable.
