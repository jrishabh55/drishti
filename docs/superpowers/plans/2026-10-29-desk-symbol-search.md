# Desk symbol search and analysis

Goal: Add Symbol searches NSE stocks by company name or ticker. Picking a result enables Add and an analysis-form link. Existing watchlist cards and breakout rows also expose Analyse.
Approach: reuse the authenticated ticker search API and shadcn Command primitives; retain exact-symbol entry when search is unavailable. Analysis links prefill the existing /analyse form and never start paid calls automatically.

- [ ] Add regression tests for name lookup, NSE filtering, keyboard selection, stale responses, exact-symbol fallback and add failures.
- [ ] Implement the picker and wire it into the Desk; add analysis links to watched stocks and scan results.
- [ ] Run frontend tests, type check and production build; independently review, then deploy and verify the new flow.

Constraints: NSE-only Desk, no server/core changes, preserve unrelated design artifacts, no automatic analyses or orders. Search empty/error/loading states must be distinct; old search responses must not replace new results.
