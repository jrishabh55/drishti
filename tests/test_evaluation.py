"""Evaluation layer: decision recording, paper-book context, backtest batches,
paper performance and HTML export.

The runner is driven with a stand-in graph whose stream is scripted but whose
record_decision/_log_state are the core's own, writing a real memory log. The
batch tests go through the real create_run (cache, credits, active-run guard)
with only the worker pool and the price preflight stubbed. Scores come from
real Yahoo closes (tests/fixtures/evaluation) through the core's settlement.
"""
from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api import clerk_users
from apps.api.desk.store import reset_desk_store_for_tests
from apps.api.evaluation import batches, portfolio
from apps.api.jobs.bus import EventBus
from apps.api.jobs.runner import JobRunner, _CancelToken
from apps.api.jobs.store import reset_store_for_tests
from apps.api.schemas import RunRequest, canonicalize_request, request_hash
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.memory.log import TradingMemoryLog

FIX = Path(__file__).parent / "fixtures" / "evaluation" / "nse_closes_2026q1.json"

DECISION = "**Rating**: Buy\n\nAccumulate on dips; thesis intact."


def _request(**kw) -> RunRequest:
    base = dict(ticker="RELIANCE.NS", analysis_date="2026-01-05", analysts=["market"],
                llm_provider="openai", shallow_thinker="gpt-5.4-mini", deep_thinker="gpt-5.4")
    return RunRequest(**{**base, **kw})


def _full_state(ticker="RELIANCE.NS", date="2026-01-05"):
    return {
        "company_of_interest": ticker, "trade_date": date,
        "market_report": "Market fine.", "sentiment_report": "", "news_report": "",
        "fundamentals_report": "",
        "investment_debate_state": {"bull_history": "bull", "bear_history": "bear", "history": "h",
                                    "current_response": "c", "judge_decision": "go"},
        "investment_plan": "Plan.", "trader_investment_plan": "Trade.",
        "risk_debate_state": {"aggressive_history": "a", "conservative_history": "c",
                              "neutral_history": "n", "history": "h", "judge_decision": "ok"},
        "final_trade_decision": DECISION, "final_rating": "Buy",
    }


@pytest.fixture()
def job_store(tmp_path, monkeypatch):
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    store = reset_store_for_tests(tmp_path / "web.sqlite")
    yield store


@pytest.fixture()
def desk_store(tmp_path):
    return reset_desk_store_for_tests(tmp_path / "desk.sqlite")


# ---------------------------------------------------------------- decision recording

class _Graph:
    """Scripted stream; the core's own record_decision + _log_state."""

    record_decision = TradingAgentsGraph.record_decision
    _log_state = TradingAgentsGraph._log_state

    def __init__(self, tmp: Path, chunks, fail_record=False):
        self.config = {"results_dir": str(tmp / "results"), "memory_log_path": str(tmp / "mem.md")}
        self.memory_log = TradingMemoryLog(self.config)
        self.chunks = chunks
        self.recorded = 0
        if fail_record:
            def boom(*a, **k):
                raise OSError("disk full")
            self.record_decision = boom

    def run_settings(self):
        return {}

    def stream_run(self, graph_input, **args):
        for chunk in self.chunks:
            yield [], chunk


def _drive(store, tmp, monkeypatch, graph, run_id, request, resume=False):
    from apps.api.integrations import graph_factory

    counted = graph.record_decision

    def counting(*a, **k):
        graph.recorded += 1
        return counted(*a, **k)

    graph.record_decision = counting
    stats = SimpleNamespace(get_stats=lambda: {})
    monkeypatch.setattr(graph_factory, "build_graph_for_request",
                        lambda req, **kw: (graph, {}, {"config": {"configurable": {"thread_id": "ns"}}}, stats))
    runner = JobRunner(store=store, bus=EventBus())
    runner._run(run_id, request, _CancelToken(), "user_a", resume=resume)
    return runner


