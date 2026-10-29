from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from apps.api.scanner.universe import enrich_universe, seed_universe
from tests.scanner_utils import make_store


def test_seed_from_bundled_csv(tmp_path):
    store = make_store(tmp_path)
    n = seed_universe(store)
    assert n > 1500  # all NSE mainboard EQ-series equities
    inst = store.instruments_df()
    assert (inst["yf_symbol"].str.endswith(".NS")).all()
    assert set(inst.loc["RELIANCE", "index_memberships"]) == {"NIFTY50", "NIFTY500"}
    non_index = inst[inst["index_memberships"].map(len) == 0]
    assert len(non_index) > 0


def test_seed_is_idempotent(tmp_path):
    store = make_store(tmp_path)
    seed_universe(store)
    n1 = len(store.instruments_df())
    seed_universe(store)
    assert len(store.instruments_df()) == n1


def test_enrich_writes_fundamentals(tmp_path):
    store = make_store(tmp_path, {"TCS": [100.0]})
    fake = MagicMock()
    fake.info = {"sector": "Technology", "industry": "IT Services",
                 "marketCap": 12_000_000_000_000, "trailingPE": 28.5,
                 "priceToBook": 12.0, "returnOnEquity": 0.45,
                 "dividendYield": 0.012, "trailingEps": 130.0,
                 "debtToEquity": 8.0, "revenueGrowth": 0.06}
    with patch("apps.api.scanner.universe.yf.Ticker", return_value=fake):
        n = enrich_universe(store, sleep_s=0)
    assert n == 1
    inst = store.instruments_df()
    assert inst.loc["TCS", "sector"] == "Technology"
    assert inst.loc["TCS", "fundamentals"]["pe"] == 28.5


def test_enrich_survives_per_symbol_failure(tmp_path):
    store = make_store(tmp_path, {"AAA": [1.0], "BBB": [1.0]})
    def boom_then_ok(sym):
        if sym == "AAA.NS":
            raise RuntimeError("rate limited")
        m = MagicMock(); m.info = {"sector": "X", "marketCap": 1}
        return m
    with patch("apps.api.scanner.universe.yf.Ticker", side_effect=boom_then_ok):
        n = enrich_universe(store, sleep_s=0)
    assert n == 1


def test_fo_membership_from_the_recorded_nse_lot_file(tmp_path):
    from apps.api.desk.datasets import parse_fo_lots
    from apps.api.desk.orb import pick_universe
    from apps.api.desk.store import reset_desk_store_for_tests
    from apps.api.scanner.universe import apply_fo_membership, fo_symbols

    reset_desk_store_for_tests(tmp_path / "desk.sqlite")   # NSE unreachable -> bundled list

    recorded = parse_fo_lots((Path(__file__).parent / "fixtures" / "desk" / "sources"
                              / "nse_fo_mktlots.csv").read_text())
    assert fo_symbols() == set(recorded)          # bundled snapshot == NSE's file
    store = make_store(tmp_path)
    seed_universe(store)
    inst = store.instruments_df()
    assert inst.loc["RELIANCE", "fno"] and not inst.loc["20MICRONS", "fno"]
    syms, notes = pick_universe(inst, "fno")
    assert notes == [] and "RELIANCE" in syms and "NIFTY" not in syms
    # a store seeded before the list existed gets the flags without a reseed
    store.upsert_instruments([{"symbol": "RELIANCE", "yf_symbol": "RELIANCE.NS",
                               "name": "Reliance", "index_memberships": [], "fno": False}])
    assert not store.instruments_df().loc["RELIANCE", "fno"]
    assert apply_fo_membership(store) == len(syms)
    assert store.instruments_df().loc["RELIANCE", "fno"]
