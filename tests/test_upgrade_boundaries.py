"""Product upgrade boundaries: menus, cache identity and checkpoint eligibility."""

import pytest
from fastapi import HTTPException
from fastapi.responses import Response

from apps.api.jobs.store import JobStore
from apps.api.routes import runs
from apps.api.routes.config import _models_for_provider
from apps.api.schemas import RunRequest, request_hash


def request(**kw):
    return RunRequest(
        **(
            dict(
                ticker="AAPL",
                analysis_date="2026-01-09",
                analysts=["market"],
                llm_provider="openai",
                shallow_thinker="offline",
                deep_thinker="offline",
            )
            | kw
        )
    )


@pytest.mark.parametrize("provider", ["openai", "google"])
def test_cli_custom_placeholder_is_not_a_web_model(provider):
    assert "custom" not in {m.id for m in _models_for_provider(provider)}


@pytest.mark.parametrize("value", ["custom", " CUSTOM ", "", "  "])
def test_model_placeholders_are_refused_before_a_run_is_queued(value, tmp_path, monkeypatch):
    from unittest.mock import Mock

    monkeypatch.setattr(runs, "get_store", lambda: JobStore(tmp_path / "runs.sqlite"))
    monkeypatch.setattr(runs, "get_runner", lambda: Mock())
    with pytest.raises(HTTPException) as exc:
        runs.create_run(
            request(shallow_thinker=value), Response(), skip_preflight=True, user_id="u1"
        )
    assert exc.value.status_code == 400
    assert "model" in exc.value.detail.lower()


def test_core_upgrade_changes_the_result_cache_identity(monkeypatch):
    import tradingagents

    monkeypatch.setattr(tradingagents, "__version__", "0.7.1")
    before = request_hash(request())
    monkeypatch.setattr(tradingagents, "__version__", "0.8.0")
    assert request_hash(request()) != before


@pytest.mark.parametrize("version", [None, "0.3.1"])
def test_incompatible_checkpoints_are_refused_before_claim(tmp_path, monkeypatch, version):
    store = JobStore(tmp_path / "runs.sqlite")
    req = request()
    run_id = store.create_run(
        ticker=req.ticker, analysis_date=req.analysis_date, config=req.model_dump(), user_id="u1"
    )
    config = (
        {} if version is None else {"_app_engine": {"core_version": version, "stream_contract": 1}}
    )
    store.set_checkpoint_context(run_id, checkpoint_ns="u1:r", effective_config=config)
    store.mark_interrupted(run_id, "server restarted")
    monkeypatch.setattr(runs, "get_store", lambda: store)
    from unittest.mock import Mock

    monkeypatch.setattr(runs, "get_runner", lambda: Mock())
    with pytest.raises(HTTPException) as exc:
        runs.resume_run(run_id, user_id="u1")
    assert exc.value.status_code == 409
    assert "upgrade" in exc.value.detail.lower()
    assert store.get_run(run_id).status == "interrupted"


def test_a_current_checkpoint_can_be_claimed(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from apps.api.integrations.engine import engine_identity

    store = JobStore(tmp_path / "runs.sqlite")
    req = request()
    run_id = store.create_run(
        ticker=req.ticker, analysis_date=req.analysis_date, config=req.model_dump(), user_id="u1"
    )
    store.set_checkpoint_context(
        run_id, checkpoint_ns="u1:r", effective_config={"_app_engine": engine_identity()}
    )
    store.mark_interrupted(run_id, "server restarted")
    monkeypatch.setattr(runs, "get_store", lambda: store)
    monkeypatch.setattr(runs, "get_runner", lambda: Mock())
    detail = runs.resume_run(run_id, user_id="u1")
    assert detail.status == "running"