def test_completed_run_records_its_decision_once_in_the_users_log(job_store, tmp_path, monkeypatch):
    req = _request()
    run_id = job_store.create_run(ticker=req.ticker, analysis_date=req.analysis_date,
                                  config=req.model_dump(), user_id="user_a")
    graph = _Graph(tmp_path, [_full_state()])
    _drive(job_store, tmp_path, monkeypatch, graph, run_id, req)

    assert graph.recorded == 1
    entries = graph.memory_log.load_entries()
    assert [(e["ticker"], e["date"], e["rating"], e["pending"]) for e in entries] == \
        [("RELIANCE.NS", "2026-01-05", "Buy", True)]
    state_log = tmp_path / "results" / "RELIANCE.NS" / "TradingAgentsStrategy_logs" / "full_states_log_2026-01-05.json"
    assert json.loads(state_log.read_text())["investment_debate_state"]["bull_history"] == "bull"
    assert job_store.get_run(run_id).status == "completed"


def test_resume_with_partial_replayed_state_does_not_duplicate(job_store, tmp_path, monkeypatch):
    req = _request()
    run_id = job_store.create_run(ticker=req.ticker, analysis_date=req.analysis_date,
                                  config=req.model_dump(), user_id="user_a")
    _drive(job_store, tmp_path, monkeypatch, _Graph(tmp_path, [_full_state()]), run_id, req)

    # A later interruption + resume whose stream carries only the last step:
    # no company_of_interest, no debate states. Recording must still work and
    # must not add a second entry.
    job_store.mark_interrupted(run_id, "deploy")
    job_store.claim_for_resume(run_id, user_id="user_a")
    graph = _Graph(tmp_path, [{"final_trade_decision": DECISION, "final_rating": "Buy"}])
    _drive(job_store, tmp_path, monkeypatch, graph, run_id, req, resume=True)

    assert graph.recorded == 1
    assert len(graph.memory_log.load_entries()) == 1
    assert job_store.get_run(run_id).status == "completed"


def test_a_failed_recording_keeps_the_completed_report(job_store, tmp_path, monkeypatch):
    req = _request()
    run_id = job_store.create_run(ticker=req.ticker, analysis_date=req.analysis_date,
                                  config=req.model_dump(), user_id="user_a")
    _drive(job_store, tmp_path, monkeypatch, _Graph(tmp_path, [_full_state()], fail_record=True), run_id, req)

    detail = job_store.get_run(run_id)
    assert detail.status == "completed"
    assert detail.final_trade_decision == DECISION and detail.rating == "Buy"


def test_a_run_without_a_decision_records_nothing(job_store, tmp_path, monkeypatch):
    req = _request()
    run_id = job_store.create_run(ticker=req.ticker, analysis_date=req.analysis_date,
                                  config=req.model_dump(), user_id="user_a")
    state = {**_full_state(), "final_trade_decision": None, "final_rating": None}
    graph = _Graph(tmp_path, [state])
    _drive(job_store, tmp_path, monkeypatch, graph, run_id, req)
    assert graph.recorded == 0 and graph.memory_log.load_entries() == []


# ---------------------------------------------------------------- paper book

def _paper(store, user, symbol, status, qty, entry, pnl_net=None, trade_date="2026-01-05",
           exit_time="10:30", reason="target", ticket_id=None, r_gross=None, charges=20.0):
    doc = {"ticket_id": ticket_id, "symbol": symbol, "side": "buy", "qty": qty, "order_type": "LIMIT",
           "limit": entry, "stop": entry * 0.99, "target": entry * 1.02, "trade_date": trade_date,
           "status": status, "events": [], "entry_fill": {"time": "09:35", "price": entry} if status != "pending" else None,
           "exit_fill": None}
    if status == "closed":
        doc.update(exit_fill={"time": exit_time, "price": entry, "reason": reason}, pnl_net=pnl_net,
                   pnl_gross=pnl_net + charges, charges=charges, r_net=round(pnl_net / 100, 2),
                   r_gross=r_gross if r_gross is not None else round((pnl_net + charges) / 100, 2))
    return store.create_paper(user, doc)


