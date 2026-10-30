"""ORB scan, entry timer and exit planner (pure functions + API)."""
from __future__ import annotations

from datetime import date, timezone

import pandas as pd
import pytest

from apps.api.desk import entry as entry_mod, exit_plan, orb
from apps.api.desk.market.session import NSE
from apps.api.desk.store import reset_desk_store_for_tests
from apps.api.routes import desk as desk_routes
from tests.desk_utils import desk_client, session_rows
from tests.scanner_utils import make_store

DAY = date(2026, 10, 28)


def ist(h, m):
    return NSE.local(DAY, h * 60 + m).astimezone(timezone.utc)


def brk_rows(n=9):
    closes = [101.0, 101.5, 101.2, 101.8, 102.5, 102.6, 102.4, 102.9, 103.0][:n]
    highs = [101.2, 102.0, 101.5, 101.9, 102.7, 102.8, 102.6, 103.0, 103.2][:n]
    lows = [100.6, 101.0, 100.9, 101.3, 101.8, 102.2, 102.1, 102.3, 102.8][:n]
    return session_rows(DAY, closes, highs=highs, lows=lows,
                        opens=[100.8] + closes[:-1])


def wick_rows():
    closes = [101.0, 101.5, 101.2, 101.8, 101.9, 101.7, 101.6, 101.9, 101.8]
    highs = [101.2, 102.0, 101.5, 101.9, 103.0, 101.9, 101.8, 102.0, 101.9]   # wick above 102
    return session_rows(DAY, closes, highs=highs, lows=[c - 0.5 for c in closes])


def test_orb_scan_finds_close_breakouts_not_wicks_or_gappy_charts():
    gap = [r for i, r in enumerate(brk_rows()) if i != 5]
    doc = orb.scan({"BRK": brk_rows(), "WICK": wick_rows(), "GAP": gap}, ist(10, 0),
                   or_minutes=15, no_entries_after="14:45")
    assert doc["counts"] == {"universe": 3, "with_data": 3, "unusable": 1, "illiquid": 0,
                             "scanned": 2, "hits": 1}
    hit = doc["hits"][0]
    assert hit["symbol"] == "BRK" and hit["at"] == "09:40" and hit["bar_start"] == "09:35"
    assert hit["or_high"] == 102.0 and hit["or_low"] == 100.6 and hit["price"] == 102.5


def test_delayed_scan_evaluates_each_contiguous_chart_at_its_available_close():
    # Wall clock 10:15, latest available bars start at 09:55 / 09:50.
    # Normal vendor lag must not make either complete prefix unusable.
    gap = [r for i, r in enumerate(brk_rows()) if i != 5]
    doc = orb.scan({"BRK": brk_rows(), "SLOW": brk_rows(8), "GAP": gap}, ist(10, 15))
    assert doc["counts"]["scanned"] == 2
    assert doc["counts"]["unusable"] == 1
    assert [h["symbol"] for h in doc["hits"]] == ["BRK", "SLOW"]
    assert doc["data_as_of"].startswith("2026-10-28T09:50")
    assert doc["as_of"].startswith("2026-10-28T10:15")
    assert any("latest completed bar" in n for n in doc["notes"])


def test_newest_cached_close_cannot_create_a_delayed_breakout_until_a_later_bar_arrives():
    # The cached final row could have been fetched while still forming.
    doc = orb.scan({"BRK": brk_rows(5)}, ist(10, 0))
    assert doc["counts"]["scanned"] == 1
    assert doc["hits"] == []
    assert doc["data_as_of"].startswith("2026-10-28T09:30")
    # Arrival of the 09:40 bar confirms the 09:35 close, which may now trigger.
    doc = orb.scan({"BRK": brk_rows(6)}, ist(10, 0))
    assert doc["hits"][0]["bar_start"] == "09:35"
    assert doc["data_as_of"].startswith("2026-10-28T09:35")


def test_delayed_scan_excludes_stale_and_opening_range_only_charts():
    doc = orb.scan({"STALE": brk_rows(4), "FRESH": brk_rows()}, ist(10, 15))
    assert doc["counts"]["scanned"] == 1
    assert doc["counts"]["unusable"] == 1
    # The range exists, but there is no completed post-range bar to evaluate.
    doc = orb.scan({"RANGE": brk_rows(3)}, ist(9, 40))
    assert doc["counts"]["scanned"] == 0
    assert doc["counts"]["unusable"] == 1


