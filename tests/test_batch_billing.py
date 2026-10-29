"""Batch upkeep without an open page, uncertain credit debits, and free
deferral behind the user's own run. Fake Clerk and runner only."""
from __future__ import annotations

import asyncio
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from apps.api import clerk_users
from apps.api.evaluation import batches, loop
from tests.test_evaluation import (  # noqa: F401 — shared fixtures
    _cells, _make_due, _request, fake_clerk, fake_runner, job_store)


def _lose_patch_response(monkeypatch, fake_clerk, *, applies=True, exc=None):
    """Clerk applies the PATCH, then the answer never arrives."""
    real = clerk_users._request
    patches = []

    def req(method, path, body=None):
        if method == "PATCH":
            patches.append(body)
            if applies:
                real(method, path, body)
            raise exc or TimeoutError("read timed out")
        return real(method, path, body)
    monkeypatch.setattr(clerk_users, "_request", req)
    return patches


# ---------------------------------------------------------------- clerk debit

def test_lost_patch_response_is_uncertain_and_applied_once(fake_clerk, monkeypatch):
    fake_clerk["u"] = {"private_metadata": {"activated": True, "credits": 3}}
    patches = _lose_patch_response(monkeypatch, fake_clerk)
    with pytest.raises(clerk_users.CreditDebitUncertain):
        clerk_users.debit_credit("u")
    assert len(patches) == 1 and fake_clerk["u"]["private_metadata"]["credits"] == 2


def test_server_error_on_patch_is_uncertain_but_clerk_refusal_is_not(fake_clerk, monkeypatch):
    fake_clerk["u"] = {"private_metadata": {"activated": True, "credits": 3}}
    _lose_patch_response(monkeypatch, fake_clerk, applies=False,
                         exc=urllib.error.HTTPError("x", 502, "bad gateway", None, None))
    with pytest.raises(clerk_users.CreditDebitUncertain):
        clerk_users.debit_credit("u")
    _lose_patch_response(monkeypatch, fake_clerk, applies=False,
                         exc=urllib.error.HTTPError("x", 422, "invalid", None, None))
    with pytest.raises(urllib.error.HTTPError):        # Clerk answered no: nothing spent
        clerk_users.debit_credit("u")


def test_fetch_failure_before_the_debit_stays_an_ordinary_error(fake_clerk, monkeypatch):
    fake_clerk["u"] = {"private_metadata": {"activated": True, "credits": 3}}
    calls = []

    def req(method, path, body=None):
        calls.append(method)
        raise ConnectionError("clerk down")
    monkeypatch.setattr(clerk_users, "_request", req)
    with pytest.raises(ConnectionError):
        clerk_users.debit_credit("u")
    assert calls == ["GET"]


# ---------------------------------------------------------------- batches

def _batch(job_store, user="u", tickers=("RELIANCE.NS", "TCS.NS", "INFY.NS")):
    cells = batches.plan_cells(list(tickers), "2026-01-05", "2026-01-05", 5)
    confirmed = 0 if user == "anonymous" else len(cells)    # anonymous runs cost nothing
    return batches.create_batch(user, _request(), cells, confirmed, job_store)


def test_uncertain_debit_stops_the_queue_charges_once_and_starts_nothing(
        job_store, fake_runner, fake_clerk, monkeypatch):
    fake_clerk["u"] = {"private_metadata": {"activated": True, "credits": 10}}
    bid = _batch(job_store)
    patches = _lose_patch_response(monkeypatch, fake_clerk)
    assert batches.pump("u", job_store) == 0
    cells = _cells(job_store, bid)
    assert {c["state"] for c in cells.values()} == {"rejected"}
    assert all("could not be confirmed" in c["error"] and "Check your credit balance" in c["error"]
               for c in cells.values())
    assert len(patches) == 1 and fake_clerk["u"]["private_metadata"]["credits"] == 9
    assert fake_runner == []                                   # no AI run
    # nothing automatic charges again: not the loop, not the page poll
    assert batches.sweep(job_store) == [] and batches.pump("u", job_store) == 0
    assert len(patches) == 1 and fake_runner == []


def test_uncertain_debit_route_detail_and_unexpected_http_exception_fail_closed(
        job_store, fake_runner, fake_clerk, monkeypatch):
    fake_clerk["u"] = {"private_metadata": {"activated": True, "credits": 10}}
    from apps.api.routes import runs

    def odd(uid):
        raise HTTPException(500, "something inside the debit")
    monkeypatch.setattr(clerk_users, "debit_credit", odd)
    with pytest.raises(HTTPException) as exc:
        batches._submit(_request(), "u")
    assert exc.value.status_code == 503 and exc.value.detail["code"] == "credit_debit_unconfirmed"
    assert fake_runner == [] and runs.logger


