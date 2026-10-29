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


# ---------------------------------------------------------------- risk facts availability

def _serve(texts):
    """Serve each NSE list from ``texts`` by dataset name; anything else is a 404."""
    def get(ds, url):
        if ds.name in texts:
            return texts[ds.name].encode()
        raise datasets.FeedUnavailable("source_error", f"{ds.name}: HTTP 404")
    return get


LISTS = {"nse.price_bands": (SRC / "nse_sec_list.csv").read_text(),
         "nse.fo_ban": (SRC / "nse_fo_ban.csv").read_text(),
         "nse.asm": (SRC / "nse_asm.json").read_text(),
         "nse.gsm": (SRC / "nse_gsm.json").read_text(),
         "nse.event_calendar": (SRC / "nse_event_calendar.json").read_text()}


def _risk(market):
    from apps.api.desk import risk
    from tests.test_desk_trade import ist, order
    from apps.api.desk.profile import DeskProfile

    return risk.check([order()], DeskProfile(max_loss_per_trade_inr=200), now=ist(10, 0),
                      market=market)


def test_unreadable_asm_gsm_with_fresh_bands_warns_skipped_not_clean(store, monkeypatch):
    texts = {k: v for k, v in LISTS.items() if k not in ("nse.asm", "nse.gsm")}
    monkeypatch.setattr(datasets, "_http_get", _serve(texts))
    facts = datasets.nse_facts(["SBIN"])
    assert facts["SBIN"]["_available"] == {"bands": True, "asm": False, "gsm": False,
                                           "fo_ban": True, "results": True}
    out = _risk(facts)
    r = out["results"][0]
    assert r["verdict"] == "PASS"                      # compatible: missing data warns
    skipped = [w for w in r["warnings"] if "skipped" in w]
    assert skipped and "ASM list" in skipped[0] and "GSM list" in skipped[0]
    assert "circuit band" not in skipped[0] and "not treated as clean" in skipped[0]
    assert not any("skipped" in w for w in out["warnings"])   # per-check, not the all-down banner


def test_every_list_read_means_no_skip_and_fresh_asm_blocks(store, monkeypatch):
    monkeypatch.setattr(datasets, "_http_get", _serve(LISTS))
    facts = datasets.nse_facts(["SBIN", "21STCENMGM"])
    assert all(facts["SBIN"]["_available"].values())
    assert not any("skipped" in w for w in _risk(facts)["results"][0]["warnings"])
    asm = dict(facts["21STCENMGM"])
    r = _risk({"SBIN": asm})["results"][0]             # SBIN order carrying an ASM stage
    assert r["verdict"] == "BLOCK" and any("surveillance" in x for x in r["reasons"])


def test_all_lists_unreadable_keeps_the_single_top_warning(store, monkeypatch):
    monkeypatch.setattr(datasets, "_http_get", _serve({}))
    assert datasets.nse_facts(["SBIN"]) == {}
    out = _risk({})
    assert any("were skipped" in w for w in out["warnings"])
    assert out["results"][0]["verdict"] == "PASS"


def test_stale_asm_copy_counts_as_unavailable(store, monkeypatch):
    monkeypatch.setattr(datasets, "_http_get", _serve(LISTS))
    old = datetime.now(timezone.utc) - timedelta(days=2)
    datasets.fetch("nse.asm", now=old)                 # cached long ago
    datasets.clear_negative_cache_for_tests()
    texts = {k: v for k, v in LISTS.items() if k != "nse.asm"}
    monkeypatch.setattr(datasets, "_http_get", _serve(texts))
    facts = datasets.nse_facts(["SBIN"])
    assert facts["SBIN"]["_available"]["asm"] is False
    assert any("ASM list" in w for w in _risk(facts)["results"][0]["warnings"])


@pytest.mark.parametrize("name,body", [
    ("nse.asm", "{}"),
    ("nse.asm", '{"longterm": {"data": []}, "shortterm": {"data": []}}'),
    ("nse.asm", '{"longterm": {"data": [{"symbol": "X"}]}}'),
    ("nse.gsm", "{}"),
    ("nse.gsm", '{"data": []}'),
    ("nse.event_calendar", '{"error": "x"}'),
])
def test_wrong_shape_lists_are_rejected_before_cache(store, monkeypatch, name, body):
    monkeypatch.setattr(datasets, "_http_get", lambda ds, url: body.encode())
    with pytest.raises(datasets.FeedUnavailable):
        datasets.fetch(name, now=NOW)
    assert store.get_feed(name, datasets.params_hash({})) is None


def test_parsers_reject_wrong_shapes_but_accept_a_truly_empty_gsm_list():
    for asm in ("{}", '{"longterm": {"data": []}, "shortterm": {"data": []}}'):
        with pytest.raises(ValueError):
            datasets.parse_surveillance(asm, None)
    with pytest.raises(ValueError):
        datasets.parse_surveillance(None, "{}")
    with pytest.raises(ValueError):
        datasets.parse_results_calendar("{}")
    assert datasets.parse_surveillance(None, "[]") == {}


def test_empty_gsm_list_is_clean_and_cached(store, monkeypatch):
    texts = dict(LISTS, **{"nse.gsm": "[]"})
    monkeypatch.setattr(datasets, "_http_get", _serve(texts))
    facts = datasets.nse_facts(["SBIN"])
    assert facts["SBIN"]["_available"]["gsm"] is True
    assert store.get_feed("nse.gsm", datasets.params_hash({}))["payload"] == "[]"


def test_one_bad_xbrl_filing_doesnt_trip_the_archive_host(store, monkeypatch):
    calls = []

    def serve(ds, url):
        calls.append(ds.name)
        if ds.name == "nse.xbrl":
            return b"<?xml version='1.0'?><report>not an xbrl instance</report>"
        return _serve(LISTS)(ds, url)

    monkeypatch.setattr(datasets, "_http_get", serve)
    with pytest.raises(datasets.FeedUnavailable):
        datasets.fetch("nse.xbrl", {"file": "INTEGRATED_FILING_INDAS_1_WEB.xml"}, now=NOW)
    # same host (nsearchives) still serves the risk lists
    assert datasets.host_down("nsearchives.nseindia.com") is None
    assert "SAIL" in datasets.fetch("nse.fo_ban", now=NOW)["text"]
    assert calls == ["nse.xbrl", "nse.fo_ban"]


def test_a_block_page_on_xbrl_does_trip_the_host(store, monkeypatch):
    monkeypatch.setattr(datasets, "_http_get", lambda ds, url: BLOCK_PAGE)
    with pytest.raises(datasets.FeedUnavailable):
        datasets.fetch("nse.xbrl", {"file": "INTEGRATED_FILING_INDAS_1_WEB.xml"}, now=NOW)
    assert datasets.host_down("nsearchives.nseindia.com") is not None


def test_deployment_probe_does_not_certify_a_200_block_page(tmp_path, monkeypatch):
    from scripts.desk_spike import probe
    monkeypatch.setattr(datasets, '_http_get', lambda *args: BLOCK_PAGE)
    out = probe('nse.asm', tmp_path)
    assert out['status'] is None and 'HTML' in out['error']
    assert list(tmp_path.iterdir()) == []


def test_deployment_probe_records_a_valid_source_body(tmp_path, monkeypatch):
    from scripts.desk_spike import probe
    body = (SRC / 'nse_asm.json').read_bytes()
    monkeypatch.setattr(datasets, '_http_get', lambda *args: body)
    out = probe('nse.asm', tmp_path)
    assert out['status'] == 200 and out['error'] is None
    assert (tmp_path / 'nse.asm.json').read_bytes() == body
