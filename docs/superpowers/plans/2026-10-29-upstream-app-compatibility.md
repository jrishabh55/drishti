# Upstream App Compatibility Implementation Plan

Goal: restore the product contracts broken by the v0.7.1 merge and review the result.
Architecture: adapt the product boundary to upstream's public stream/state APIs; keep core files unchanged.
Scope: the five reproduced regressions and upgrade safeguards identified in the conversation.
Constraints: preserve unrelated local Trade Desk work; no paid API calls; no fabricated historical fundamentals; no deployment or push.

1. Add failing end-to-end tests using the real upstream graph and scripted models for tool events, missing-price abort, manager sections, parallel analyst starts and replay deduplication. Adapt runner to stream_run, translator to investment_plan/final_trade_decision; retain old replay compatibility. Use create_run_state and the graph's final rating.
2. Add failing tests proving REVIEW, missing and unrecognized ratings cannot produce actionable trade sizing. Keep Hold reference levels and existing long/short behavior.
3. Add failing API tests for custom menu placeholders. Filter them from catalog responses and reject placeholder/empty model IDs before queuing, while permitting actual model IDs.
4. Add tests for engine-version cache invalidation and checkpoint rejection before a resume claim. Record an application/core compatibility identity with new runs; reject missing/old identity. Document historical data restrictions, rebuild and checkpoint guidance; refresh the stale dependency lock if needed.
5. Run focused checks and the full Python suite, request independent fresh-context review, resolve material findings, apply only this patch to the user's checkout, then rerun the complete suite including local Trade Desk tests.

Review focus: missing-price tool results must stop before research; replay must not duplicate manager events; REVIEW must never become a viable long; concurrent runs must retain their own config; old checkpoints must not consume a resume claim.
