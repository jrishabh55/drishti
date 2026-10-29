# WhatsApp and Telegram connections and paper-trade controls

Status: proposed design for user approval, 29 October 2026.

## Intent and agreed direction

Operate the existing Drishti Trade Desk from a connected phone: receive an exact proposed trade, accept or reject it, and receive subsequent updates. Set up the messaging connections inside Drishti. The user has selected direct integrations: their self-hosted Evolution API for WhatsApp and Telegram's Bot API for Telegram. Evo CRM and n8n were considered; neither is required for this feature.

The latest request adds Evolution server configuration through environment variables and the platform, WhatsApp mobile-number connection including a QR scanner, and guided Telegram connection. Here, “scanner” means the QR code scanned by WhatsApp Linked devices. Drishti does not need camera access to scan the phone.

Read-only deployment discovery found the user's existing server at `https://evolution-srv1.codeation.io`, with the deployed image `evoapicloud/evolution-api:v2.3.7`; its public health/version response confirms 2.3.7. Do not upgrade or repurpose this service or its existing instances. Integrate with this version and use Drishti-owned instances only.

## Scope and defaults proposed by the implementer

- Add a Messages page under the Trade Desk with WhatsApp and Telegram connection cards. Link to it from Alerts and settings; preserve existing in-app alerts and users' saved Telegram delivery settings.
- Provide connection, verification, reconnect, disconnect, opt-in alert delivery and an explicit test-message action for each channel.
- Provide Approve paper trade, Reject, View plan and Status interactions. Paper approval uses the existing exact-ticket confirmation and risk checks. A message cannot create arbitrary orders.
- Keep real broker orders disabled. Messaging does not arm Zerodha or bypass typed live-order confirmation. Paper-first is an implementer default because the user has not yet selected the live-approval scope.
- Never change upstream tradingagents/ or cli/. Use the existing API, TanStack frontend, encryption key, per-user ownership and persistent volume.

## Approaches

1. Direct provider adapters in Drishti (selected): reuse the current FastAPI backend for identity, ticket state, risk and delivery; add WhatsApp through Evolution and inbound Telegram callbacks. Fewer services and one authoritative trade ledger.
2. n8n between messaging and Drishti: useful later for summaries, exports and other automation; adds another workflow runtime to the approval path.
3. Evo CRM as a messaging hub: useful for a shared support inbox and contact management; its full CRM stack is unnecessary for this personal trade-control flow.

## Configuration and secrets

Environment configuration: EVOLUTION_API_URL, EVOLUTION_API_KEY, MESSAGING_PUBLIC_BASE_URL and optional EVOLUTION_ALLOWED_ORIGINS. MESSAGING_PUBLIC_BASE_URL is a validated HTTPS Drishti origin, not a request Host header. These variables are optional; an unconfigured deployment must boot and show an actionable setup state. Map them through the relevant Compose files and document their use.

The platform displays the configured Evolution URL and offers a per-user connection configuration with an optional server URL/API-key override. The server URL must match an administrator-trusted configured origin (default EVOLUTION_API_URL, plus EVOLUTION_ALLOWED_ORIGINS); untrusted targets are rejected before any request. This allows a trusted private self-hosted server without exposing an arbitrary internal-network proxy. Disallow embedded URL credentials, query/fragment tricks, redirects to other origins and caller-supplied request paths. Explain an unapproved origin in the UI rather than silently ignoring the field.

Server credentials remain backend-only; per-user overrides and Telegram bot tokens use the existing Fernet storage. Status responses expose configured/source/connection state, never credentials, raw upstream payloads or other users' instances. Do not log bot-token URLs or Evolution response bodies containing keys. Encryption setup is required before persisting a connection.

The server API key may provision only a server-generated, app-owned instance name for that user. Persist ownership before calling Evolution; retries reuse the same name. Never let a user enumerate or select arbitrary instances using the shared administrator credential. Changing the endpoint or credentials requires disconnecting the previous connection first; it cannot repoint an existing owned instance silently.

## WhatsApp user flow

1. Open Messages → WhatsApp. See server URL, configuration source and setup readiness. Save any permitted override; validate the connection using a bounded request without exposing a fleet list.
2. Choose QR or phone-number pairing. For phone pairing, enter the number with country code; validate and normalize it. Explain the WhatsApp Linked devices steps.
3. Create/reuse the user's owned WHATSAPP-BAILEYS instance and configure a narrowly scoped authenticated callback. Show the returned QR image, or generate an image locally from the returned QR string. Never use a public QR-generation service. Show pairing code only for that user's current connection and expire it appropriately.
4. Poll connection state while pairing; stop polling on navigation, success or expiry. Refresh QR only on explicit retry or expiry, with rate limits. Show connected number and state as returned by Evolution; an HTTP 200 alone is not proof of connection.
5. Choose and verify a private notification/approval recipient. Default to the linked number, with a self-chat explanation. A different recipient must complete a one-time private challenge before activation. No groups, broadcasts or channel-wide approvals.
6. Enable alerts and paper approvals independently. Test message is explicit and sends only to the verified recipient. Disconnect disables all pending actions locally immediately, logs out only the owned instance and reports any remote cleanup failure. Do not delete a shared server or unrelated instance.

Evolution adapters target the v2 create/connect/state/logout/webhook/text-message interfaces and tolerate documented nested response variants. Normalize known shapes and fail explicitly on unknown shapes. Surface unsupported phone pairing or interactive buttons honestly. Text commands containing an expiring action code are the fallback for WhatsApp buttons; successful message delivery alone does not certify native button support.

