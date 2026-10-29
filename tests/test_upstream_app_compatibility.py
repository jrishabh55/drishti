"""Exercise the product runner against the real upgraded graph, without network or LLM calls."""

from collections import Counter
from copy import deepcopy

import pytest

from apps.api.integrations import graph_factory
from apps.api.jobs.bus import EventBus
from apps.api.jobs.runner import JobRunner, _CancelToken
from apps.api.jobs.store import JobStore
from apps.api.jobs.translator import ChunkTranslator
from apps.api.schemas import RunRequest
from tests import test_graph_end_to_end as core
from tradingagents.dataflows import router


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    core.offline.__wrapped__(monkeypatch, tmp_path)
    model = core.ScriptedModel(structured=True)
    monkeypatch.setattr(
        core.trading_graph, "create_tier_client", lambda *a, **k: core._Client(model)
    )
    cfg = deepcopy(core.DEFAULT_CONFIG)
    cfg.update(
        results_dir=str(tmp_path / "results"),
        data_cache_dir=str(tmp_path / "cache"),
        memory_log_path=str(tmp_path / "memory.md"),
    )
    monkeypatch.setattr(graph_factory, "DEFAULT_CONFIG", cfg)
    monkeypatch.setenv("WEBAPP_PER_USER_MEMORY_DIR", str(tmp_path / "users"))
    monkeypatch.setenv("WEBAPP_REPORTS_DIR", str(tmp_path / "reports"))
    store = JobStore(tmp_path / "runs.sqlite")
    runner = JobRunner(store=store, bus=EventBus(), concurrency=2)
    request = RunRequest(
        ticker="NVDA",
        analysis_date=core.TRADE_DATE,
        analysts=["market", "news", "social", "fundamentals"],
        research_depth=1,
        llm_provider="openai",
        shallow_thinker="offline",
        deep_thinker="offline",
        checkpoint_enabled=False,
    )

    def run():
        run_id = store.create_run(
            ticker=request.ticker,
            analysis_date=request.analysis_date,
            config=request.model_dump(),
            user_id="u1",
        )
        runner._run_safely(run_id, request, _CancelToken(), "u1")
        return store.get_run(run_id), store.replay_events(run_id)

    yield run, request, store, runner, model
    runner.shutdown()


def test_runner_surfaces_analyst_tools_and_both_manager_sections(pipeline):
    detail, events = pipeline[0]()
    assert detail.status == "completed", detail.error
    assert any(e.type == "tool.called" and e.data["name"] == "get_stock_data" for e in events)
    sections = {e.data["section"] for e in events if e.type == "report.section"}
    assert sections >= {"investment_plan", "trader_investment_plan", "final_trade_decision"}
    completed = Counter(e.data["team"] for e in events if e.type == "team.completed")
    assert completed == {"research": 1, "trading": 1, "risk": 1}
    first_report = next(i for i, e in enumerate(events) if e.type == "analyst.report")
    assert {e.data["analyst"] for e in events[:first_report] if e.type == "analyst.started"} == set(
        pipeline[1].analysts
    )
    assert detail.rating == "Overweight"


@pytest.mark.parametrize("sentinel", ["NO_DATA_AVAILABLE", "DATA_UNAVAILABLE"])
def test_missing_prices_stop_the_real_graph_before_research(pipeline, monkeypatch, sentinel):
    for method in ["get_stock_data", "get_indicators"]:
        for vendor in router.VENDOR_METHODS[method]:
            monkeypatch.setitem(
                router.VENDOR_METHODS[method],
                vendor,
                lambda *a, **k: f"{sentinel}: offline missing prices",
            )
    detail, events = pipeline[0]()
    assert detail.status == "failed", detail.error
    assert "Market data unavailable" in detail.error
    assert not any(e.type == "team.started" and e.data["team"] == "research" for e in events)
    assert not any(e.type == "run.final" for e in events)
    assert detail.final_trade_decision is None


def test_current_manager_fields_are_not_reannounced_on_resume():
    state = {
        "investment_plan": "research decision",
        "final_trade_decision": "final decision",
        "investment_debate_state": {"history": "debate", "bull_history": "bull"},
        "risk_debate_state": {"history": "risk", "aggressive_history": "risk"},
    }
    translator = ChunkTranslator("r", selected_analysts=["market"], replay_state=state)
    events = list(translator.handle_chunk(state))
    assert not [e for e in events if e.type in {"team.completed", "report.section"}]