def test_snapshot_is_the_users_own_book(desk_store):
    _paper(desk_store, "user_a", "RELIANCE", "open", 10, 1500.0)
    _paper(desk_store, "user_a", "RELIANCE", "open", 10, 1520.0)
    _paper(desk_store, "user_a", "TCS", "closed", 5, 4000.0, pnl_net=250.0)
    _paper(desk_store, "user_b", "INFY", "open", 100, 1600.0)

    a = portfolio.snapshot("user_a", desk_store)
    # 100k capital + 250 realized - 30,200 committed to open RELIANCE.
    assert a == {"cash": 70050.0, "currency": "INR",
                 "positions": [{"ticker": "RELIANCE.NS", "quantity": 20.0, "average_price": 1510.0}]}
    b = portfolio.snapshot("user_b", desk_store)
    assert [p["ticker"] for p in b["positions"]] == ["INFY.NS"]
    assert portfolio.snapshot("user_c", desk_store) == {"cash": 100000.0, "currency": "INR", "positions": []}


def test_client_sent_snapshot_is_never_trusted(desk_store):
    forged = {"cash": 1e9, "currency": "INR", "positions": []}
    assert portfolio.apply_portfolio_snapshot(_request(portfolio_snapshot=forged), "user_a").portfolio_snapshot is None
    opted = portfolio.apply_portfolio_snapshot(_request(use_paper_portfolio=True, portfolio_snapshot=forged), "user_a")
    assert opted.portfolio_snapshot["cash"] == 100000.0


def test_cache_key_includes_the_book_only_when_opted_in(desk_store):
    plain = _request()
    assert "portfolio_snapshot" not in canonicalize_request(plain)
    first = portfolio.apply_portfolio_snapshot(_request(use_paper_portfolio=True), "user_a")
    same = portfolio.apply_portfolio_snapshot(_request(use_paper_portfolio=True), "user_a")
    assert request_hash(first) == request_hash(same) != request_hash(plain)
    _paper(desk_store, "user_a", "RELIANCE", "open", 10, 1500.0)
    changed = portfolio.apply_portfolio_snapshot(_request(use_paper_portfolio=True), "user_a")
    assert request_hash(changed) != request_hash(first)


def test_graph_receives_the_frozen_book(monkeypatch):
    from apps.api.integrations import graph_factory

    seen = {}

    def fake_state(self, ticker, date, asset_type="stock", portfolio=None):
        seen["portfolio"] = portfolio
        return {"portfolio_context": portfolio.render(ticker) if portfolio else ""}

    monkeypatch.setattr(TradingAgentsGraph, "create_run_state", fake_state)
    monkeypatch.setattr("tradingagents.graph.trading_graph.resolve_instrument_identity", lambda *a, **k: {})
    snap = {"cash": 70050.0, "currency": "INR",
            "positions": [{"ticker": "RELIANCE.NS", "quantity": 20.0, "average_price": 1510.0}]}
    _g, state, _a, _s = graph_factory.build_graph_for_request(
        _request(use_paper_portfolio=True, portfolio_snapshot=snap, checkpoint_enabled=False), user_id="user_a")
    assert seen["portfolio"].cash == 70050.0
    assert "Current position in RELIANCE.NS: 20 units, average price 1,510.00" in state["portfolio_context"]
    _request_plain = _request(checkpoint_enabled=False)
    graph_factory.build_graph_for_request(_request_plain, user_id="user_a")
    assert seen["portfolio"] is None


def _client(router, user):
    app = FastAPI()

    @app.middleware("http")
    async def auth(request: Request, call_next):  # noqa: ANN001
        request.state.user_id = user["id"]
        return await call_next(request)

    app.include_router(router, prefix="/api")
    return TestClient(app)


