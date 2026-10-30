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


# ---- fix round 1: T3-L1 live exit reason, T3-L2 strategy-version filter

def test_live_journal_exit_reason_is_the_trade_top_level_reason(env):
    desk, kite, client, *_ = env
    kite.create_trade("user_a", {"symbol": "HDFC", "status": "closed", "trade_date": "2026-10-28",
                                 "filled_qty": 5, "entry_avg": 100.0, "exit_avg": 99.0,
                                 "exit_reason": "stop", "exit_fill": {"price": 99.0, "time": "11:00:00"},
                                 "pnl_gross": -5.0, "charges": 5.0, "pnl_net": -10.0, "r_net": -1.0})
    [row] = client.get("/api/evaluation/trading-performance?mode=live").json()["trades"]
    assert row["exit_reason"] == "stop"
    csv_rows = list(csv.DictReader(io.StringIO(client.get("/api/evaluation/trade-journal.csv?mode=live").text)))
    assert csv_rows[0]["exit_reason"] == "stop"
    paper(desk, "user_a", "SBIN")
    [p] = client.get("/api/evaluation/trading-performance").json()["trades"]
    assert p["exit_reason"] == "target"                         # paper: exit_fill.reason


def test_strategy_version_filter_python_get_and_csv(env):
    desk, _, client, user, *_ = env
    other = {"strategy_id": "s2", "strategy_name": "VWAP", "strategy_version": 2}
    paper(desk, "user_a", "TCS", pnl=300.0, attribution=STRAT)                         # s1 v2
    paper(desk, "user_a", "INFY", pnl=-40.0, attribution={**STRAT, "strategy_version": 1})
    paper(desk, "user_a", "WIPRO", pnl=10.0, attribution=other)                        # s2 v2
    paper(desk, "user_a", "SBIN", pnl=5.0)                                             # manual
    paper(desk, "user_b", "HDFC", pnl=999.0, attribution=STRAT)                        # not owned

    rows = analytics.journal_rows("user_a", portfolio_id="all", strategy_id="s1", strategy_version=1)
    assert [r["symbol"] for r in rows] == ["INFY"]
    q = "/api/evaluation/trading-performance?mode=paper&portfolio_id=all"
    v2 = client.get(q + "&strategy_id=s1&strategy_version=2").json()
    assert [t["symbol"] for t in v2["trades"]] == ["TCS"] and v2["realized"]["pnl_net"] == 300.0
    assert v2["filters"] == {"portfolio_id": "all", "strategy_id": "s1", "strategy_version": 2}
    assert [(g["strategy_id"], g["strategy_version"]) for g in v2["by_strategy"]] == [("s1", 2)]
    assert [t["symbol"] for t in client.get(q + "&strategy_id=s2&strategy_version=2").json()["trades"]] == ["WIPRO"]
    assert client.get(q + "&strategy_id=s1&strategy_version=9").json()["trades"] == []
    base = client.get(q + "&strategy_id=s1").json()                   # unversioned: unchanged
    assert {t["symbol"] for t in base["trades"]} == {"TCS", "INFY"}
    assert base["filters"]["strategy_version"] is None

    r = client.get("/api/evaluation/trade-journal.csv?portfolio_id=all&strategy_id=s1&strategy_version=1")
    assert [x["symbol"] for x in csv.DictReader(io.StringIO(r.text))] == ["INFY"]

    for bad in ("&strategy_id=s1&strategy_version=0", "&strategy_id=s1&strategy_version=-1",
                "&strategy_id=s1&strategy_version=x", "&strategy_version=1",
                "&strategy_id=manual&strategy_version=1"):
        assert client.get(q + bad).status_code == 422, bad
        assert client.get("/api/evaluation/trade-journal.csv?portfolio_id=all" + bad).status_code == 422, bad
    user["id"] = "user_b"
    assert [t["symbol"] for t in client.get(q + "&strategy_id=s1&strategy_version=2").json()["trades"]] == ["HDFC"]


def test_version_filter_matches_string_stamped_versions(env):
    desk, *_ = env
    paper(desk, "user_a", "TCS", attribution={**STRAT, "strategy_version": "3"})
    assert [r["symbol"] for r in analytics.journal_rows("user_a", strategy_id="s1", strategy_version=3)] == ["TCS"]


# ---- accounting integration: scale-out legs through desk.paper.accounting

def scaled(desk, user, symbol="SBIN", pf="default"):
    """10 bought at 100; 4 scaled out at 110 at 10:00, 6 still open."""
    return desk.create_paper(user, {
        "symbol": symbol, "side": "buy", "status": "open", "trade_date": "2026-10-30", "qty": 10,
        "open_qty": 6, "stop": 99.0, "initial_stop": 99.0, "target": 120.0, "portfolio_id": pf,
        "product": "MIS", "interval": "5m", "events": [],
        "entry_fill": {"time": "09:30", "price": 100.0},
        "exit_legs": [{"time": "10:00", "price": 110.0, "qty": 4, "reason": "scale_out"}]})


