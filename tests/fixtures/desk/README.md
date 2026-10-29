# Trade Desk test fixtures

- `upstream-TSLA-60d5m.json.gz` — copied from day-trader-skill
  (https://github.com/Jakeschincariol/day-trader-skill, commit 3d0f4ef, MIT,
  © 2026 Jake Schincariol; see apps/api/desk/THIRD_PARTY_NOTICES.md).
  Yahoo v8 chart JSON: TSLA 5-minute bars for the 60 days to 2026-10-19. Used
  only for port-correctness tests that reproduce the upstream README numbers
  with the NYSE session spec.
- NSE behaviour is tested on synthetic sessions built in `tests/desk_utils.py`
  (no network in tests). Record real `.NS` fixtures with
  `scripts/desk_spike.py` from a machine that can reach Yahoo.