def test_manager_sections_use_current_state_fields():
    translator = ChunkTranslator("r", selected_analysts=["market"])
    events = list(
        translator.handle_chunk(
            {
                "investment_plan": "research decision",
                "final_trade_decision": "final decision",
                "investment_debate_state": {},
                "risk_debate_state": {},
            }
        )
    )
    assert {e.data["section"] for e in events if e.type == "report.section"} == {
        "investment_plan",
        "final_trade_decision",
    }


def test_social_fetches_in_parallel_analysts_use_the_users_helper(pipeline, monkeypatch):
    from apps.api.relay import RelayConnection, reset_relay_registry_for_tests
    from apps.api.routes.relay import get_internal_tokens
    from tradingagents.dataflows import fetch_proxy

    reg = reset_relay_registry_for_tests()
    reg.register(RelayConnection("u1", lambda m: None))
    observed = []

    def fetch(*args, **kwargs):
        proxy = fetch_proxy._current_proxy()
        observed.append(get_internal_tokens().resolve(proxy.token) if proxy else None)
        return "no posts"

    monkeypatch.setattr(core.sentiment_analyst, "fetch_stocktwits_messages", fetch)
    monkeypatch.setattr(core.sentiment_analyst, "fetch_reddit_posts", fetch)
    detail, _ = pipeline[0]()
    assert detail.status == "completed", detail.error
    assert observed == ["u1", "u1"]
    assert fetch_proxy._current_proxy() is None
    reset_relay_registry_for_tests()


def test_app_resumes_a_real_interrupted_checkpoint_without_duplicate_sections(
    pipeline, monkeypatch
):
    run, request, store, runner, model = pipeline
    request.checkpoint_enabled = True
    original_count = core.ScriptedModel._count

    def crash_once(self):
        if len(self.calls) == 11:
            self.calls.append(1)
            raise SystemExit("simulated process exit")
        return original_count(self)

    monkeypatch.setattr(core.ScriptedModel, "_count", crash_once)
    with pytest.raises(SystemExit):
        run()
    detail = store.list_runs(user_id="u1")[0]
    before = store.replay_events(detail.id)
    assert detail.status == "running"
    assert store.get_checkpoint_context(detail.id) is not None
    assert store.sweep_orphaned_runs() == 1
    assert store.claim_for_resume(detail.id, user_id="u1")
    runner._run_safely(detail.id, request, _CancelToken(), "u1", resume=True)
    resumed = store.get_run(detail.id)
    assert resumed.status == "completed", resumed.error
    events = store.replay_events(detail.id)
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert events[-1].type == "run.final"
    assert len(events) > len(before)
    sections = Counter(e.data["section"] for e in events if e.type == "report.section")
    assert sections == {
        "investment_plan": 1,
        "trader_investment_plan": 1,
        "final_trade_decision": 1,
    }


def test_two_simultaneous_users_keep_their_graph_config_and_proxy(pipeline, monkeypatch):
    import threading

    from apps.api.relay import RelayConnection, reset_relay_registry_for_tests
    from apps.api.routes.relay import get_internal_tokens
    from tradingagents.dataflows import fetch_proxy
    from tradingagents.dataflows.config import get_config

    _, request, store, runner, _ = pipeline
    registry = reset_relay_registry_for_tests()
    for user in ["u1", "u2"]:
        registry.register(RelayConnection(user, lambda m: None))
    barrier = threading.Barrier(2, timeout=5)
    observed = []

    def prices(*args, **kwargs):
        barrier.wait()
        proxy = fetch_proxy._current_proxy()
        observed.append(
            (
                get_config()["memory_log_path"],
                get_internal_tokens().resolve(proxy.token) if proxy else None,
            )
        )
        return "valid offline price data"

    for vendor in router.VENDOR_METHODS["get_stock_data"]:
        monkeypatch.setitem(router.VENDOR_METHODS["get_stock_data"], vendor, prices)
    ids = []
    try:
        for user in ["u1", "u2"]:
            run_id = store.create_run(
                ticker=request.ticker,
                analysis_date=request.analysis_date,
                config=request.model_dump(),
                user_id=user,
            )
            ids.append(run_id)
            runner.submit(run_id, request, user_id=user)
        runner.executor.shutdown(wait=True)
        assert [store.get_run(run_id).status for run_id in ids] == ["completed", "completed"]
        assert sorted((path.split("/")[-2], user) for path, user in observed) == [
            ("u1", "u1"),
            ("u2", "u2"),
        ]
    finally:
        reset_relay_registry_for_tests()