@pytest.mark.parametrize("kwargs", [
    {"at": "10:15"}, {"day": DAY}, {"source": "live:kite"},
])
def test_explicit_replay_and_live_scans_still_require_every_bar_to_cutoff(kwargs):
    doc = orb.scan({"BRK": brk_rows()}, ist(10, 15), **kwargs)
    assert doc["counts"]["scanned"] == 0
    assert doc["counts"]["unusable"] == 1


def test_delayed_scan_does_not_treat_partial_after_close_data_as_a_full_session():
    doc = orb.scan({"BRK": brk_rows()}, ist(15, 40))
    assert doc["counts"]["scanned"] == 0
    assert doc["counts"]["unusable"] == 1
    assert any("final session bars" in note for note in doc["notes"])


def test_orb_window_refuses_before_the_first_post_range_bar_and_on_holidays():
    with pytest.raises(orb.ORBError, match="first bar after"):
        orb.scan({"BRK": brk_rows()}, ist(9, 33), or_minutes=15)
    holiday = NSE.local(date(2026, 10, 2), 600).astimezone(timezone.utc)
    with pytest.raises(orb.ORBError, match="closed"):
        orb.scan({}, holiday)


def test_orb_liquidity_filters_and_universe_fallback():
    doc = orb.scan({"BRK": brk_rows()}, ist(10, 0), min_avg_value_cr=10,
                   avg_value_cr={"BRK": 2.0})
    assert doc["counts"]["illiquid"] == 1 and not doc["hits"]
    inst = pd.DataFrame({"fno": [False, False], "index_memberships": [["NIFTY500"], []]},
                        index=pd.Index(["A", "B"], name="symbol"))
    syms, notes = orb.pick_universe(inst, "fno")
    assert syms == ["A"] and "F&O" in notes[0]


def test_entry_timer_statuses():
    now = ist(10, 0)
    tr = entry_mod.evaluate(brk_rows(), now, symbol="BRK")
    assert tr["status"] == "triggered" and tr["trigger"]["time"] == "09:40"
    assert tr["trigger"]["price"] == 102.5 and tr["opening_range"]["high"] == 102.0
    forming = entry_mod.evaluate(brk_rows(2), ist(9, 26), symbol="BRK")
    assert forming["status"] == "waiting" and forming["phase"] == "forming"
    wait = entry_mod.evaluate(wick_rows(), now, symbol="WICK")
    assert wait["status"] == "waiting" and "under the range high" in wait["reason"]
    lvl = entry_mod.evaluate(brk_rows(), now, symbol="BRK", setup="level", level=102.8)
    assert lvl["status"] == "triggered" and lvl["trigger"]["time"] == "09:55"
    # Window: nothing triggers before no_entries_before.
    early = entry_mod.evaluate(brk_rows(), now, symbol="BRK", no_entries_before="09:50")
    assert early["status"] == "triggered" and early["trigger"]["time"] == "09:50"
    # After the cutoff with no trigger: expired.
    exp = entry_mod.evaluate(wick_rows(), ist(14, 50), symbol="WICK")
    assert exp["status"] == "expired"


def test_entry_timer_volume_filter_and_errors():
    thin = entry_mod.evaluate(brk_rows(), ist(10, 0), symbol="BRK", min_volume_x=5)
    assert thin["status"] == "waiting"
    with pytest.raises(entry_mod.EntryError):
        entry_mod.evaluate([], ist(10, 0), symbol="X", setup="level")


