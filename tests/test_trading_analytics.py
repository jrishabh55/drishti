"""Trading analytics + journal: ownership, filters, attribution, open marks,
benchmark availability, annotations and CSV export (controlled providers only)."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta

import pytest

from apps.api.desk.bars import BarsError
from apps.api.desk.market.session import NSE
from apps.api.desk.store import reset_desk_store_for_tests
from apps.api.evaluation import analytics
from apps.api.kite.store import reset_kite_store_for_tests
from apps.api.routes.evaluation import router
from tests.desk_utils import desk_client

NOW = datetime(2026, 10, 30, 11, 0, tzinfo=NSE.tz)
STRAT = {"strategy_id": "s1", "strategy_name": "ORB 15", "strategy_version": 2}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    desk = reset_desk_store_for_tests(tmp_path / "desk.sqlite")
    kite = reset_kite_store_for_tests(tmp_path / "kite.sqlite")
    marks: dict = {}
    bench: dict = {"rows": None}
    monkeypatch.setattr(analytics, "_now", lambda: NOW)
    monkeypatch.setattr(analytics, "latest_mark", lambda user_id, symbol: marks.get(symbol))

    def fake_bench():
        if bench["rows"] is None:
            raise BarsError("benchmark feed down")
        return bench["rows"]
    monkeypatch.setattr(analytics, "benchmark_closes", fake_bench)
    client, user = desk_client(router)
    return desk, kite, client, user, marks, bench


def paper(desk, user, symbol, *, status="closed", pnl=100.0, date="2026-10-28", pf="default",
          exit_time="10:30", attribution=None, ticket_id=None, qty=10, entry=100.0):
    doc = {"symbol": symbol, "status": status, "trade_date": date, "qty": qty, "stop": entry - 1,
           "portfolio_id": pf, "ticket_id": ticket_id, "interval": "5m",
           "entry_fill": {"time": "09:30", "price": entry} if status != "pending" else None}
    if status == "closed":
        doc.update(exit_fill={"time": exit_time, "price": entry + pnl / qty, "reason": "target"},
                   pnl_gross=pnl + 20, charges=20.0, pnl_net=pnl, r_net=round(pnl / qty, 2))
    doc.update(attribution or {})
    return desk.create_paper(user, doc)


def test_ownership_mode_separation_and_legacy_attribution(env):
    desk, kite, client, user, *_ = env
    paper(desk, "user_a", "SBIN", pnl=200.0)                       # legacy: no attribution
    paper(desk, "user_a", "TCS", pnl=-50.0, attribution=STRAT)
    paper(desk, "user_b", "INFY", pnl=9999.0, attribution=STRAT)   # someone else's
    kite.create_trade("user_a", {"symbol": "HDFC", "status": "closed", "trade_date": "2026-10-28",
                                 "filled_qty": 5, "entry_avg": 100.0, "exit_avg": 110.0,
                                 "exit_fill": {"price": 110.0, "time": "11:00:00"},
                                 "pnl_gross": 50.0, "charges": 5.0, "pnl_net": 45.0, "r_net": 1.0})

    out = client.get("/api/evaluation/trading-performance?mode=paper").json()
    assert out["mode"] == "paper"
    assert sorted(t["symbol"] for t in out["trades"]) == ["SBIN", "TCS"]     # no live, no user_b
    assert out["realized"]["pnl_net"] == 150.0
    by_sym = {t["symbol"]: t for t in out["trades"]}
    assert by_sym["SBIN"]["attribution"] == "manual" and by_sym["SBIN"]["strategy_id"] is None
    assert by_sym["TCS"]["strategy_name"] == "ORB 15" and by_sym["TCS"]["strategy_version"] == 2
    groups = {(g["strategy_id"], g["strategy_version"]): g for g in out["by_strategy"]}
    assert groups[(None, None)]["label"] == "Manual/legacy"
    assert groups[("s1", 2)]["realized"] == -50.0

    live = client.get("/api/evaluation/trading-performance?mode=live").json()
    assert [t["symbol"] for t in live["trades"]] == ["HDFC"] and live["realized"]["pnl_net"] == 45.0
    assert "by_portfolio" not in live or live["by_portfolio"] == []

    user["id"] = "user_b"
    assert client.get("/api/evaluation/trading-performance?mode=live").json()["trades"] == []


def test_portfolio_and_strategy_filters(env):
    desk, _, client, user, *_ = env
    pf = desk.create_portfolio("user_a", {"name": "Swing", "capital": 50_000})
    paper(desk, "user_a", "SBIN", pnl=100.0)
    paper(desk, "user_a", "TCS", pnl=300.0, pf=pf["id"], attribution=STRAT)
    paper(desk, "user_a", "INFY", pnl=-40.0, pf=pf["id"],
          attribution={**STRAT, "strategy_version": 1})

    q = "/api/evaluation/trading-performance?mode=paper"
    allp = client.get(q + "&portfolio_id=all").json()
    assert allp["realized"]["pnl_net"] == 360.0
    rows = {p["id"]: p for p in allp["by_portfolio"]}
    assert rows["default"]["realized"] == 100.0 and rows[pf["id"]]["realized"] == 260.0
    assert rows[pf["id"]]["name"] == "Swing" and rows[pf["id"]]["capital"] == 50_000
    assert allp["capital"] is None                    # no fake combined account

    one = client.get(q + f"&portfolio_id={pf['id']}").json()
    assert one["capital"] == 50_000 and one["realized"]["pnl_net"] == 260.0
    assert one["realized"]["return_pct"] == pytest.approx(0.52)
    assert client.get(q).json()["realized"]["pnl_net"] == 100.0           # default by default

    s = client.get(q + "&portfolio_id=all&strategy_id=s1").json()
    assert {t["symbol"] for t in s["trades"]} == {"TCS", "INFY"}
    assert sorted(g["strategy_version"] for g in s["by_strategy"]) == [1, 2]
    man = client.get(q + "&portfolio_id=all&strategy_id=manual").json()
    assert [t["symbol"] for t in man["trades"]] == ["SBIN"]

    assert client.get(q + "&portfolio_id=nope").status_code == 404
    other = desk.create_portfolio("user_b", {"name": "B", "capital": 1000})
    assert client.get(q + f"&portfolio_id={other['id']}").status_code == 404
    assert client.get("/api/evaluation/trading-performance?mode=both").status_code == 422


def test_attribution_falls_back_to_the_creating_ticket_only(env):
    desk, _, client, *_ = env
    t = desk.create_ticket("user_a", {"symbol": "SBIN", "status": "paper", "fingerprint": "f", **STRAT})
    paper(desk, "user_a", "SBIN", ticket_id=t["id"])
    [row] = client.get("/api/evaluation/trading-performance").json()["trades"]
    assert row["strategy_id"] == "s1" and row["attribution"] == "strategy"


def test_open_marks_use_real_price_and_freshness(env):
    desk, kite, client, _, marks, _ = env
    paper(desk, "user_a", "SBIN", status="open", qty=10, entry=100.0)
    paper(desk, "user_a", "TCS", status="open", qty=2, entry=500.0)
    paper(desk, "user_a", "INFY", status="open", qty=1, entry=50.0)
    paper(desk, "user_a", "WIPRO", status="pending", qty=1, entry=50.0)   # not a position
    marks["SBIN"] = {"price": 103.0, "source": "delayed:yahoo",
                     "as_of": (NOW - timedelta(minutes=20)).isoformat()}
    marks["TCS"] = {"price": 490.0, "source": "live:kite",
                    "as_of": (NOW - timedelta(days=1)).isoformat()}       # yesterday: stale
    out = client.get("/api/evaluation/trading-performance").json()
    by = {t["symbol"]: t for t in out["trades"]}
    assert by["SBIN"]["mark"]["status"] == "delayed" and by["SBIN"]["open_pnl"] == 30.0
    assert by["TCS"]["mark"]["status"] == "stale" and by["TCS"]["open_pnl"] is None
    assert by["INFY"]["mark"]["status"] == "unavailable" and by["INFY"]["open_pnl"] is None
    assert by["WIPRO"]["mark"] is None
    assert out["open"] == {"positions": 3, "marked": 1, "unmarked": 2, "pnl": 30.0,
                           "basis": "gross, before exit charges"}
    assert out["realized"]["pnl_net"] == 0

    kite.create_trade("user_a", {"symbol": "SBIN", "status": "protected", "trade_date": "2026-10-30",
                                 "filled_qty": 4, "held_qty": 4, "entry_avg": 101.0})
    marks["SBIN"] = {"price": 102.0, "source": "live:kite", "as_of": NOW.isoformat()}
    live = client.get("/api/evaluation/trading-performance?mode=live").json()
    assert live["trades"][0]["mark"]["status"] == "live" and live["open"]["pnl"] == 4.0


def test_benchmark_is_explicitly_available_or_not(env):
    desk, _, client, _, _, bench = env
    paper(desk, "user_a", "SBIN", pnl=1000.0, date="2026-10-27")
    paper(desk, "user_a", "TCS", pnl=500.0, date="2026-10-29")
    out = client.get("/api/evaluation/trading-performance").json()
    b = out["benchmark"]
    assert b["available"] is False and "benchmark feed down" in b["reason"] and b["return_pct"] is None

    day = lambda d: datetime(2026, 10, d, tzinfo=NSE.tz)  # noqa: E731
    bench["rows"] = [(day(24), 0, 0, 0, 20000.0, 0), (day(27), 0, 0, 0, 20100.0, 0),
                     (day(29), 0, 0, 0, 20200.0, 0)]
    b = client.get("/api/evaluation/trading-performance").json()["benchmark"]
    assert b["available"] is True and b["start"] == "2026-10-27" and b["end"] == "2026-10-29"
    assert b["return_pct"] == pytest.approx(1.0)                 # 20000 (close before) -> 20200
    assert b["book_return_pct"] == pytest.approx(1.5)            # 1500 on 100k default capital

    bench["rows"] = [(day(27), 0, 0, 0, 20100.0, 0), (day(29), 0, 0, 0, 20200.0, 0)]
    b = client.get("/api/evaluation/trading-performance").json()["benchmark"]
    assert b["available"] is False and "before 2026-10-27" in b["reason"]


def test_equity_curve_and_drawdown_over_filtered_trades(env):
    desk, _, client, *_ = env
    paper(desk, "user_a", "A", pnl=300.0, date="2026-10-27")
    paper(desk, "user_a", "B", pnl=-500.0, date="2026-10-28")
    paper(desk, "user_a", "C", pnl=100.0, date="2026-10-29")
    out = client.get("/api/evaluation/trading-performance").json()
    assert [p["pnl"] for p in out["curve"]] == [0, 300.0, -200.0, -100.0]
    assert out["stats"]["max_drawdown"] == 500.0 and out["stats"]["trades"] == 3
    assert "estimate" in out["charges_note"]


def test_annotations_are_owned_and_round_trip(env):
    desk, kite, client, user, *_ = env
    mine = paper(desk, "user_a", "SBIN")
    theirs = paper(desk, "user_b", "TCS")
    live = kite.create_trade("user_a", {"symbol": "HDFC", "status": "cancelled", "trade_date": "2026-10-28"})

    url = f"/api/evaluation/trade-journal/paper/{mine['id']}"
    r = client.put(url, json={"note": "Chased the open", "tags": ["fomo", "orb"]})
    assert r.status_code == 200 and r.json()["note"] == "Chased the open"
    [row] = client.get("/api/evaluation/trading-performance").json()["trades"]
    assert row["annotation"]["note"] == "Chased the open" and row["annotation"]["tags"] == ["fomo", "orb"]

    assert client.put(f"/api/evaluation/trade-journal/paper/{theirs['id']}",
                      json={"note": "x"}).status_code == 404
    assert client.put(f"/api/evaluation/trade-journal/live/{mine['id']}",
                      json={"note": "x"}).status_code == 404      # wrong book
    assert client.put(f"/api/evaluation/trade-journal/live/{live['id']}",
                      json={"note": "skipped"}).status_code == 200
    assert client.put(url, json={"note": "x" * 2001}).status_code == 422

    user["id"] = "user_b"                                        # B cannot read A's note
    [b_row] = client.get("/api/evaluation/trading-performance").json()["trades"]
    assert b_row["annotation"] is None

    user["id"] = "user_a"
    assert client.put(url, json={"note": "", "tags": []}).status_code == 200
    [row] = client.get("/api/evaluation/trading-performance").json()["trades"]
    assert row["annotation"] is None                             # cleared


def test_csv_export_uses_same_filters_and_neutralises_formulas(env):
    desk, _, client, *_ = env
    pf = desk.create_portfolio("user_a", {"name": "Swing", "capital": 50_000})
    t = paper(desk, "user_a", "SBIN", pnl=-25.0, attribution=STRAT)
    paper(desk, "user_a", "TCS", pf=pf["id"])
    paper(desk, "user_b", "INFY")
    client.put(f"/api/evaluation/trade-journal/paper/{t['id']}", json={"note": "=HYPERLINK(1)"})

    r = client.get("/api/evaluation/trade-journal.csv?mode=paper&strategy_id=s1&portfolio_id=all")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert [x["symbol"] for x in rows] == ["SBIN"]
    assert rows[0]["strategy_name"] == "ORB 15" and rows[0]["strategy_version"] == "2"
    assert rows[0]["pnl_net"] == "-25.0"                        # numbers stay numbers
    assert rows[0]["note"] == "'=HYPERLINK(1)"                  # text can't become a formula
    assert rows[0]["charges_basis"] == "estimated"
    assert rows[0]["portfolio_name"] == "Default"
    assert client.get("/api/evaluation/trade-journal.csv?portfolio_id=zzz").status_code == 404


def test_existing_paper_performance_endpoint_is_unchanged(env):
    desk, _, client, *_ = env
    paper(desk, "user_a", "SBIN", pnl=100.0, attribution=STRAT)
    out = client.get("/api/evaluation/paper-performance").json()
    assert set(out) == {"portfolio_id", "capital", "equity", "stats", "curve", "days",
                        "comparisons", "calibration", "unmatched"}
    assert out["equity"] == 100_100.0