@pytest.fixture()
def fake_runner(monkeypatch):
    submitted = []
    monkeypatch.setattr("apps.api.routes.runs.get_runner",
                        lambda: SimpleNamespace(submit=lambda rid, req, **kw: submitted.append((rid, req, kw))))
    monkeypatch.setattr("apps.api.integrations.preflight.preflight_ticker",
                        lambda t, d: SimpleNamespace(ok=True))
    return submitted


def test_opted_in_run_freezes_its_book_for_resume(job_store, desk_store):
    # What create_run does (root wires the call before request_hash): snapshot,
    # hash, persist request.model_dump() as config_json; resume rebuilds from it.
    _paper(desk_store, "user_a", "RELIANCE", "open", 10, 1500.0)
    _paper(desk_store, "user_a", "INFY", "pending", 5, 1600.0)   # an order, not a holding
    forged = {"cash": 1, "currency": "INR", "positions": []}
    req = portfolio.apply_portfolio_snapshot(
        _request(use_paper_portfolio=True, portfolio_snapshot=forged), "user_a")
    run_id = job_store.create_run(ticker=req.ticker, analysis_date=req.analysis_date,
                                  config=req.model_dump(), request_hash=request_hash(req), user_id="user_a")
    stored = job_store.get_run(run_id).config["portfolio_snapshot"]
    # Pending orders reserve cash but are not positions.
    assert [p["ticker"] for p in stored["positions"]] == ["RELIANCE.NS"]
    assert stored["cash"] == 100000 - 15000 - 8000

    # The book moves on; the run's own book does not.
    _paper(desk_store, "user_a", "TCS", "open", 1, 4000.0)
    resumed = RunRequest(**job_store.get_run(run_id).config)
    assert portfolio.context_for(resumed).cash == 77000.0
    assert request_hash(resumed) == request_hash(req)

    # Old rows without the new fields still load.
    legacy = {k: v for k, v in _request().model_dump().items()
              if k not in ("use_paper_portfolio", "portfolio_snapshot")}
    assert portfolio.context_for(RunRequest(**legacy)) is None


# ---------------------------------------------------------------- batches

@pytest.fixture()
def fake_clerk(monkeypatch):
    users: dict = {}

    def _req(method, path, body=None):
        uid = path.split("/")[2]
        if uid not in users:
            raise urllib.error.HTTPError(path, 404, "nf", None, None)
        if method == "PATCH":
            users[uid].setdefault("private_metadata", {}).update(body["private_metadata"])
        return users[uid]

    clerk_users.reset_cache_for_tests()
    monkeypatch.setattr(clerk_users, "_request", _req)
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_fake")
    yield users
    clerk_users.reset_cache_for_tests()


def _batch_body(**kw):
    settings = {k: v for k, v in _request().model_dump().items() if k not in ("ticker", "analysis_date")}
    return {"tickers": ["RELIANCE.NS", "TCS.NS"], "start_date": "2026-01-05", "end_date": "2026-01-15",
            "every_n_days": 5, "settings": settings, **kw}


def _complete(store, run_id, rating="Buy"):
    store.mark_running(run_id)
    store.mark_completed(run_id, decision_text=f"**Rating**: {rating}", rating=rating,
                         final_state={"final_trade_decision": f"**Rating**: {rating}"})