def test_other_users_queues_are_untouched_by_a_billing_stop(
        job_store, fake_runner, fake_clerk, monkeypatch):
    fake_clerk["u"] = {"private_metadata": {"activated": True, "credits": 10}}
    fake_clerk["v"] = {"private_metadata": {"activated": True, "credits": 10}}
    _batch(job_store, "u")
    bv = _batch(job_store, "v")
    real = clerk_users._request
    _lose_patch_response(monkeypatch, fake_clerk)
    batches.pump("u", job_store)
    monkeypatch.setattr(clerk_users, "_request", real)
    assert {c["state"] for c in _cells(job_store, bv).values()} == {"queued"}
    assert batches.pump("v", job_store) == 1


def test_collision_with_own_run_is_a_free_short_deferral(job_store, fake_runner, monkeypatch):
    bid = _batch(job_store, "anonymous", ("RELIANCE.NS", "TCS.NS"))
    collide = {"on": True}
    real_submit = batches._submit

    def submit(req, uid):
        if collide["on"]:
            raise HTTPException(409, f"A run for {req.ticker} is already queued or running for this "
                                     "user. Wait for it to finish before starting another.")
        return real_submit(req, uid)
    monkeypatch.setattr(batches, "_submit", submit)
    for _ in range(batches.MAX_ATTEMPTS + 3):          # more than any retry budget
        assert batches.pump("anonymous", job_store) == 0
        _make_due(job_store, bid)
    cells = _cells(job_store, bid)
    first = cells[("RELIANCE.NS", "2026-01-05")]
    assert first["state"] == "queued" and first["attempts"] == 0
    assert "waiting for your RELIANCE.NS run" in first["error"]
    assert cells[("TCS.NS", "2026-01-05")]["next_attempt_at"] is None   # no user-wide hold
    collide["on"] = False                                               # the manual run finished
    assert batches.pump("anonymous", job_store) == 1 and len(fake_runner) == 1


def test_collision_delay_is_short(job_store, fake_runner, monkeypatch):
    bid = _batch(job_store, "anonymous", ("RELIANCE.NS",))
    monkeypatch.setattr(batches, "_submit", lambda req, uid: (_ for _ in ()).throw(
        HTTPException(409, "A run for RELIANCE.NS is already queued or running for this user.")))
    before = datetime.now(timezone.utc)
    batches.pump("anonymous", job_store)
    at = datetime.fromisoformat(_cells(job_store, bid)[("RELIANCE.NS", "2026-01-05")]["next_attempt_at"])
    assert timedelta(0) < at - before <= timedelta(seconds=31)


# ---------------------------------------------------------------- sweep / loop

def test_sweep_pumps_due_users_and_skips_cancelled_backoff_and_active(
        job_store, fake_runner, fake_clerk, monkeypatch):
    for u in ("due", "backoff", "cancelled", "active"):
        fake_clerk[u] = {"private_metadata": {"activated": True, "credits": 10}}
    _batch(job_store, "due", ("RELIANCE.NS",))
    b_back = _batch(job_store, "backoff", ("RELIANCE.NS",))
    with batches._conn(job_store) as c:
        c.execute("UPDATE eval_cells SET next_attempt_at=? WHERE batch_id=?",
                  ((datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(), b_back))
    batches.cancel_batch(_batch(job_store, "cancelled", ("RELIANCE.NS",)), "cancelled", job_store)
    _batch(job_store, "active", ("RELIANCE.NS", "TCS.NS"))
    assert batches.pump("active", job_store) == 1           # now has a queued run
    assert batches.due_users(job_store) == ["due"]
    assert batches.sweep(job_store) == ["due"]              # the real pump ran
    assert [kw["user_id"] for _, _, kw in fake_runner] == ["active", "due"]
    assert batches.due_users(job_store) == []               # due now has an active run


def test_evaluation_loop_sweeps_at_once_then_periodically_and_survives_errors(monkeypatch):
    passes = []

    def sweep():
        passes.append(1)
        if len(passes) == 2:
            raise RuntimeError("one bad pass")
        return []
    monkeypatch.setattr(batches, "sweep", sweep)

    async def go():
        task = asyncio.create_task(loop.evaluation_loop(interval=3600))
        await asyncio.sleep(0.05)
        assert passes == [1]                     # immediate first sweep after a restart
        task.cancel()
        task = asyncio.create_task(loop.evaluation_loop(interval=0.01))
        await asyncio.sleep(0.1)
        task.cancel()
    asyncio.run(go())
    assert len(passes) >= 4                      # kept going after the failing pass
    assert loop.EVALUATION_LOOP_SECONDS == 30
