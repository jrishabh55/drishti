"""Regressions for the review of 1403b10: 1m history and replay, Telegram
retry discovery and expiry, provider-name policy, resume tier checks, 1m
scanner rows."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
from cryptography.fernet import Fernet

from apps.api.desk import bars as bars_mod, bars_source, timemachine as tm
from apps.api.desk.market.session import NSE
from apps.api.desk.store import reset_desk_store_for_tests
from apps.api.jobs.store import reset_store_for_tests
from apps.api.schemas import RunRequest
from tests.desk_utils import random_walk_sessions, trading_days
from tests.scanner_utils import make_store

ONE = replace(NSE, bar_min=1)


def _frame(rows):
    idx = pd.DatetimeIndex([r[0] for r in rows])
    return pd.DataFrame({"Open": [r[1] for r in rows], "High": [r[2] for r in rows],
                         "Low": [r[3] for r in rows], "Close": [r[4] for r in rows],
                         "Volume": [r[5] for r in rows]}, index=idx)


def _plan(day: date):
    return tm.normalize_intraday_plan({"symbol": "RELIANCE", "date": day.isoformat(), "entry": 1000,
                                       "stop": 995, "target": 1010, "entry_time": "09:31"}, spec=ONE)


# ---------------------------------------------------------------- (1) 1m history

def test_one_minute_replay_runs_on_twenty_plus_real_sessions():
    day = date(2026, 10, 28)
    rows = random_walk_sessions(day, 24, start_price=1000.0, spec=ONE)
    plan = _plan(day)
    # Put the entry inside the entry bar's range so the plan is coherent.
    entry_bar = [r for r in rows if r[0] == ONE.local(day, ONE.bar_start_min(plan["entry_bar"]))][0]
    plan.update(entry=round(entry_bar[4], 2), stop=round(entry_bar[4] - 3, 2), target=round(entry_bar[4] + 6, 2))
    out = tm.run_intraday(rows, plan, spec=ONE, now=ONE.local(date(2026, 10, 29), 600),
                          replays=500, seed=3)
    mc = out["mc"]
    assert mc["pool_sessions"] >= 20 and mc["replays"] == 500
    assert abs(mc["p_target"] + mc["p_stop"] + mc["p_neither"] - 1) < 1e-3
    assert out["plan"]["entry_time"] == "09:31"
    assert out["data"]["interval"] == "1m"


def test_yahoo_sized_one_minute_history_is_honestly_unavailable():
    day = date(2026, 10, 28)
    rows = random_walk_sessions(day, 6, spec=ONE)
    with pytest.raises(tm.TMError) as err:
        tm.run_intraday(rows, _plan(day), spec=ONE, now=ONE.local(date(2026, 10, 29), 600), replays=100)
    msg = str(err.value)
    assert "needs at least 20" in msg and "1-minute" in msg and "Kite" in msg and "5-minute" in msg


def test_one_minute_history_accumulates_real_fetches(tmp_path, monkeypatch):
    store = reset_desk_store_for_tests(tmp_path / "desk.sqlite")
    days = trading_days(date(2026, 10, 28), 12)
    rows = random_walk_sessions(days[-1], 12, spec=ONE)
    by_day = lambda ds: [r for r in rows if r[0].astimezone(NSE.tz).date() in ds]  # noqa: E731
    windows = iter([by_day(days[:6]), by_day(days[6:])])   # two Yahoo-sized windows
    monkeypatch.setattr(bars_mod, "_download", lambda sym, interval, start: _frame(next(windows)))

    first = bars_mod.fetch_history("RELIANCE", "1m", now=datetime(2026, 10, 20, tzinfo=timezone.utc), store=store)
    merged = bars_mod.fetch_history("RELIANCE", "1m", now=datetime(2026, 10, 29, tzinfo=timezone.utc), store=store)
    assert len(first) == 6 * ONE.n_bars
    assert len(merged) == len(rows) == 12 * ONE.n_bars          # real bars only, nothing filled in
    assert [r[0] for r in merged] == sorted(r[0] for r in rows)
    assert len(store.get_history("RELIANCE.NS", "1m")["bars"]) == len(rows)
    # 5m keeps replacing (Yahoo's 60 days already cover the pool).
    assert "5m" not in bars_mod.ACCUMULATE_DAYS


# ---------------------------------------------------------------- (5) 1m scanner rows

def test_one_minute_session_rows_are_pruned_and_leave_scanner_panels_alone(tmp_path, monkeypatch):
    reset_desk_store_for_tests(tmp_path / "desk.sqlite")
    scanner = make_store(tmp_path, None)
    day = date(2026, 10, 28)
    two_days = random_walk_sessions(day, 2, spec=ONE)
    monkeypatch.setattr(bars_mod, "fetch_history", lambda sym, interval, now=None: two_days)
    with scanner._conn() as c:
        c.execute("INSERT INTO meta (k, v) VALUES ('bar_version', '7') "
                  "ON CONFLICT(k) DO UPDATE SET v='7'")
        before = c.execute("SELECT v FROM meta WHERE k='bar_version'").fetchone()["v"]
    series = bars_source.get_session_rows("u", "RELIANCE", "1m", now=ONE.local(date(2026, 10, 29), 600))
    assert len(series.rows) == 400
    with scanner._conn() as c:
        assert c.execute("SELECT COUNT(*) n FROM bars WHERE timeframe='1m'").fetchone()["n"] == 400
        assert c.execute("SELECT v FROM meta WHERE k='bar_version'").fetchone()["v"] == before


# ---------------------------------------------------------------- (2) notifications

@pytest.fixture()
def notif(tmp_path, monkeypatch):
    reset_store_for_tests(tmp_path / "jobs.sqlite")
    desk = reset_desk_store_for_tests(tmp_path / "desk.sqlite")
    monkeypatch.setenv("WEBAPP_KEY_ENCRYPTION_SECRET", Fernet.generate_key().decode())
    from apps.api.desk import notifications

    return notifications, desk


def _event(desk, uid, at: datetime, msg="RELIANCE crossed 1500"):
    ev = desk.add_alert_event(uid, "a1", {"symbol": "RELIANCE", "type": "above", "message": msg})
    with desk._conn() as c:
        c.execute("UPDATE desk_alert_events SET at=?, doc=? WHERE id=?",
                  (at.isoformat(), json.dumps({**ev, "at": at.isoformat()}), ev["id"]))
    return {**ev, "at": at.isoformat()}


def test_failed_send_is_found_by_the_sweep_without_active_alerts(notif, monkeypatch):
    notifications, desk = notif
    notifications.configure("a", "123:tok", "42", True)
    notifications.configure("b", "123:tok", "43", True)
    now = datetime.now(timezone.utc)
    ev = _event(desk, "a", now)   # after opt-in, inside the expiry window
    monkeypatch.setattr(notifications, "_send", lambda *a: (_ for _ in ()).throw(OSError("offline")))
    assert not notifications.deliver("a", ev)
    assert desk.active_alerts_all_users() == [] and desk.open_paper_all_users() == []

    assert notifications.pending_users() == {"a"}     # b has nothing pending
    from apps.api.desk import loop
    from apps.api.routes import desk as desk_routes

    swept = []
    monkeypatch.setattr(desk_routes, "refresh_paper", lambda uid: None)
    monkeypatch.setattr(desk_routes, "evaluate_alerts", lambda uid: swept.append(uid))
    loop.sweep()
    assert swept == ["a"]

    sent = []
    monkeypatch.setattr(notifications, "_send", lambda token, chat, text: sent.append((chat, text)))
    notifications.retry_recent("a")
    assert sent == [("42", "RELIANCE crossed 1500")]
    assert notifications.pending_users() == set()


def test_stale_alerts_expire_instead_of_arriving_late(notif, monkeypatch):
    notifications, desk = notif
    sent = []
    monkeypatch.setattr(notifications, "_send", lambda token, chat, text: sent.append(text))
    # Opted in long ago; one alert from three hours ago, one fresh.
    notifications.configure("a", "123:tok", "42", True)
    now = datetime.now(timezone.utc)
    old = _event(desk, "a", now - timedelta(hours=3), "old news")
    _event(desk, "a", now - timedelta(minutes=1), "fresh")
    doc = notifications._config("a")
    doc["enabled_since"] = (now - timedelta(days=1)).isoformat()
    from apps.api import user_keys

    notifications.get_store().set_user_key(
        "a", "telegram", user_keys._fernet().encrypt(json.dumps(doc).encode()).decode())

    notifications.retry_recent("a")
    assert sent == ["fresh"]
    assert not notifications.deliver("a", old)
    assert sent == ["fresh"]


# ---------------------------------------------------------------- (3) provider policy

def _req(**kw):
    base = dict(ticker="RELIANCE.NS", analysis_date="2026-01-05", analysts=["market"],
                llm_provider="openai", shallow_thinker="m", deep_thinker="m")
    return RunRequest(**{**base, **kw})


def test_policy_and_batch_quote_normalise_provider_names(monkeypatch):
    from apps.api import clerk_users
    from apps.api.evaluation import batches
    from apps.api.integrations.product_providers import providers_for, uses_server_credit

    assert providers_for(_req(llm_provider=" OpenAI ")) == {"openai"}
    assert uses_server_credit(_req(llm_provider="OpenAI"))
    assert uses_server_credit(_req(llm_provider="anthropic", deep_think_provider="OPENAI"))
    assert not uses_server_credit(_req(llm_provider="Anthropic"))

    monkeypatch.setattr(clerk_users, "enabled", lambda: True)
    monkeypatch.setattr(clerk_users, "get_gate", lambda uid: SimpleNamespace(credits=50))
    cells = [("RELIANCE.NS", "2026-01-05"), ("TCS.NS", "2026-01-05")]
    assert batches.quote(_req(llm_provider="OpenAI"), cells, "user_1")["max_credits"] == 2
    mixed = _req(llm_provider="anthropic", deep_think_provider="openai")
    assert batches.quote(mixed, cells, "user_1")["max_credits"] == 2
    assert batches.quote(_req(llm_provider="anthropic"), cells, "user_1")["max_credits"] == 0


# ---------------------------------------------------------------- (4) resume

def test_resume_checks_every_tier_before_claiming(tmp_path, monkeypatch):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from apps.api.integrations import engine
    from apps.api.routes import runs

    monkeypatch.setenv("WEBAPP_KEY_ENCRYPTION_SECRET", Fernet.generate_key().decode())
    store = reset_store_for_tests(tmp_path / "jobs.sqlite")
    req = _req(llm_provider="openai", deep_think_provider="anthropic")
    run_id = store.create_run(ticker=req.ticker, analysis_date=req.analysis_date,
                              config=req.model_dump(), user_id="u")
    store.set_checkpoint_context(run_id, checkpoint_ns="ns",
                                 effective_config={"_app_engine": engine.engine_identity()})
    store.mark_interrupted(run_id, "deploy")
    submitted = []
    monkeypatch.setattr(runs, "get_runner", lambda: SimpleNamespace(submit=lambda *a, **k: submitted.append(a)))

    app = FastAPI()

    @app.middleware("http")
    async def auth(request: Request, call_next):  # noqa: ANN001
        request.state.user_id = "u"
        return await call_next(request)

    app.include_router(runs.router, prefix="/api")
    client = TestClient(app)

    res = client.post(f"/api/runs/{run_id}/resume")
    assert res.status_code == 409 and "Anthropic" in res.json()["detail"]
    assert store.get_run(run_id).status == "interrupted" and submitted == []

    from apps.api.integrations import product_providers

    product_providers.save_key("u", "anthropic", "sk-ant-test-key")
    assert client.post(f"/api/runs/{run_id}/resume").status_code == 200
    assert len(submitted) == 1


def test_time_machine_requires_complete_replay_window_not_unused_closing_bars():
    day = date(2026, 10, 28)
    rows = random_walk_sessions(day, 25, start_price=1000.0)
    # Recorded RELIANCE failure shape: Yahoo has no continuous bars at 15:20.
    # A 15:15 exit does not use them. Never fill any missing OHLCV.
    rows = [r for r in rows if r[0].astimezone(NSE.tz).strftime("%H:%M") < "15:15"]
    plan = tm.normalize_intraday_plan({"symbol":"RELIANCE", "date":day.isoformat(),
        "entry":1000, "stop":995, "target":1010, "entry_time":"09:35"})
    out = tm.run_intraday(rows, plan, until="15:15", now=NSE.local(date(2026,10,29),600), replays=100)
    assert out["mc"]["pool_sessions"] >= 20
    assert out["mc"]["until"] == "15:15"
    assert out["mc"]["band_times"][-1] == "15:15"
    # The same history cannot support a later exit, or a missing interior bar.
    with pytest.raises(tm.TMError, match="needs at least 20"):
        tm.run_intraday(rows, plan, until="15:30", now=NSE.local(date(2026,10,29),600), replays=100)
    gapped = [r for r in rows if r[0].astimezone(NSE.tz).strftime("%H:%M") != "11:45"]
    with pytest.raises(tm.TMError, match="needs at least 20"):
        tm.run_intraday(gapped, plan, until="15:15", now=NSE.local(date(2026,10,29),600), replays=100)


def test_recorded_reliance_yahoo_history_replays_without_auction_bar_interpolation():
    import gzip
    from pathlib import Path
    raw = json.loads(gzip.decompress((Path(__file__).parent / "fixtures/desk/sources/yahoo_RELIANCE_5m_20261029.json.gz").read_bytes()))
    rows = bars_mod._deserialize(raw["bars"])
    day = date(2026, 10, 29)
    plan = tm.normalize_intraday_plan({"symbol":"RELIANCE", "date":day.isoformat(),
        "entry":1400, "stop":1395, "target":1410, "entry_time":"09:35"})
    out = tm.run_intraday(rows, plan, until="15:15", now=NSE.local(day,1000), replays=100)
    assert out["mc"]["pool_sessions"] == 38
    assert out["mc"]["replays"] == 100
    assert "2026-10-15" in {d["date"] for d in out["data"]["short_sessions_left_out"]}


def test_circuit_reference_uses_latest_previous_day_even_when_replay_window_has_a_gap():
    day = date(2026, 10, 28)
    rows = random_walk_sessions(day, 25, start_price=1000.0)
    prior_day = date(2026, 10, 27)
    latest = [r for r in rows if r[0].astimezone(NSE.tz).date() == prior_day][-1]
    rows = [r for r in rows if not (r[0].astimezone(NSE.tz).date() == prior_day
            and r[0].astimezone(NSE.tz).strftime("%H:%M") == "11:45")]
    plan = tm.normalize_intraday_plan({"symbol":"RELIANCE", "date":day.isoformat(),
        "entry":1000, "stop":995, "target":1010, "entry_time":"09:35"})
    out = tm.run_intraday(rows, plan, until="15:15", now=NSE.local(date(2026,10,29),600),
                          circuit_pct=5, replays=100)
    assert out["mc"]["circuit_reference"]["date"] == prior_day.isoformat()
    assert out["mc"]["circuit_reference"]["close"] == latest[4]
    assert any("official auction close" in w for w in out["warnings"])
