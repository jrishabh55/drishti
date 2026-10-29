"""Source hardening for the Indian research feeds: block pages served with
HTTP 200 are never cached or read as "not on the list", a failing host is
skipped by every dataset on it, watchlist news answers within a deadline,
plural promoter categories count, and the F&O universe refreshes from NSE
while keeping each instrument's other data."""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from apps.api.desk import datasets, feeds, insider, news, research
from apps.api.desk.store import reset_desk_store_for_tests
from tests.scanner_utils import make_store

SRC = Path(__file__).parent / "fixtures" / "desk" / "sources"
NOW = datetime(2026, 10, 29, 10, 0, tzinfo=timezone.utc)
BLOCK_PAGE = b"<html><head><title>Access Denied</title></head><body>Reference #18</body></html>"


@pytest.fixture()
def store(tmp_path):
    datasets.clear_negative_cache_for_tests()
    feeds.clear_facts_providers_for_tests()
    yield reset_desk_store_for_tests(tmp_path / "desk.sqlite")
    datasets.clear_negative_cache_for_tests()


def test_html_200_is_never_cached_or_read_as_clean_facts(store, monkeypatch):
    monkeypatch.setattr(datasets, "_http_get", lambda ds, url: BLOCK_PAGE)
    got = research.symbol_facts("RELIANCE", now=NOW)
    assert got["status"] == "unavailable"
    assert {s["status"] for s in got["sources"]} == {"unavailable"}
    # no "not banned" / "not in the band file" claims from a block page
    assert "fo_ban" not in got["facts"] and "listed_in_band_file" not in got["facts"]
    assert got["facts"]["fno"] is True and "snapshot" in got["facts"]["fno_source"]
    for name in ("nse.price_bands", "nse.fo_lots", "nse.fo_ban", "nse.asm", "nse.gsm"):
        assert store.get_feed(name, datasets.params_hash({})) is None
    assert datasets.nse_facts(["RELIANCE"]) == {}
    deals = insider.latest_deals(["RELIANCE"], only_watchlist=False, now=NOW)
    assert deals["status"] == "unavailable" and deals["deals"] == []


def test_block_page_after_good_data_serves_the_good_copy_as_stale(store, monkeypatch):
    good = (SRC / "nse_fo_ban.csv").read_bytes()
    monkeypatch.setattr(datasets, "_http_get", lambda ds, url: good)
    assert datasets.fetch("nse.fo_ban", now=NOW)["cached"] is False
    monkeypatch.setattr(datasets, "_http_get", lambda ds, url: BLOCK_PAGE)
    got = datasets.fetch("nse.fo_ban", now=NOW + timedelta(hours=7))
    assert got["stale"] and "SAIL" in got["text"]
    assert "SAIL" in store.get_feed("nse.fo_ban", datasets.params_hash({}))["payload"]


@pytest.mark.parametrize("name,body", [
    ("nse.announcements", b"<!DOCTYPE html><html>maintenance</html>"),
    ("nse.xbrl", b"<!DOCTYPE html><html>not found</html>"),
    ("nse.xbrl", b"<?xml version='1.0'?><html>not xbrl</html>"),
    ("nse.bulk_deals", b"Something went wrong, please try later"),
    ("nse.fo_lots", b"UNDERLYING,SYMBOL\nNIFTY 50,NIFTY,65\n"),
    ("yahoo.rss", b"<?xml version='1.0'?><error>throttled</error>"),
])
def test_malformed_bodies_are_rejected_per_kind(store, monkeypatch, name, body):
    monkeypatch.setattr(datasets, "_http_get", lambda ds, url: body)
    params = {"nse.announcements": {"symbol": "INFY", "from": "2026-10-28", "to": "2026-10-29"},
              "nse.xbrl": {"file": "INTEGRATED_FILING_INDAS_1_WEB.xml"},
              "yahoo.rss": {"symbol": "INFY"}}.get(name, {})
    with pytest.raises(datasets.FeedUnavailable):
        datasets.fetch(name, params, now=NOW)
    assert store.get_feed(name, datasets.params_hash(params)) is None


def test_parsers_reject_the_wrong_schema():
    for parse in (datasets.parse_fo_ban, datasets.parse_fo_lots, datasets.parse_price_bands,
                  datasets.parse_deals):
        with pytest.raises(ValueError):
            parse("<html><body>Access Denied</body></html>")
    assert datasets.parse_fo_ban("Securities in Ban For Trade Date 30-OCT-2026: NIL\n") == []


def test_blocked_host_is_skipped_by_every_dataset_and_watchlist_news_is_bounded(store, monkeypatch):
    nse_calls = []

    def serve(ds, url):
        if "nseindia.com" in url:
            nse_calls.append(url)
            time.sleep(0.3)              # a slow timeout
            raise TimeoutError("read timed out")
        raise datasets.FeedUnavailable("source_error", f"{ds.name}: HTTP 404")

    monkeypatch.setattr(datasets, "_http_get", serve)
    syms = ["RELIANCE", "INFY", "TCS", "SBIN", "HDFCBANK", "ITC", "LT", "AXISBANK", "WIPRO", "ONGC"]
    t0 = time.monotonic()
    out = news.watchlist_news(syms, days=3, now=NOW)
    elapsed = time.monotonic() - t0
    # without the breaker: 10 symbols x 0.3 s; with it, only the first
    # concurrent wave reaches NSE and the rest skip the failing host at once
    assert len(nse_calls) <= news.WATCHLIST_WORKERS
    assert elapsed < 1.5
    nse = [s for s in out["sources"] if s["source"] == "nse.announcements"]
    assert len(nse) == 10 and all(s["status"] == "unavailable" for s in nse)
    assert any("failing" in s["error"] for s in nse)
    # another dataset on the same host is skipped too, without a request
    n = len(nse_calls)
    with pytest.raises(datasets.FeedUnavailable):
        datasets.fetch("nse.asm", now=NOW)
    assert len(nse_calls) == n