def test_batch_needs_exact_cost_confirmation_and_bills_each_cell(job_store, fake_runner, fake_clerk):
    from apps.api.routes.evaluation import router

    fake_clerk["user_1"] = {"private_metadata": {"activated": True, "credits": 10}}
    client = _client(router, {"id": "user_1"})

    q = client.post("/api/evaluation/batches/quote", json=_batch_body()).json()
    assert (q["cells"], q["max_credits"], q["credits_available"]) == (6, 6, 10)
    assert client.post("/api/evaluation/batches", json=_batch_body(confirmed_credits=5)).status_code == 409
    assert fake_runner == []

    res = client.post("/api/evaluation/batches", json=_batch_body(confirmed_credits=6))
    assert res.status_code == 201
    view = res.json()
    # One live batch run per user avoids tying up workers on the per-user lock.
    assert [c["phase"] for c in view["cells"]] == ["running"] + ["queued"] * 5
    assert fake_clerk["user_1"]["private_metadata"]["credits"] == 9
    assert {r.analysis_date for _, r, _ in fake_runner} == {"2026-01-05"}

    # A finished cell frees its ticker for the next.
    _complete(job_store, view["cells"][0]["run_id"])
    batches.pump("user_1")
    assert fake_clerk["user_1"]["private_metadata"]["credits"] == 8
    assert [(r.ticker, r.analysis_date) for _, r, _ in fake_runner][-1] == ("RELIANCE.NS", "2026-01-10")

    # Another user cannot see it.
    other = _client(router, {"id": "user_2"})
    assert other.get(f"/api/evaluation/batches/{view['id']}").status_code == 404
    assert other.get("/api/evaluation/batches").json() == []


def test_batch_refuses_more_than_the_user_can_pay(job_store, fake_runner, fake_clerk):
    from apps.api.routes.evaluation import router

    fake_clerk["user_1"] = {"private_metadata": {"activated": True, "credits": 3}}
    res = _client(router, {"id": "user_1"}).post("/api/evaluation/batches", json=_batch_body(confirmed_credits=6))
    assert res.status_code == 402 and fake_runner == []


def test_out_of_credits_mid_batch_rejects_cells_explicitly(job_store, fake_runner, fake_clerk):
    from apps.api.routes.evaluation import router

    fake_clerk["user_1"] = {"private_metadata": {"activated": True, "credits": 6}}
    client = _client(router, {"id": "user_1"})
    view = client.post("/api/evaluation/batches", json=_batch_body(confirmed_credits=6)).json()
    fake_clerk["user_1"]["private_metadata"]["credits"] = 0   # spent elsewhere
    clerk_users.reset_cache_for_tests()
    _complete(job_store, view["cells"][0]["run_id"])
    batches.pump("user_1")
    cell = client.get(f"/api/evaluation/batches/{view['id']}").json()["cells"][1]
    assert cell["phase"] == "rejected" and "credits" in cell["error"]


def test_batch_grid_is_bounded(job_store, fake_runner):
    from apps.api.routes.evaluation import router

    client = _client(router, {"id": "anonymous"})
    res = client.post("/api/evaluation/batches/quote",
                      json=_batch_body(start_date="2026-01-01", end_date="2026-03-01", every_n_days=1))
    assert res.status_code == 422 and "limit" in res.json()["detail"]
    assert client.post("/api/evaluation/batches/quote", json=_batch_body(tickers=[])).status_code == 422

    # At most MAX_OPEN_BATCHES batches may still be queueing runs.
    for _ in range(batches.MAX_OPEN_BATCHES):
        assert client.post("/api/evaluation/batches", json=_batch_body(confirmed_credits=0)).status_code == 201
    res = client.post("/api/evaluation/batches", json=_batch_body(confirmed_credits=0))
    assert res.status_code == 409 and res.json()["detail"]["code"] == "too_many_batches"


def _fixture_closes(monkeypatch, today="2026-02-14"):
    from tradingagents.memory import settlement

    data = json.loads(FIX.read_text())["closes"]
    series = {s: pd.Series(list(v.values()), index=pd.to_datetime(list(v.keys()))) for s, v in data.items()}

    def get_closes(symbol, start, end):
        s = series[symbol]
        return s[(s.index >= start) & (s.index < end)]

    monkeypatch.setattr(settlement, "get_closes", get_closes)
    monkeypatch.setattr(settlement, "_now", lambda: pd.Timestamp(today, tz="UTC"))
    return series


