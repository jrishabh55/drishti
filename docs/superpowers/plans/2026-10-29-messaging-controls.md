# Messaging Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect a user's WhatsApp/Telegram inside Drishti and safely approve exact paper tickets from a verified private chat.
**Architecture:** Backend-owned connections, encrypted provider credentials, authenticated public callbacks, persistent actions/outbox and existing atomic paper confirmation. Direct Evolution v2.3.7 and Telegram adapters; the frontend never receives provider credentials after saving.
**Tech Stack:** FastAPI, requests, SQLite/Fernet, React/TanStack/shadcn, local QR rendering.
**Spec:** `docs/superpowers/specs/2026-10-29-messaging-controls-design.md` (approved by user: “go”).

## Global Constraints
- Product-layer changes only; upstream core stays read-only.
- Five-minute private pairing/actions; fifteen-minute notification freshness; no real broker execution from messaging.
- Evolution URL is administrator-trusted; instances are generated and owned per user. Never enumerate/reuse unrelated instances.
- Clerk protects setup; webhook endpoints authenticate provider-specific secrets and bound sender/instance instead.
- Existing Telegram alerts, paper accounts and encryption secret remain compatible.
- No unsolicited test sends, account linking or real orders during automated/live UI verification.

## Review Focus
- Provider timeout after create/send: reuse instance/action IDs and never repeat a trade action.
- Disconnect while callback is queued: generation revocation wins before confirmation.
- Bot reused across users or foreign webhook: reject takeover.
- Cross-channel/app simultaneous decisions: exactly one paper trade, immutable ticket fingerprint.
- WhatsApp self-chat echoes, LID-only identity, malformed QR: do not invent successful pairing/approval capability.

### Task 1: Provider adapters and persistent connections
**Files:** create `apps/api/messaging/{__init__,providers,store,connections}.py`; test `tests/test_messaging_connections.py`.
**Interfaces:** `connections.status(uid, channel) -> dict`, `configure_whatsapp(uid,url,key)`, `pair_whatsapp(uid,number)`, `configure_telegram(uid,token,replace_webhook=False)`, `begin_telegram_pair(uid)`, `confirm_pair(uid,channel,code='')`, `disconnect(uid,channel)`. `providers.evolution(base,key,path,method='GET',body=None,params=None)` and `providers.telegram(token,method,body=None)` normalize sanitized failures, bounded timeouts, no redirects. Store offers scoped connection/document and atomic action/outbox primitives.
- [ ] Write tests for absent setup, trusted URL validation, instance reuse/ownership, no secret leakage, QR/phone variants, Telegram conflict/private pairing and revocation.
- [ ] Run focused tests and observe missing behavior fail.
- [ ] Implement additive persistence, encrypted configuration and bounded provider adapters, including a local QR image fallback.
- [ ] Run focused tests; commit the deliverable.

### Task 2: Authenticated API and channel delivery
**Files:** create `apps/api/routes/messaging.py`, `apps/api/messaging/service.py`; modify `apps/api/auth.py`, `apps/api/app.py`, `apps/api/desk/loop.py`; test `tests/test_messaging_api.py`.
**Interfaces:** authenticated `/api/messaging/{channel}` setup/status/preferences/test/disconnect and pairing actions; narrow `/api/messaging/hooks/{id}` callbacks. `service.receive(id,secret,payload)` verifies channel identity before dispatch. `service.sweep()` performs bounded delivery/expiry/transition work; lifecycle owns its periodic background task.
- [ ] Write tests for missing/wrong callback secret, body bounds, mismatched sender/instance, outgoing echoes, one-time pairing and disconnect races.
- [ ] Observe red, implement routes/service and lifecycle, preserving existing auth behavior and legacy Telegram delivery.
- [ ] Verify per-channel opt-in and retry/expiry without event-loop I/O; commit.

### Task 3: Exact paper approval and transition updates
**Files:** create `apps/api/messaging/actions.py`; modify messaging service/store; test `tests/test_messaging_actions.py`.
**Interfaces:** `actions.propose(uid,ticket_id) -> dict`, `actions.decide(uid,channel,token,decision,sender) -> dict`; proposal endpoint returns sanitized delivery/action state. Existing `desk.confirm_ticket(...ConfirmBody(mode='paper'))` and `cancel_ticket` remain authoritative.
- [ ] Write tests for blocked/changed/strategy ticket, five-minute/session expiry, concurrent cross-channel decisions, consumed action replay and post-disconnect denial.
- [ ] Observe red, implement opaque action cards/Telegram buttons/WhatsApp explicit reply codes, outbox and paper-transition messages.
- [ ] Verify crash recovery consults ticket/paper ledger; callbacks never enter live broker paths; commit.

### Task 4: Guided setup UI, configuration, review and release
**Files:** create `apps/web/src/routes/desk.messages.tsx`, `apps/web/src/components/desk/MessagingSettings.tsx`, `apps/web/src/lib/messaging-api.ts`; modify DeskNav, Alerts, settings and TicketCard; add component tests, env/Compose docs and operator guide.
- [ ] Write frontend tests for unconfigured/error/pairing/connected states, QR/code rendering, Telegram confirmation, toggles and paper-proposal action; observe red.
- [ ] Implement with existing shadcn/tokens, polling cleanup, secret clearing, server defaults and actionable capability errors; preserve legacy Telegram alerts.
- [ ] Run full Python suite, frontend tests/typecheck/build and Compose config; independent whole-branch review; fix material issues with regression tests.
- [ ] Integrate/push/deploy under existing authorization; preserve encryption/live-order flags. Configure discovered Evolution URL and backend-only key securely. Verify health and real setup screens; phone linking remains user interaction.
- [ ] Record actual checks and external setup dependencies. Close the reviewer session after completion.