def test_exit_plan_or_mid_matches_upstream_reel_shape_on_nse_ticks():
    plan = exit_plan.build_plan(symbol="BRK", date="2026-10-28", entry=102.5, entry_time="09:40",
                                or_high=102.0, or_low=100.6, target_param=1.0)
    assert plan["stop"] == 101.3              # (102.0 + 100.6) / 2, ₹0.01 tick
    assert plan["risk_per_share"] == 1.2 and plan["target"] == 103.7
    assert plan["entry_bar"] == 4 and plan["flat_by"] == "15:15"
    big = exit_plan.compute(1503.3, stop_rule="or_mid", or_high=1502.0, or_low=1489.95)
    assert big["stop"] == 1495.9            # 1495.975 -> down to the ₹0.10 tick
    assert big["target"] == 1518.1          # 1503.3 + 2 × 7.4, already on the ₹0.10 tick
    odd = exit_plan.compute(1503.3, stop_rule="price", stop_param=1496.2, target_param=1.5)
    assert odd["target"] == 1513.9           # 1513.95 rounds toward the entry on ₹0.10


@pytest.mark.parametrize("kw,msg", [
    ({"stop_rule": "price", "stop_param": 103}, "at or above the entry"),
    ({"stop_rule": "pct", "stop_param": 0.01}, "too tight"),
    ({"stop_rule": "or_mid"}, "needs the opening range"),
    ({"stop_rule": "price", "stop_param": 100, "target_rule": "price", "target_param": 99},
     "at or below the entry"),
])
def test_exit_plan_refusals(kw, msg):
    with pytest.raises(exit_plan.PlanError, match=msg):
        exit_plan.compute(102.5, **kw)


def test_exit_plan_flat_by_rules_and_atr():
    with pytest.raises(exit_plan.PlanError, match="square-off"):
        exit_plan.build_plan(symbol="X", date="2026-10-28", entry=100, stop_rule="pct",
                             stop_param=1, flat_by="15:25")
    with pytest.raises(exit_plan.PlanError, match="no time left"):
        exit_plan.build_plan(symbol="X", date="2026-10-28", entry=100, stop_rule="pct",
                             stop_param=1, entry_time="15:15")
    from apps.api.desk.bars import session_bars

    bars, _ = session_bars(brk_rows(), NSE, ist(10, 0))
    plan = exit_plan.build_plan(symbol="BRK", date="2026-10-28", entry=102.5, stop_rule="atr",
                                stop_param=1.5, session_bars_to_entry=bars[:5])
    assert plan["atr_bars"] == 5 and plan["stop"] < 102.5


# ---------------------------------------------------------------- API

@pytest.fixture()
def api(tmp_path, monkeypatch):
    reset_desk_store_for_tests(tmp_path / "desk.sqlite")
    scan = make_store(tmp_path, None)
    scan.upsert_instruments([{"symbol": s, "yf_symbol": f"{s}.NS", "name": s,
                              "index_memberships": ["NIFTY500"], "fno": False}
                             for s in ("BRK", "WICK")])
    rows = {"BRK": brk_rows(), "WICK": wick_rows()}
    long = pd.DataFrame([{"symbol": s, "ts": r[0].isoformat(), "open": r[1], "high": r[2],
                          "low": r[3], "close": r[4], "volume": r[5]}
                         for s, rr in rows.items() for r in rr])
    scan.upsert_bars("5m", long)
    monkeypatch.setattr(desk_routes, "_now", lambda: ist(10, 0))
    client, user = desk_client(desk_routes.router)
    client.put("/api/desk/profile", json={"min_avg_value_cr": 0, "min_price": 0})
    return client, user


def test_api_orb_entry_and_plan_lifecycle(api):
    client, user = api
    doc = client.get("/api/desk/orb").json()
    assert [h["symbol"] for h in doc["hits"]] == ["BRK"]
    assert any("F&O" in n for n in doc["notes"])
    st = client.get("/api/desk/entry/BRK").json()
    assert st["status"] == "triggered" and st["source"] == "delayed:yahoo"
    # Plan from the entry timer (no entry given) -> or_mid stop, 2R target.
    r = client.post("/api/desk/plans", json={"symbol": "BRK"})
    assert r.status_code == 201, r.text
    plan = r.json()
    assert plan["entry"] == 102.5 and plan["stop"] == 101.3 and plan["target_r"] == 2.0
    pid = plan["id"]
    # Not triggered -> 422 with the reason.
    r = client.post("/api/desk/plans", json={"symbol": "WICK"})
    assert r.status_code == 422 and "waiting" in r.json()["detail"]
    # Edit, isolation, delete.
    r = client.put(f"/api/desk/plans/{pid}", json={"symbol": "BRK", "target_param": 1.0})
    assert r.json()["target"] == 103.7
    user["id"] = "user_b"
    assert client.get(f"/api/desk/plans/{pid}").status_code == 404
    assert client.delete(f"/api/desk/plans/{pid}").status_code == 404
    user["id"] = "user_a"
    assert client.post("/api/desk/exit-plan", json={"symbol": "BRK", "entry": 102.5,
                                                    "stop_rule": "pct", "stop_param": 1,
                                                    "setup": "level"}).status_code == 200
    assert client.delete(f"/api/desk/plans/{pid}").status_code == 204