def test_open_partial_marks_remaining_qty_and_books_the_leg(env):
    from apps.api.desk import paper as paper_mod
    from apps.api.desk.profile import load_profile

    desk, _, client, _, marks, _ = env
    t = scaled(desk, "user_a")
    paper(desk, "user_a", "TCS", pnl=100.0, date="2026-10-29")
    marks["SBIN"] = {"price": 105.0, "source": "live:kite", "as_of": NOW.isoformat()}
    leg = paper_mod.accounting(t, load_profile("user_a", desk))["realized"]
    assert 0 < leg < 40.0                                          # 40 gross less leg charges

    out = client.get("/api/evaluation/trading-performance").json()
    row = next(r for r in out["trades"] if r["symbol"] == "SBIN")
    assert row["open_qty"] == 6 and row["open_pnl"] == 30.0        # (105-100) x 6, not x 10
    assert row["partial_pnl_net"] == leg
    r = out["realized"]
    assert r["pnl_net"] == round(100.0 + leg, 2) and r["partial_pnl_net"] == leg
    assert r["closed_pnl_net"] == 100.0 and r["trades"] == 1        # counts stay closed-only
    assert out["stats"]["trades"] == 1 and out["stats"]["win_rate"] == 1.0
    assert [(p["kind"], p["pnl"]) for p in out["curve"][1:]] == [
        ("closed", 100.0), ("partial", round(100.0 + leg, 2))]
    assert out["by_portfolio"][0]["realized"] == round(100.0 + leg, 2)
    csv_rows = {c["symbol"]: c for c in csv.DictReader(io.StringIO(
        client.get("/api/evaluation/trade-journal.csv").text))}
    assert csv_rows["SBIN"]["open_qty"] == "6" and float(csv_rows["SBIN"]["partial_pnl_net"]) == leg


def test_partial_then_full_close_is_counted_once(env):
    from apps.api.desk import paper as paper_mod
    from apps.api.desk.profile import load_profile

    desk, _, client, *_ = env
    t = scaled(desk, "user_a")
    closed = paper_mod.close_now(t, 105.0, NOW, load_profile("user_a", desk))
    desk.update_paper(t["id"], "user_a", closed)
    assert closed["pnl_gross"] == 70.0                              # 4 x 10 + 6 x 5

    out = client.get("/api/evaluation/trading-performance").json()
    row = out["trades"][0]
    assert row["status"] == "closed" and row["open_qty"] == 0 and row["partial_pnl_net"] == 0
    assert out["realized"]["pnl_net"] == closed["pnl_net"]          # not leg + pnl_net
    assert out["realized"]["partial_pnl_net"] == 0 and out["realized"]["trades"] == 1
    assert out["realized"]["charges"] == closed["charges"]
    # One point per booked leg; the closed trade's legs end exactly on its stored pnl_net.
    assert [p["kind"] for p in out["curve"][1:]] == ["partial", "closed"]
    assert out["curve"][-1]["pnl"] == closed["pnl_net"]
    assert out["open"]["positions"] == 0


def test_paper_performance_endpoint_stays_closed_only_with_open_partials(env):
    desk, _, client, *_ = env
    scaled(desk, "user_a")
    paper(desk, "user_a", "TCS", pnl=100.0)
    out = client.get("/api/evaluation/paper-performance").json()
    assert out["equity"] == 100_100.0 and out["stats"]["trades"] == 1
    assert len(out["curve"]) == 2


def test_curve_books_each_leg_at_its_time_so_drawdown_is_real(env):
    """10@100: 4 scaled out at 110 (10:00); another trade loses 30 (10:30);
    the last 6 exit at 95 (11:00). Coarse one-point curves showed 30 drawdown."""
    from apps.api.desk import paper as paper_mod
    from apps.api.desk.profile import load_profile

    desk, _, client, *_ = env
    prof = load_profile("user_a", desk)
    t = scaled(desk, "user_a")
    leg = paper_mod.accounting(t, prof)["realized"]
    closed = paper_mod.close_now(t, 95.0, NSE.local(datetime(2026, 10, 30).date(), 11 * 60), prof)
    desk.update_paper(t["id"], "user_a", closed)
    paper(desk, "user_a", "TCS", pnl=-30.0, date="2026-10-30", exit_time="10:30")

    out = client.get("/api/evaluation/trading-performance").json()
    assert leg == 39.55 and closed["pnl_net"] == 8.93
    assert [(p["at"], p["pnl"], p["kind"]) for p in out["curve"]] == [
        (None, 0, None), ("2026-10-30 10:00", 39.55, "partial"),
        ("2026-10-30 10:30", 9.55, "closed"), ("2026-10-30 11:00", -21.07, "closed")]
    assert out["stats"]["max_drawdown"] == 60.62
    assert out["realized"]["pnl_net"] == round(8.93 - 30, 2)      # counted once
    events = paper_mod.realized_events(closed, prof)
    assert round(sum(e["pnl_net"] for e in events), 2) == closed["pnl_net"]
    assert round(sum(e["charges"] for e in events), 2) == closed["charges"]


def test_legacy_closed_trade_is_one_event_at_its_exit():
    from apps.api.desk import paper as paper_mod
    from apps.api.desk.profile import DeskProfile

    t = {"status": "closed", "qty": 10, "entry_fill": {"time": "09:30", "price": 100.0},
         "exit_fill": {"time": "10:30", "price": 101.0, "reason": "target"},
         "pnl_net": 7.5, "charges": 2.5}
    assert paper_mod.realized_events(t, DeskProfile()) == [
        {"time": "10:30", "qty": 10, "reason": "target", "pnl_net": 7.5, "charges": 2.5}]
    assert paper_mod.realized_events({"status": "cancelled"}, DeskProfile()) == []
