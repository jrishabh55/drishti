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

The existing self-hosted service discovered for this deployment is `https://evolution-srv1.codeation.io`, image `evoapicloud/evolution-api:v2.3.7`; its public version response confirmed 2.3.7 on 29 October 2026. No server upgrade or changes to pre-existing instances are required. Drishti creates its own generated instance per user. Provisioning, QR linking and real callbacks must still be verified against that installation before claiming end-to-end provider compatibility.

Providers call `/api/messaging/hooks/<connection-id>` through the ordinary web service. Keep the API container private. This one callback prefix bypasses Clerk and requires a separate per-connection provider secret. The web proxy preserves provider authentication headers; setup APIs still require ordinary app authentication. Configure a public HTTPS app origin explicitly; callback URLs never derive from the incoming Host header.

## WhatsApp

1. Save the server configuration. Leave the API-key override blank to use the server key.
2. Generate a QR, then scan it in WhatsApp → Linked devices → Link a device. Alternatively enter your number with country code and request a pairing code. Codes expire; refresh after expiry. QR generation stays local to Drishti.
3. Refresh connection state to confirm the actual linked number. Choose a private recipient (your own number for self-chat, or another number you control).
4. Send a verification code and enter it in Drishti. This sends a real message only when you press the button.
5. Reply to the resulting LINK instruction as a **new message** in WhatsApp. This independently proves inbound replies work. If self-chat replies are not delivered by your provider, use another verified private number or the authenticated app for decisions. LID-only unknown identities and groups cannot approve.
6. Enable alerts and/or paper approvals. Use Test WhatsApp for an explicit test message.

WhatsApp proposals use explicit `APPROVE <code>`, `REJECT <code>` and `STATUS <code>` replies. This is the supported fallback across Evolution installations where native interactive buttons are unavailable. Delivery of a QR or message is not treated as proof of button support.

## Telegram

1. Create a bot in Telegram's BotFather and save its token in the password field.
2. Drishti validates the bot and installs its authenticated webhook. An existing webhook in another app is not replaced unless you explicitly choose replacement. Create a separate bot when you want the original integration to continue.
3. Create a connection link, open it in Telegram and Start the bot in a private chat. The link is one-time and expires after five minutes.
4. Confirm the displayed Telegram identity back in Drishti. There is no manual chat-ID lookup.
5. Enable alerts and/or paper approvals, then use Test Telegram if desired.

Existing legacy Telegram notifications remain available. Enabling guided Telegram alerts disables the user's legacy delivery toggle to prevent duplicates. A saved legacy chat alone never authorizes trade approvals.

## Approve a paper proposal

Create a plan and a ready ticket in Drishti, then choose **Send for paper approval**. This sends the exact ticket to verified channels with paper approvals enabled. It does not automatically turn scanner alerts into trades, and live strategy tickets are excluded.

Telegram has Approve paper trade, Reject, Status and View plan buttons. WhatsApp has explicit reply codes and the plan link. Approvals expire after five minutes or the entry cutoff, whichever comes first. Current risk is checked again. The persistent ticket and shared per-user lock prevent double paper trades from repeated callbacks, retries or decisions in both channels.

Reject cancels a pending proposal; it never sells an existing position. Status updates describe paper acceptance, waiting for entry, fill, cancellation and exit including net P&L when available. Messaging delivery retries do not repeat trade execution. Notification failures remain visible in Messages, and the in-app ledger is authoritative.

**Real broker orders remain disabled.** These callbacks do not arm Zerodha, call its order API or bypass its separate live-trading controls.

Disconnect revokes pending actions locally before attempting remote logout/webhook cleanup. If remote cleanup fails, Drishti reports it; check the provider dashboard. Never remove unrelated Evolution instances or replace another user's webhook.

## Release checks

Automated verification uses isolated databases and controlled provider responses; it creates no real provider accounts/messages or broker orders. Live acceptance needs the account owner's phone scan, Telegram Start, identity confirmation and explicit test-message actions. Record those separately from offline test results.