def test_batch_scores_only_real_completed_closes(job_store, fake_runner, monkeypatch):
    from apps.api.routes.evaluation import router

    series = _fixture_closes(monkeypatch)
    client = _client(router, {"id": "anonymous"})
    body = _batch_body(start_date="2026-01-05", end_date="2026-02-09", every_n_days=35)
    view = client.post("/api/evaluation/batches", json=body | {"confirmed_credits": 0}).json()
    # RELIANCE 01-05 Buy, TCS 01-05 Sell; their second dates (02-09) lack a full window.
    for _ in range(4):
        view = client.get(f"/api/evaluation/batches/{view['id']}").json()
        active = next(c for c in view['cells'] if c['phase'] == 'running')
        _complete(job_store, active['run_id'], 'Buy' if active['ticker']=='RELIANCE.NS' else 'Sell')
    view = client.get(f"/api/evaluation/batches/{view['id']}").json()
    _complete(job_store, view["cells"][1]["run_id"], "Buy")
    _complete(job_store, view["cells"][3]["run_id"], "Buy")

    view = _wait_for_scores(client, view["id"])
    phases = {(c["ticker"], c["date"]): c["phase"] for c in view["cells"]}
    assert phases == {("RELIANCE.NS", "2026-01-05"): "scored", ("RELIANCE.NS", "2026-02-09"): "pending",
                      ("TCS.NS", "2026-01-05"): "scored", ("TCS.NS", "2026-02-09"): "pending"}

    rel, nifty = series["RELIANCE.NS"], series["^NSEI"]
    after = rel[rel.index > "2026-01-05"]
    raw = after.iloc[5] / after.iloc[0] - 1
    bench = nifty.asof(after.index[5]) / nifty.asof(after.index[0]) - 1
    score = view["cells"][0]["score"]
    assert score["benchmark"] == "^NSEI" and score["resolved"] == after.index[5].strftime("%Y-%m-%d")
    assert score["alpha"] == pytest.approx(raw - bench)

    summary = view["summary"]
    assert (summary["scored"], summary["pending"]) == (2, 2)
    assert summary["by_rating"]["Buy"]["mean_alpha"] == pytest.approx(raw - bench)
    assert summary["by_rating"]["Buy"]["hit_rate"] == (1.0 if raw - bench > 0 else 0.0)
    assert set(summary["by_rating"]) == {"Buy", "Sell"}


def test_missing_closes_are_an_explicit_error_not_a_score(job_store, fake_runner, monkeypatch):
    from apps.api.routes.evaluation import router
    from tradingagents.memory import settlement

    monkeypatch.setattr(settlement, "_now", lambda: pd.Timestamp("2026-02-14", tz="UTC"))
    monkeypatch.setattr(settlement, "get_closes", lambda *a: (_ for _ in ()).throw(OSError("vendor down")))
    client = _client(router, {"id": "anonymous"})
    view = client.post("/api/evaluation/batches", json=_batch_body(tickers=["RELIANCE.NS"], end_date="2026-01-05")
                       | {"confirmed_credits": 0}).json()
    _complete(job_store, view["cells"][0]["run_id"])
    cell = _wait_for_scores(client, view["id"])["cells"][0]
    assert cell["phase"] == "error" and "vendor down" in cell["score"]["reason"]


# ---------------------------------------------------------------- paper performance