Self-chat inbound events need explicit treatment: accept only the bound private recipient and owned instance, and ignore provider message IDs recorded in our outgoing-message ledger. Do not globally treat fromMe as proof of user approval or discard all fromMe events, since self-chat replies may use it. If the installed provider cannot deliver distinguishable self-chat replies, show approvals unavailable for that configuration and offer a verified separate recipient or the authenticated app link. Do not pretend self-chat approval works without a real capability check.

## Telegram user flow

1. Explain that Telegram bots are created in BotFather; Drishti cannot create a bot or link a personal Telegram account with a WhatsApp-style QR.
2. Paste the bot token into a password field. Validate with getMe, store encrypted and display bot username. A bot can belong to only one app connection; reject reuse across users so one user cannot replace another's webhook.
3. Show Connect Telegram as a private t.me start link with a random, one-time, five-minute pairing token. The user starts the bot; the authenticated webhook records the private Telegram user/chat. The app displays the proposed identity and the logged-in user confirms the link. Manual chat-ID entry is no longer required.
4. Configure a provider webhook with a separate random secret and a public URL through Drishti's web proxy. Use message and callback_query updates only. Do not overwrite a pre-existing foreign webhook without an explicit warning/action in setup.
5. Offer independent alert/paper-approval toggles, test message, connection refresh and disconnect. Remove/revoke only the webhook installed by this connection; invalidate pairing and action tokens locally immediately.

Legacy configured Telegram chats may continue receiving already-authorized alerts. Require private-account verification before enabling interactive approvals. Keep existing endpoint behavior compatible while adding the guided connection APIs.

## Messages, actions and trade updates

Proposal trigger (implementer default, resolved after the builder check): add an explicit “Send for paper approval” action to the existing ready-ticket UI. It sends that exact ready ticket to the user's enabled, verified approval channels. Resending reuses its outstanding action and cannot extend expiry silently. It does not turn a raw scan alert into a plan or size/create new tickets automatically. Existing strategy tickets awaiting live confirmation are excluded from this paper flow; selecting this action on an ordinary ready ticket explicitly chooses paper mode. An eligible proposal requires a ready ticket whose current risk check passes. Broader automatic proposal generation or chat commands that create plans are separate work, not implicit in notification setup.

Trade cards show PAPER prominently, symbol, side, quantity, entry/order type, stop, target, estimated risk/charges, data freshness and action expiry. Buttons/commands reference an opaque short action token; no credentials or user-selected order JSON are carried in it. Approvals expire after five minutes or the order/session cutoff, whichever is earlier. The backend loads the owned ticket and verifies its order fingerprint and ready state, then reruns current risk checks under the same per-user execution lock before confirming a paper trade.

Keep an action row per ticket and connection generation, with durable pending/processing/approved/rejected/expired/blocked states and an audit record of channel, verified sender and time. Both channels resolve the same ticket. Duplicate callbacks, delivery retries, concurrent app actions and approval in both apps cannot create a second paper trade. Recovery after a crash reconciles with the existing ticket/paper ledger rather than blindly resubmitting. Reject cancels only a still-cancellable ticket; rejecting a sent/filled trade never means “sell”.

Authenticated inbound routes are a narrow exception to the Clerk middleware. They verify the per-connection provider secret before parsing or making state changes, enforce payload limits, match instance/chat/sender, reject groups and stale/replayed actions, and acknowledge duplicates harmlessly. Web proxying must preserve webhook headers and body without requiring a browser session. Human app setup remains Clerk-protected.

Extend durable event delivery to WhatsApp without losing current Telegram deduplication and 15-minute notification freshness. Queue bounded network work outside HTTP/event-loop paths. Deliver meaningful state transitions: proposal, approval/rejection/expiry, waiting for entry, filled, stop/target/flat-by exit and final paper P&L. Send only transitions, not every price tick. Messaging outages leave authoritative in-app state intact. A delivery retry must never retry a trade action.

## Persistence and lifecycle

Use additive tables in the existing persistent Desk database for channel connections, pairing challenges, inbound deduplication, actions and outbound deliveries. Store secrets through the existing encrypted user-key store. Connection generations revoke old tokens when credentials change or a connection is removed. Bound event retention and retry work. Existing startup/sweep loops may schedule delivery, but outbound failure must not block scans or paper settlement. Single-process deployment remains unchanged.

## Verification and acceptance

Meaningful offline tests must cover user isolation; shared-key instance ownership; untrusted origins and redirect rejection; secret redaction; real documented Evolution response variants; QR/code expiry; pairing retries; Telegram private-chat linking and foreign webhook conflicts; invalid webhook secrets; outgoing self-chat echo rejection; replayed/stale callbacks; simultaneous cross-channel approval and cancel; changed/blocked ticket rejection; disconnect revocation; event deduplication and delivery retry with no repeated trade action.

Frontend tests cover QR/pairing states, Telegram start-link/verification flow, loading/error/retry/unavailable states, independent delivery/approval toggles and accessibility. Run the full Python and frontend suites, typecheck, Node production build and Compose validation. Review the complete patch before integration.

Live verification checks the unconfigured/setup UI and existing account preservation without real trades or unsolicited messages. QR/account linking and test messages require the user's real provider configuration and their interaction on the phone. Do not claim real Evolution compatibility, self-chat callbacks or Telegram delivery has passed until those checks actually run. Documentation must list the deployed provider version and any remaining setup steps. Do not enable real broker orders as part of this release.

## Left to implementation

Exact component/file decomposition, bounded timeout/backoff numbers, local QR library selection, table/index details and sanitized status wording are implementation choices. The builder must retain the ownership, callback authentication, revocation, atomic approval and paper-only constraints above.