def test_watchlist_news_answers_by_its_deadline(store, monkeypatch):
    def hang(ds, url):
        time.sleep(1.0)
        raise datasets.FeedUnavailable("source_error", f"{ds.name}: HTTP 404")

    monkeypatch.setattr(datasets, "_http_get", hang)
    t0 = time.monotonic()
    out = news.watchlist_news(["RELIANCE", "INFY"], days=2, now=NOW, deadline_s=0.2)
    assert time.monotonic() - t0 < 0.8
    assert out["status"] == "unavailable" and out["items"] == []
    assert all("no answer within" in s["error"] for s in out["sources"])


def test_throttle_slots_are_reserved_under_the_lock():
    datasets.clear_negative_cache_for_tests()
    waits = []
    threads = [threading.Thread(target=lambda: waits.append(datasets._take_slot("h.example")))
               for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(round(w) for w in waits) == [0, 1, 2, 3, 4]   # never two in one slot
    datasets.clear_negative_cache_for_tests()


def test_plural_promoters_count_as_promoters():
    rows = [{"symbol": "ABC", "acqName": "X Trust", "personCategory": cat,
             "tdpTransactionType": "Sell", "acqMode": "Market Sale", "secAcq": "100",
             "secVal": "100000", "date": "28-Oct-2026 18:00", "acqfromDt": "27-Oct-2026",
             "acqtoDt": "27-Oct-2026"}
            for cat in ("Promoters", "Promoter Group", "Promoter & Director", "Director")]
    pit = insider.parse_pit(json.dumps({"data": rows}))
    assert [r["promoter"] for r in pit] == [True, True, True, False]
    s = insider.summarize(pit)
    assert s["promoter_market_trades"]["sell_inr"] == 300000.0
    assert s["market_trades"]["sell_inr"] == 400000.0


def _seed(tmp_path):
    scan = make_store(tmp_path, None)
    scan.upsert_instruments([
        {"symbol": "RELIANCE", "yf_symbol": "RELIANCE.NS", "name": "Reliance", "sector": "Energy",
         "industry": "Refining", "market_cap": 1.9e13, "index_memberships": ["NIFTY50"],
         "fno": False, "fundamentals": {"pe": 24.0}},
        {"symbol": "NEWCO", "yf_symbol": "NEWCO.NS", "name": "New Co", "sector": "Tech",
         "index_memberships": [], "fno": False, "fundamentals": {"pe": 30.0}},
        {"symbol": "20MICRONS", "yf_symbol": "20MICRONS.NS", "name": "20 Microns",
         "index_memberships": [], "fno": True, "fundamentals": {}},
    ])
    return scan


def test_fo_membership_refreshes_from_nse_and_keeps_other_columns(store, tmp_path, monkeypatch):
    from apps.api.scanner.universe import apply_fo_membership, fo_membership_source

    scan = _seed(tmp_path)
    live = (SRC / "nse_fo_mktlots.csv").read_text() + "NEW COMPANY LTD ,NEWCO ,500 ,500 ,500\n"
    monkeypatch.setattr(datasets, "_http_get", lambda ds, url: live.encode())
    apply_fo_membership(scan)
    inst = scan.instruments_df()
    assert inst.loc["RELIANCE", "fno"] and inst.loc["NEWCO", "fno"]   # NEWCO isn't in the bundle
    assert not inst.loc["20MICRONS", "fno"]                           # dropped by NSE
    assert inst.loc["RELIANCE", "sector"] == "Energy" and inst.loc["RELIANCE", "market_cap"] == 1.9e13
    assert inst.loc["RELIANCE", "fundamentals"] == {"pe": 24.0}
    assert inst.loc["RELIANCE", "index_memberships"] == ["NIFTY50"]
    src = fo_membership_source(scan)
    assert src["source"] == "nse.fo_lots" and src["as_of"] and not src["stale"]


def test_fo_membership_falls_back_to_bundled_snapshot_and_says_so(store, tmp_path, monkeypatch):
    from apps.api.scanner.universe import FO_SNAPSHOT_DATE, apply_fo_membership, fo_membership_source

    scan = _seed(tmp_path)
    monkeypatch.setattr(datasets, "_http_get", lambda ds, url: BLOCK_PAGE)
    apply_fo_membership(scan)
    inst = scan.instruments_df()
    assert inst.loc["RELIANCE", "fno"] and not inst.loc["NEWCO", "fno"]
    assert inst.loc["NEWCO", "fundamentals"] == {"pe": 30.0}
    src = fo_membership_source(scan)
    assert src["source"] == "bundled" and src["as_of"] == FO_SNAPSHOT_DATE and src["stale"]
    assert "HTML" in src["reason"]


def test_risk_facts_never_use_stale_copies_as_current_clearance(store, monkeypatch):
    def stale(name, *args, **kwargs):
        if name == "nse.fo_ban":
            return {"text": (SRC / "nse_fo_ban.csv").read_text(), "stale": True}
        raise datasets.FeedUnavailable("source_error", "source unavailable")
    monkeypatch.setattr(datasets, "fetch", stale)
    # Yesterday's ban list remains useful research, but cannot clear today's risk check.
    assert datasets.nse_facts(["RELIANCE"]) == {}