def test_performance_stats_curve_days_and_replay_comparison(desk_store):
    from apps.api.evaluation.performance import performance

    plan = desk_store.create_plan("user_a", {"symbol": "RELIANCE", "date": "2026-01-05", "entry": 1500.0,
                                             "stop": 1490.0, "target": 1520.0, "status": "ticketed"})
    ticket = desk_store.create_ticket("user_a", {"plan_id": plan["id"], "symbol": "RELIANCE", "status": "paper", "fingerprint": "f1"})
    tm_plan = {"symbol": "RELIANCE", "date": "2026-01-05", "entry": 1500.0, "stop": 1490.0, "target": 1520.0}
    desk_store.put_tm("user_a", "k1", {"mode": "intraday", "plan": tm_plan, "data_as_of": "2026-01-05",
                                       "mc": {"replays": 10000, "p_target": 0.4, "p_stop": 0.5,
                                              "p_neither": 0.1, "avg_R": 0.15}})
    _paper(desk_store, "user_a", "RELIANCE", "closed", 10, 1500.0, pnl_net=180.0, ticket_id=ticket["id"],
           r_gross=2.0, reason="target")
    _paper(desk_store, "user_a", "TCS", "closed", 5, 4000.0, pnl_net=-120.0, trade_date="2026-01-06",
           reason="stop")
    _paper(desk_store, "user_a", "INFY", "closed", 5, 1600.0, pnl_net=60.0, trade_date="2026-01-06",
           exit_time="14:00", reason="flat_by")
    _paper(desk_store, "user_b", "INFY", "closed", 5, 1600.0, pnl_net=-9999.0)

    out = performance("user_a", desk_store)
    assert out["stats"]["trades"] == 3 and out["stats"]["profit_factor"] == 2.0
    assert [p["equity"] for p in out["curve"]] == [100000, 100180.0, 100060.0, 100120.0]
    assert out["stats"]["max_drawdown"] == 120.0
    assert out["stats"]["max_drawdown_pct"] == pytest.approx(round(120 / 100180 * 100, 3))
    assert [(d["date"], d["trades"], d["pnl_net"]) for d in out["days"]] == \
        [("2026-01-06", 2, -60.0), ("2026-01-05", 1, 180.0)]
    by_symbol = {c["symbol"]: c for c in out["comparisons"]}
    assert by_symbol["RELIANCE"]["expected"]["p_target"] == 0.4
    assert by_symbol["RELIANCE"]["actual"]["outcome"] == "target"
    assert by_symbol["TCS"]["expected"] is None          # no replay: not invented
    assert out["unmatched"] == 2
    assert out["calibration"] == {"trades": 1, "expected_target_rate": 0.4, "actual_target_rate": 1.0,
                                  "expected_stop_rate": 0.5, "actual_stop_rate": 0.0,
                                  "expected_avg_r": 0.15, "actual_avg_r": 2.0}


def test_performance_of_an_empty_ledger(desk_store):
    from apps.api.evaluation.performance import performance

    out = performance("nobody", desk_store)
    assert out["stats"] == {"trades": 0} and out["calibration"] is None and out["days"] == []


# ---------------------------------------------------------------- HTML export

def test_html_report_is_standalone_escaped_and_owned(job_store):
    from apps.api.routes.evaluation import router

    req = _request()
    run_id = job_store.create_run(ticker=req.ticker, analysis_date=req.analysis_date,
                                  config=req.model_dump(), user_id="user_a")
    job_store.mark_running(run_id)
    state = {**_full_state(), "market_report": "<script>alert(1)</script> **bold** ![x](https://evil/x.png)"}
    job_store.mark_completed(run_id, decision_text=DECISION, rating="Buy", final_state=state)

    res = _client(router, {"id": "user_a"}).get(f"/api/runs/{run_id}/report.html")
    assert res.status_code == 200
    assert res.headers["content-security-policy"].startswith("default-src 'none'")
    assert "attachment" in res.headers["content-disposition"]
    html = res.text
    assert "<script" not in html and "&lt;script&gt;" in html
    assert "<strong>bold</strong>" in html and "<img" not in html
    assert "RELIANCE.NS" in html and "Buy" in html and "Accumulate on dips" in html
    assert "sk-" not in html
    assert _client(router, {"id": "user_b"}).get(f"/api/runs/{run_id}/report.html").status_code == 404


