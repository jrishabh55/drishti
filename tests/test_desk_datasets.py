"""NSE dataset catalog, fetch cache, parsers (assumed formats) and risk facts."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from apps.api.desk import datasets, feeds
from apps.api.desk.store import reset_desk_store_for_tests
from tests.scanner_utils import make_store

FO_BAN = "Securities in Ban For Trade Date 29-OCT-2026:\n1,RBLBANK\n2,MANAPPURAM\n"
BANDS = ("Symbol,Series,Security Name,Band,Remarks\n"
         "SBIN,EQ,State Bank of India,20,\nYESBANK,EQ,Yes Bank,5,\n"
         "SMALLCO,BE,Small Co,2,\nHDFCBANK,EQ,HDFC Bank,No Band,\n")
DEALS = ("Date,Symbol,Security Name,Client Name,Buy/Sell,Quantity Traded,"
         "Trade Price / Wght. Avg. Price,Remarks\n"
         "29-OCT-2026,SBIN,State Bank of India,SOME FUND,BUY,\"1,500,000\",812.35,-\n")


@pytest.fixture()
def store(tmp_path):
    datasets.clear_negative_cache_for_tests()
    feeds.clear_facts_providers_for_tests()
    return reset_desk_store_for_tests(tmp_path / "desk.sqlite")


def test_catalog_urls_are_pinned_and_params_validated():
    ds = datasets.CATALOG["nse.announcements"]
    span = {"from": "2026-10-01", "to": "2026-10-29"}
    assert datasets.build_url(ds, {"symbol": "sbin", **span}).endswith(
        "symbol=SBIN&from_date=01-10-2026&to_date=29-10-2026")
    assert "symbol=M%26M&" in datasets.build_url(ds, {"symbol": "M&M", **span})
    with pytest.raises(datasets.FeedUnavailable, match="not an NSE symbol"):
        datasets.build_url(ds, {"symbol": "x&y=1/../", **span})
    with pytest.raises(datasets.FeedUnavailable, match="not an NSE XBRL"):
        datasets.build_url(datasets.CATALOG["nse.xbrl"], {"file": "../../etc/passwd"})
    with pytest.raises(datasets.FeedUnavailable, match="not a BSE scrip"):
        datasets.build_url(datasets.CATALOG["bse.announcements"],
                           {"code": "1 OR 1", **span})
    assert "strPrevDate=20261001&" in datasets.build_url(
        datasets.CATALOG["bse.announcements"], {"code": "500325", **span})
    with pytest.raises(datasets.FeedUnavailable, match="takes no"):
        datasets.build_url(datasets.CATALOG["nse.fo_ban"], {"url": "https://evil"})
    pit = datasets.build_url(datasets.CATALOG["nse.insider_pit"],
                             {"symbol": "SBIN", "from": "2026-10-01", "to": "2026-10-29"})
    assert "symbol=SBIN&from_date=01-10-2026&to_date=29-10-2026" in pit
    assert all(d.route == "server" for d in datasets.CATALOG.values())   # server first


def test_parsers_on_assumed_formats():
    assert datasets.parse_fo_ban(FO_BAN) == ["RBLBANK", "MANAPPURAM"]
    bands = datasets.parse_price_bands(BANDS)
    assert bands["SBIN"] == {"band_pct": 20.0, "series": "EQ"}
    assert bands["HDFCBANK"]["band_pct"] is None and bands["SMALLCO"]["series"] == "BE"
    deal = datasets.parse_deals(DEALS)[0]
    assert deal["qty"] == 1_500_000 and deal["side"] == "BUY" and deal["value_cr"] == 121.85


def test_fetch_caches_serves_stale_and_negative_caches(store, monkeypatch):
    calls = []

    def ok(ds, url):
        calls.append(url)
        return FO_BAN.encode()

    monkeypatch.setattr(datasets, "_http_get", ok)
    now = datetime(2026, 10, 29, 5, tzinfo=timezone.utc)
    assert datasets.fetch("nse.fo_ban", store=store, now=now)["cached"] is False
    assert datasets.fetch("nse.fo_ban", store=store, now=now + timedelta(minutes=5))["cached"]
    assert len(calls) == 1

    def down(ds, url):
        calls.append(url)
        raise datasets.FeedUnavailable("source_error", "HTTP 403")

    monkeypatch.setattr(datasets, "_http_get", down)
    later = now + timedelta(hours=8)
    got = datasets.fetch("nse.fo_ban", store=store, now=later)
    assert got.get("stale") and "RBLBANK" in got["text"]
    n = len(calls)
    datasets.fetch("nse.fo_ban", store=store, now=later)          # negative cache: no call
    assert len(calls) == n
    with pytest.raises(datasets.FeedUnavailable):
        datasets.fetch("nse.asm", store=store, now=later)


def test_helper_route_says_so(store, monkeypatch):
    ds = datasets.CATALOG["nse.asm"]
    monkeypatch.setitem(datasets.CATALOG, "nse.asm",
                        datasets.Dataset(ds.name, ds.url, ds.kind, ds.ttl_s, route="helper"))
    with pytest.raises(datasets.FeedUnavailable) as exc:
        datasets.fetch("nse.asm", store=store)
    assert exc.value.reason == "helper_route_not_built"


def test_nse_facts_feed_the_risk_check(store, tmp_path, monkeypatch):
    scan = make_store(tmp_path, None)
    yday = (date.today() - timedelta(days=1)).isoformat()
    scan.upsert_bars("1d", pd.DataFrame([{"symbol": "YESBANK", "ts": f"{yday}T00:00:00+05:30",
                                          "open": 20, "high": 21, "low": 19.5, "close": 20.0,
                                          "volume": 1e7}]))
    texts = {"nse.price_bands": BANDS, "nse.fo_ban": FO_BAN}

    def fetch(name, *a, **k):
        if name not in texts:
            raise datasets.FeedUnavailable("source_error", "HTTP 403")
        return {"text": texts[name]}

    monkeypatch.setattr(datasets, "fetch", fetch)
    facts = datasets.nse_facts(["YESBANK", "RBLBANK", "ZZZ"])
    avail = {"bands": True, "asm": False, "gsm": False, "fo_ban": True, "results": False}
    assert facts["YESBANK"] == {"series": "EQ", "band_pct": 5.0, "band_upper": 21.0,
                                "band_lower": 19.0, "fo_ban": False, "_available": avail}
    missing_band = {**avail, "bands": False}
    assert facts["RBLBANK"] == {"fo_ban": True, "_available": missing_band}
    assert facts["ZZZ"] == {"fo_ban": False, "_available": missing_band}  # no row cannot clear its band check
    feeds.register_facts_provider(datasets.nse_facts)
    assert feeds.market_facts(["YESBANK"])["YESBANK"]["band_pct"] == 5.0


def test_nse_facts_empty_when_feeds_down(store, monkeypatch):
    def down(*a, **k):
        raise datasets.FeedUnavailable("source_error", "blocked")

    monkeypatch.setattr(datasets, "fetch", down)
    assert datasets.nse_facts(["SBIN"]) == {}
