# Private paper portfolios and Kite data sharing

User-authorized scope: private/internal Drishti, multiple test accounts, multiple
paper portfolios per account, and explicit Kite connection sharing between users.

## Behaviour

- Preserve each existing user's ledger as their default paper portfolio. Add named
  portfolios with independent starting capital, cash, trades, risk accounting and
  performance. Plans/watchlists and the user's risk-rule settings stay reusable;
  each portfolio supplies its own capital to paper sizing and risk checks.
- Select a paper portfolio in the desk. Bind sizing/tickets/confirmation to that
  portfolio. Changing selection never silently moves an existing ticket or trade.
  Background updates and Telegram approval must retain the bound portfolio.
- Preserve backwards compatibility: omitted portfolio ID means `default`.
  Legacy broker execution stays on its existing account path and gates. Named
  paper portfolios must never become a means of bypassing broker risk controls.
- A Kite connection owner may grant/revoke market-data access to explicitly named
  Drishti account IDs. The page displays the current account ID for copying.
  A recipient explicitly selects one directly granted connection, or their own.
  Grant lists are private to the owner/recipient; no user-directory enumeration.
- Sharing covers quotes and the existing live/historical bar provider. Owner
  credentials, login callbacks, broker orders/positions, trade history, arming,
  kill switches and stream management remain owner-only. No transitive sharing.
  The owner controls which symbols their stream subscribes to; recipients can
  inspect its freshness but cannot replace/stop that stream.
- Every shared read checks the grant and selection. Revocation immediately removes
  access, including checking again after blocking upstream reads. Provider expiry,
  disconnection or unavailable symbols fall back to honestly labelled delayed bars.
  Kite bars remain memory-only, outside global Yahoo/scanner caches.
- No existing account is automatically granted access or switched to a shared
  source. Server-side live ordering remains disabled (`KITE_ORDERS_ENABLED=0`).

## Implementation boundaries

Use the existing SQLite stores, encrypted owner credentials and Clerk identities.
Add tables/columns with non-destructive, idempotent migration and legacy defaults.
Keep real user IDs at the bar-provider/auth boundary; do not encode portfolio IDs
into user identity. New portfolio IDs and sharing grants must be ownership checked.
Use existing shadcn controls, themes and account-isolated client state. Guard stale
responses on account/portfolio/source changes. No paid provider calls, bot messages,
real trades or actual access grants during QA.

These permissions implement application access only; they do not expand the data
provider's contractual rights. Kite's mock-trading restriction discussed with the
user remains a provider-permission question, even for closed use.

## Acceptance

Tests must exercise legacy preservation, user and portfolio isolation, independent
cash/risk/performance, ticket binding, background/Telegram paths, grant/revoke and
selection, stale responses, no secret leakage or shared trading authority. Run full
Python and frontend checks, independent review, deploy, and inspect the live UI.