def test_batch_memory_is_isolated_from_normal_analysis(tmp_path):
    from apps.api.schemas import RunRequest, request_hash
    from apps.api.integrations.graph_factory import _build_config
    base=dict(ticker='RELIANCE.NS',analysis_date='2026-10-28',analysts=['market'],
              llm_provider='openai',shallow_thinker='quick',deep_thinker='deep')
    normal=RunRequest(**base)
    batch=RunRequest(**base,evaluation_batch_id='00000000-0000-0000-0000-000000000001')
    assert _build_config(normal,user_id='alice')['memory_log_path'] != _build_config(batch,user_id='alice')['memory_log_path']
    assert request_hash(normal) != request_hash(batch)


def test_batch_submits_at_most_one_cell_per_user(job_store, fake_runner):
    cells=batches.plan_cells(['RELIANCE.NS','TCS.NS'],'2026-01-05','2026-01-10',5)
    bid=batches.create_batch('anonymous',_request(),cells,0,job_store)
    assert batches.pump('anonymous',job_store)==1
    assert len(fake_runner)==1
    assert batches.pump('anonymous',job_store)==0


def test_score_failure_is_cached_until_retry_time(job_store, fake_runner, monkeypatch):
    calls=[]
    def broken(*args):
        calls.append(args)
        raise RuntimeError('vendor unavailable')
    monkeypatch.setattr(batches,'score_cell',broken)
    cells=batches.plan_cells(['RELIANCE.NS'],'2026-01-05','2026-01-05',5)
    bid=batches.create_batch('anonymous',_request(),cells,0,job_store)
    batches.pump('anonymous',job_store)
    _complete(job_store,fake_runner[0][0],'Buy')
    batches.batch_view(bid,'anonymous',job_store)
    batches.batch_view(bid,'anonymous',job_store)
    assert len(calls)==1


def _wait_for_scores(client, bid):
    import time
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        view = client.get(f"/api/evaluation/batches/{bid}").json()
        if not any((c.get("score") or {}).get("reason") == "checking real closing prices" for c in view["cells"]):
            return view
        time.sleep(0.01)
    raise AssertionError("background scoring did not settle")


def test_batch_view_never_waits_for_vendor_and_deduplicates_score_jobs(job_store, fake_runner, monkeypatch):
    import threading
    import time
    started, release = threading.Event(), threading.Event()
    calls = []
    def blocked(*args):
        calls.append(args)
        started.set()
        assert release.wait(3)
        return {"status": "scored", "raw_return": .1, "alpha": .05}
    monkeypatch.setattr(batches, "score_cell", blocked)
    base = _request()
    bid = batches.create_batch("anonymous", base, [(base.ticker, base.analysis_date)], 0, job_store)
    batches.pump("anonymous", job_store)
    view = batches.batch_view(bid, "anonymous", job_store)
    _complete(job_store, view["cells"][0]["run_id"])
    try:
        before = time.monotonic()
        for _ in range(3):
            view = batches.batch_view(bid, "anonymous", job_store, background=True)
            assert view["cells"][0]["phase"] == "pending"
        assert time.monotonic() - before < .5
        assert started.wait(1)
        assert len(calls) == 1
    finally:
        release.set()
        deadline = time.monotonic() + 2
        while any(k[1] == bid for k in batches._score_jobs) and time.monotonic() < deadline:
            time.sleep(.01)


def test_replay_calibration_matches_interval_entry_time_and_direction():
    from apps.api.evaluation.performance import _same_plan
    plan = {"symbol":"RELIANCE", "date":"2026-01-05", "entry":1500, "stop":1490,
            "target":1520, "side":"buy", "entry_time":"09:31", "interval":"1m"}
    assert _same_plan(plan, plan)
    for field, value in (("interval","5m"), ("entry_time","09:35"), ("side","sell")):
        assert not _same_plan(plan, {**plan, field:value})
