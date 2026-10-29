# Phone connections and paper approvals

Open **Trade Desk → Messages** (`/desk/messages`). This release connects directly to Evolution API for WhatsApp and Telegram Bot API for Telegram. Evo CRM and n8n are not dependencies.

## Administrator configuration

Configure these backend environment variables. The Dokploy Compose file maps them into the API container; development/fullstack Compose already reads the project `.env` through `env_file`.

```
EVOLUTION_API_URL=https://your-evolution-server.example
EVOLUTION_API_KEY=<server API key>
MESSAGING_PUBLIC_BASE_URL=https://your-public-drishti.example
# Optional additional trusted Evolution origins, comma separated:
EVOLUTION_ALLOWED_ORIGINS=
MESSAGING_LOOP=1
```

Preserve `WEBAPP_KEY_ENCRYPTION_SECRET` on the persistent volume. Never put these credentials in `VITE_*` variables. Only administrator-trusted Evolution origins can be selected in the platform; this prevents a caller from redirecting the shared server key to another host. Per-user API-key overrides are encrypted. The shared key is never shown in a status response.

The self-hosted service is `https://evolution-srv1.codeation.io`, pinned to `evoapicloud/evolution-api:v2.3.7`. On 30 October 2026 IST the latest stable release remains 2.3.7. The official `latest` tag points to a newer rebuild of that version; a Dokploy refresh pulled and started that image, but its public readiness check returned HTTP 502 during verification, so the source configuration was restored to the previous versioned image. This does not establish an image defect: the two-core, roughly 1 GB host was under high load, and the restored image also needed time for Prisma generation before listening. Allow for startup and verify public and authenticated readiness before any future promotion. No scheduled automatic updater is configured. Check the release notes and image version before each deployment: 2.4 release candidates introduce mandatory license activation. See the [official releases](https://github.com/evolution-foundation/evolution-api/releases).

Drishti creates its own generated instance per user. Server configuration and successful API authentication do not establish a phone session. Provisioning, QR linking and real callbacks must still be verified before claiming end-to-end provider compatibility.

Providers call `/api/messaging/hooks/<connection-id>` through the ordinary web service. Keep the API container private. This one callback prefix bypasses Clerk and requires a separate per-connection provider secret. The web proxy preserves provider authentication headers; setup APIs still require ordinary app authentication. Configure a public HTTPS app origin explicitly; callback URLs never derive from the incoming Host header.

## WhatsApp

1. Save the server configuration. Leave the API-key override blank to use the server key.
2. Generate a QR, then scan it in WhatsApp → Linked devices → Link a device. Alternatively enter your number with country code and request a pairing code. Codes expire; refresh after expiry. QR generation stays local to Drishti.
3. Refresh connection state to confirm the actual linked number. Choose a private recipient (your own number for self-chat, or another number you control).
4. Send a verification code and enter it in Drishti. This sends a real message only when you press the button.
5. Reply to the resulting LINK instruction as a **new message** in WhatsApp. This independently proves inbound replies work. If self-chat replies are not delivered by your provider, use another verified private number or the authenticated app for decisions. LID-only unknown identities and groups cannot approve.
6. Enable alerts and/or paper approvals. Use Test WhatsApp for an explicit test message.

WhatsApp proposals request **Approve paper trade**, **Reject** and **Status** native reply buttons through Evolution's `sendButtons` endpoint. The message also contains explicit `APPROVE <code>`, `REJECT <code>` and `STATUS <code>` replies and the plan link. These use the same expiring proposal and risk checks as button clicks. If the server rejects the button endpoint as unsupported (HTTP 404, 405 or 501), delivery falls back to the original plain text. Authentication errors, timeouts and server failures retain the normal retry behavior rather than immediately sending a second message whose first delivery is uncertain.

Evolution 2.3.7 exposes a native reply-button endpoint. Drishti handles its native-flow JSON reply IDs and older button-reply IDs; display text does not authorize an action. The [2.4 prerelease notes](https://github.com/evolution-foundation/evolution-api/releases/tag/2.4.0-rc1) document fixes for button rendering across WhatsApp clients. Delivery success does not certify that buttons display on a particular phone: verify rendering and clicks after pairing. If buttons do not display, use the reply codes when visible or the authenticated app; do not upgrade to a prerelease just to assume compatibility.

## Telegram

1. Create a bot in Telegram's BotFather and save its token in the password field.
2. Drishti validates the bot and installs its authenticated webhook. An existing webhook in another app is not replaced unless you explicitly choose replacement. Create a separate bot when you want the original integration to continue.
3. Create a connection link, open it in Telegram and Start the bot in a private chat. The link is one-time and expires after five minutes.
4. Confirm the displayed Telegram identity back in Drishti. There is no manual chat-ID lookup.
5. Enable alerts and/or paper approvals, then use Test Telegram if desired.

Existing legacy Telegram notifications remain available. Enabling guided Telegram alerts disables the user's legacy delivery toggle to prevent duplicates. A saved legacy chat alone never authorizes trade approvals.

## Approve a paper proposal

Create a plan and a ready ticket in Drishti, then choose **Send for paper approval**. This sends the exact ticket to verified channels with paper approvals enabled. It does not automatically turn scanner alerts into trades, and live strategy tickets are excluded.

Telegram has Approve paper trade, Reject, Status and View plan buttons. WhatsApp requests the first three as reply buttons, with the plan link and reply codes in the body; Evolution does not allow mixing reply and URL buttons in that message. Approvals expire after five minutes or the entry cutoff, whichever comes first. Current risk is checked again. The persistent ticket and shared per-user lock prevent double paper trades from repeated callbacks, retries or decisions in both channels.

Reject cancels a pending proposal; it never sells an existing position. Status updates describe paper acceptance, waiting for entry, fill, cancellation and exit including net P&L when available. Messaging delivery retries do not repeat trade execution. Notification failures remain visible in Messages, and the in-app ledger is authoritative.

**Real broker orders remain disabled.** These callbacks do not arm Zerodha, call its order API or bypass its separate live-trading controls.

Disconnect revokes pending actions locally before attempting remote logout/webhook cleanup. If remote cleanup fails, Drishti reports it; check the provider dashboard. Never remove unrelated Evolution instances or replace another user's webhook.

Remote cleanup failures remain visible after reloading; use **Retry cleanup** before creating another connection. Confirming a linked identity rotates its binding generation and turns delivery/approval toggles off until you opt in again. Earlier queued messages and proposals cannot follow a newly linked identity. A provider send already in flight may still finish; new sends and trade actions use the current binding.

## Release checks

Automated verification uses isolated databases and controlled provider responses; it creates no real provider accounts/messages or broker orders. Live acceptance needs the account owner's phone scan, Telegram Start, identity confirmation and explicit test-message actions. Record those separately from offline test results.

### Verified release — 30 October 2026 IST

- Product commit `1adcb1c`, deployed successfully as Dokploy `GmQmSPN22BxzT0nXTiSlx`; API and web healthy with zero failing health streaks.
- Full UTC regression suite: **2,373 Python tests and 103 subtests passed**, 3 existing deselections; **57 frontend tests**, TypeScript checking, production build and Compose validation passed.
- Independent review identified seven delivery/recovery/identity/QR issues; each was addressed with regression coverage. Callback acknowledgments use Telegram's documented webhook response operation, and unprovisioned WhatsApp refresh stays local until QR/pairing is requested.
- Live Chrome verification confirmed both setup cards, the Evolution URL/server-key readiness, saved WhatsApp configuration, QR/phone controls and disabled unverified approval toggles. Manual and polled pre-QR refresh returned HTTP 200. Unauthenticated setup requests returned 401 and unsigned callbacks 403. The account retained its five analysis credits.
- Running API configuration confirmed the Evolution URL/key, public callback origin, enabled messaging loop and existing encryption key; `KITE_ORDERS_ENABLED=0` remains explicit. No QR was provisioned, phone account linked, message sent, paid analysis run or real trade placed during verification. Actual phone linking and provider callbacks await the user's scan/Start and verification steps above.
- One local suite run crossed midnight: twelve parametrized cases in `tests/test_undated_tools_as_of.py` and `tests/test_yahoo_rate_limit.py` captured the previous date before execution, so the runtime historical-data guard withheld their mock calls. Both files passed all 61 tests after midnight in local time and UTC; the complete UTC suite then passed. No upstream core code or existing tests changed.
- Evolution image check: refresh `uWmYMXYdYG57iO3xs8ReL` pulled the official `latest` digest `sha256:966625532d9076a2381e973a271307d107e6f070450de3abeeea8bd18be07252`. Readiness was not established, so rollback `PnSC1XFhdpGTgRlQ3wVxc` restored `v2.3.7`, digest `sha256:1bd8afc4a6cf48822e6cf02469aeae7bd35a12a6b616eacd1291926307f4d339`. The rollback completed; the running container had zero restarts, public version and authenticated instance listing returned HTTP 200, and the empty pre-update instance inventory was preserved. PostgreSQL and Redis were not recreated. No phone was linked or message sent.

### WhatsApp buttons — 30 October 2026 IST

Native proposal buttons and native-flow reply parsing passed 75 focused messaging tests, including duplicate clicks across channels, forwarding, malformed replies, expiry, sender/instance/generation isolation, risk rejection and delivery fallback. Full verification: 2,405 Python tests and 103 subtests passed, 3 existing deselections, 42 existing warnings; 57 frontend tests, type checking and production build passed. Independent review found no Critical or Important blockers. Product commit `e8aba99` deployed successfully as Dokploy `9Mbi4rhjpbvUbiLu8b0bc`; API and web are healthy with zero failing health streaks. Public web returned 200, unauthenticated setup 401 and unsigned callback 403; the running API retained `KITE_ORDERS_ENABLED=0` and its Evolution configuration. Phone rendering and real callbacks still await owner pairing and an explicit test. No real messages or broker orders were sent during verification.

## Evolution 2.4 release candidate and dependency boundaries

As checked on 30 October 2026 IST, 2.4.0-rc2 remains a prerelease. RC1 adds button/list/carousel rendering fixes, a redesigned manager, message/history/contact-identity fixes and mandatory activation; RC2 adds GIF support, more WhatsApp identity/reply fixes and optional activation by a previously registered operator email. See [RC1](https://github.com/evolution-foundation/evolution-api/releases/tag/2.4.0-rc1) and [RC2](https://github.com/evolution-foundation/evolution-api/releases/tag/2.4.0-rc2).

The [licensing documentation](https://docs.evolutionfoundation.com.br/licensing) describes free community activation, without message/instance limits. This registers the self-hosted Evolution installation; it is separate from WhatsApp pairing and Meta's business service. The operator supplies contact information and the server sends aggregate technical telemetry. An unactivated 2.4 API returns `503 LICENSE_REQUIRED`. The [RC2 runtime source](https://github.com/evolution-foundation/evolution-api/blob/2.4.0-rc2/src/licensing/runtime.ts) activates a stored license locally and treats remote activation-notice/heartbeat failures as non-blocking; first activation still requires the licensing service. Do not describe ordinary licensing-server downtime as an automatic shutdown of already activated installations.

There is a published-terms discrepancy to resolve before upgrading a commercial deployment: the docs describe plain Apache 2.0 with no extra clauses, while the [RC2 LICENSE](https://github.com/evolution-foundation/evolution-api/blob/2.4.0-rc2/LICENSE) adds logo/copyright-preservation and visible usage-notification conditions, with commercial-license language for noncompliance. Do not assume the website overrides the tagged license or that removing activation is automatically an appropriate migration strategy.

Today only Drishti's WhatsApp transport and phone setup depend on Evolution. Telegram uses Telegram directly; analysis, plans, risk checks, paper money and the ledger remain in Drishti. Replacing Evolution would require another WhatsApp adapter, webhook parser and setup flow, but would not require moving the trading ledger. Keep Evolution replaceable, use verified stable updates, and evaluate Meta's official Cloud API directly for a longer-term production WhatsApp channel. No RC license registration, extra external service or migration is performed by this button release.
