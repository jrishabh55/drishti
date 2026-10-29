# WhatsApp approval buttons implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this single task inline, then request one independent review.

**Goal:** Offer Approve paper trade, Reject and Status buttons through the existing Evolution 2.3.7 server.

**Architecture:** Keep the existing durable proposal and callback authorization paths. Queue the existing action buttons for WhatsApp too, translate callback buttons into Evolution reply buttons at delivery, and normalize native-flow replies before the existing paper decision handler. Keep explicit text codes and the plan link in the message. Fall back to sendText only for definitive unsupported-endpoint HTTP responses (404, 405, 501); ambiguous failures retain the normal outbox retry behavior.

**Tech stack:** Existing Python/FastAPI/SQLite, Evolution v2 sendButtons, React copy update.

**Spec:** docs/superpowers/specs/2026-10-29-messaging-controls-design.md (already approved; native buttons with text fallback). This is completion of that adapter behavior, not a new trade workflow.

## Global constraints

- Paper only; real broker orders remain disabled.
- Exact recipient, instance, generation, expiry and current-risk checks remain authoritative.
- Never send real phone messages during automated verification.
- Do not change upstream tradingagents/ or cli/.
- At most three reply buttons; do not mix URL buttons with replies. Plan URL remains in the body.

## Review focus

- Native-flow button replies and old button replies both reach the same idempotent paper decision path.
- Malformed JSON, non-object JSON and forwarded interactive content cannot execute trades.
- Wrong instance/sender, expired proposal and changed identity remain unable to approve.
- Timeout, authentication and server failures must not trigger a second text send in the same delivery attempt.
- Endpoint rejection uses the original text and proposal code, retaining one paper decision across channels.

## Task 1: Native WhatsApp proposal delivery and replies

**Files:** apps/api/messaging/{actions,service}.py; tests/test_messaging_buttons.py; apps/web/src/components/desk/MessagingSettings.tsx; docs/messaging-setup.md.

**Interfaces:** Retain send(doc, text, buttons=None), receive(cid, secret, payload), and the existing outbox button shape. Evolution consumes title/description/footer and buttons of type reply with displayText/id. Incoming nativeFlowResponseMessage.paramsJson is a bounded JSON object with id and name quick_reply.

- [x] Write tests for real proposal-to-provider payload, native/legacy callback-to-paper state, duplicate/cross-channel replies, expiry and wrong recipient, malformed/forwarded content, definitive fallback and no fallback on ambiguous failures.
- [x] Run the new file and observe failures for missing WhatsApp buttons/native callback support.
- [x] Implement minimal queue, payload, fallback and callback normalization changes.
- [x] Run new tests and the complete UTC Python suite; frontend tests, type checking and build for updated copy.
- [x] Update UI/operator wording honestly; obtain independent review and address important findings.
- [ ] Commit, merge/push using existing authorization, deploy Drishti and verify health. Do not update Evolution or send real messages.