def test_api_orb_accepts_normal_delayed_feed_without_filling_internal_gaps(api, monkeypatch):
    client, _ = api
    monkeypatch.setattr(desk_routes, "_now", lambda: ist(10, 15))
    response = client.get("/api/desk/orb")
    assert response.status_code == 200
    doc = response.json()
    assert doc["counts"]["scanned"] == 2
    assert doc["counts"]["unusable"] == 0
    assert [h["symbol"] for h in doc["hits"]] == ["BRK"]


@pytest.mark.parametrize("hour,minute", [(15, 29), (15, 30), (17, 0)])
def test_automatic_scan_keeps_entry_window_results_when_vendor_has_late_session_holes(hour, minute):
    # Production 2026-10-30: 15:25 exists but 15:20 (and sometimes 15:15) do not.
    full = session_rows(DAY, [101.0] * 4 + [103.0] * 71)
    late_gap = [r for i, r in enumerate(full) if i not in (72, 73)]
    doc = orb.scan({"BRK": late_gap}, ist(hour, minute))
    assert doc["counts"]["scanned"] == 1
    assert doc["hits"][0]["at"] == "09:40"
    assert doc["data_as_of"].startswith("2026-10-28T14:40")
    assert doc["evaluation_as_of"].startswith("2026-10-28T14:45")
    assert doc["coverage"]["incomplete"] == 1
    assert {x["start"] for x in doc["coverage"]["missing_times"]} == {"15:15", "15:20"}
    assert any("14:45" in n and "entry window" in n.lower() for n in doc["notes"])


def test_entry_window_cutoff_does_not_admit_internal_holes_or_late_breakouts():
    rows = session_rows(DAY, [101.0] * 4 + [103.0] * 71)
    gap = [r for i, r in enumerate(rows) if i != 20]
    late = session_rows(DAY, [101.0] * 66 + [104.0] * 9)
    doc = orb.scan({"GAP": gap, "LATE": late}, ist(17, 0))
    assert doc["counts"]["scanned"] == 1
    assert doc["counts"]["unusable"] == 1
    assert doc["hits"] == []


@pytest.mark.parametrize("kwargs", [{"at": "15:30"}, {"day": DAY}, {"source": "live:kite"}, {"no_entries_after": None}])
def test_full_session_requests_stay_strict_with_a_current_last_bar_and_internal_gap(kwargs):
    rows = [r for i, r in enumerate(session_rows(DAY, [101.0] * 75)) if i != 73]
    doc = orb.scan({"BRK": rows}, ist(17, 0), **kwargs)
    assert doc["counts"]["scanned"] == 0
    assert doc["counts"]["unusable"] == 1
    assert any("15:20" in n for n in doc["notes"])


def test_non_grid_entry_deadline_uses_only_bars_closed_before_it():
    rows = session_rows(DAY, [101.0] * 66 + [104.0] * 9)
    doc = orb.scan({"LATE": rows}, ist(17, 0), no_entries_after="14:47")
    assert doc["counts"]["scanned"] == 1
    assert doc["hits"] == []
    assert doc["data_as_of"].startswith("2026-10-28T14:40")


def test_stale_cached_entry_deadline_row_cannot_become_a_confirmed_close():
    # 14:40 may have been fetched while forming; wall time alone is no proof.
    rows = session_rows(DAY, [101.0] * 65 + [104.0])
    for hour, minute in [(14, 46), (15, 10), (15, 29), (17, 0)]:
        doc = orb.scan({"PARTIAL": rows}, ist(hour, minute))
        assert doc["counts"]["scanned"] == 0
        assert doc["hits"] == []
