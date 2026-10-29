from __future__ import annotations

from apps.api.scanner.seed import seed_prebuilt
from tests.scanner_utils import make_store


def test_all_prebuilt_seed_and_validate(tmp_path):
    store = make_store(tmp_path)
    n = seed_prebuilt(store)
    assert n == 24  # 19 originals + 5 day-trader daily setups
    names = {s["name"] for s in store.list_scanners("nobody")}
    assert "Golden cross" in names and "Volume spike" in names
    assert "Death cross" in names and "Weekly momentum" in names
    assert "Bull flag (tight after a run)" in names and "20-day closing breakout" in names
    # Idempotent: re-seed updates, never duplicates.
    seed_prebuilt(store)
    assert len(store.list_scanners("nobody")) == 24


def test_daytrader_daily_setups_match_upstream_rules(tmp_path):
    """The five day-trader-skill daily setups, expressed in the scanner AST,
    fire on the shapes upstream's find_setups() tags."""
    from apps.api.scanner.engine import get_engine

    # Upstream bull_flag: c[-6]/c[-16]-1 > 10% and the last-5 range < 5% of the close.
    flag = [100.0] * 10 + [100 + i * 2.0 for i in range(1, 11)] + [121.0, 121.5, 120.8, 121.2, 121.4]
    breakout = [100.0] * 24 + [104.0]   # same length: the engine scores the latest bar
    store = make_store(tmp_path, {"FLAG": flag, "BRK": breakout})
    seed_prebuilt(store)
    by_name = {s["name"]: s for s in store.list_scanners("nobody")}
    engine = get_engine()

    def matches(name):
        from apps.api.scanner.schema import parse_definition

        res = engine.run(parse_definition(by_name[name]["definition"]))
        return {m["symbol"] for m in res["matches"]}

    assert "FLAG" in matches("Bull flag (tight after a run)")
    assert "BRK" in matches("20-day closing breakout")
    assert "BRK" not in matches("Bull flag (tight after a run)")
