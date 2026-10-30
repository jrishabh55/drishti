"""Named paper portfolios: legacy preservation, isolation, binding, rechecks."""
from __future__ import annotations

import json
import sqlite3

import pandas as pd
import pytest

from apps.api.desk import feeds, risk
from apps.api.desk.store import DeskStore, get_desk_store, reset_desk_store_for_tests
from apps.api.routes import desk as desk_routes
from tests.desk_utils import desk_client, session_rows
from tests.scanner_utils import make_store
from tests.test_desk_trade import DAY, ist

LEVEL_PLAN = {"symbol": "SBIN", "entry": 800.0, "entry_time": "09:40", "setup": "level",
              "stop_rule": "price", "stop_param": 796, "target_param": 2}


@pytest.fixture()
def api(tmp_path, monkeypatch):
    reset_desk_store_for_tests(tmp_path / "desk.sqlite")
    scan = make_store(tmp_path, None)
    scan.upsert_instruments([{"symbol": "SBIN", "yf_symbol": "SBIN.NS", "name": "SBI",
                              "index_memberships": ["NIFTY500"], "fno": True}])
    rows = session_rows(DAY, [800.0] * 9, highs=[800.5] * 9, lows=[799.5] * 9)
    scan.upsert_bars("5m", pd.DataFrame([{"symbol": "SBIN", "ts": r[0].isoformat(), "open": r[1],
                                          "high": r[2], "low": r[3], "close": r[4],
                                          "volume": r[5]} for r in rows]))
    clock = {"now": ist(10, 0)}
    monkeypatch.setattr(desk_routes, "_now", lambda: clock["now"])
    client, user = desk_client(desk_routes.router)
    client.put("/api/desk/profile", json={"max_loss_per_trade_inr": 2000, "max_order_inr": 90000})
    feeds.clear_facts_providers_for_tests()
    return client, user, clock, scan


def _add_bars(scan, n):
    more = session_rows(DAY, [800.0] * n, highs=[800.5] * n, lows=[799.5] * n)
    scan.upsert_bars("5m", pd.DataFrame([{"symbol": "SBIN", "ts": r[0].isoformat(),
                                          "open": r[1], "high": r[2], "low": r[3],
                                          "close": r[4], "volume": r[5]} for r in more]))


# ---------------------------------------------------------------- legacy migration

LEGACY_SCHEMA = """
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
INSERT INTO meta VALUES ('schema_version', '1');
CREATE TABLE desk_tickets (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, plan_id TEXT,
    status TEXT NOT NULL, fingerprint TEXT NOT NULL, doc TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE paper_trades (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, ticket_id TEXT,
    symbol TEXT NOT NULL, status TEXT NOT NULL, trade_date TEXT NOT NULL, doc TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
"""


def test_legacy_ledger_becomes_the_default_portfolio_idempotently(tmp_path):
    path = tmp_path / "desk.sqlite"
    with sqlite3.connect(path) as c:
        c.executescript(LEGACY_SCHEMA)
        doc = {"id": "t1", "symbol": "SBIN", "status": "closed", "trade_date": "2026-10-27", "qty": 5,
               "pnl_net": 120.0}
        c.execute("INSERT INTO paper_trades VALUES ('t1','a',NULL,'SBIN','closed','2026-10-27',?,"
                  "'2026-10-27T05:00:00','2026-10-27T05:00:00')", (json.dumps(doc),))
        c.execute("INSERT INTO desk_tickets VALUES ('k1','a',NULL,'paper','fp',?,"
                  "'2026-10-27T05:00:00','2026-10-27T05:00:00')", (json.dumps({"id": "k1", "status": "paper"}),))
    DeskStore(path)
    store = DeskStore(path)                       # second open: no duplicate-column error
    assert store.schema_version() >= 2
    [t] = store.list_paper("a")
    assert t["id"] == "t1" and t["portfolio_id"] == "default" and t["pnl_net"] == 120.0
    assert store.get_ticket("k1", "a")["portfolio_id"] == "default"
    assert store.list_paper("a", portfolio_id="other") == []
    assert len(store.list_paper("a", portfolio_id=None)) == 1


# ---------------------------------------------------------------- CRUD + isolation

def test_portfolio_crud_capital_history_and_owner_isolation(api):
    client, user, _, _ = api
    lst = client.get("/api/desk/portfolios").json()
    assert [p["id"] for p in lst] == ["default"] and lst[0]["capital"] == 100000
    r = client.post("/api/desk/portfolios", json={"name": "Small ORB", "capital": 20000})
    assert r.status_code == 201
    pf = r.json()
    assert pf["name"] == "Small ORB" and pf["capital"] == 20000 and pf["id"] != "default"
    assert client.post("/api/desk/portfolios", json={"name": "small orb", "capital": 5}).status_code == 409
    assert client.post("/api/desk/portfolios", json={"name": " ", "capital": 5}).status_code == 422
    assert client.post("/api/desk/portfolios", json={"name": "x", "capital": 0}).status_code == 422
    # Rename + capital change while there is no history.
    r = client.patch(f"/api/desk/portfolios/{pf['id']}", json={"name": "Tiny", "capital": 25000})
    assert r.status_code == 200 and r.json()["name"] == "Tiny" and r.json()["capital"] == 25000
    assert r.json()["capital_history"][-1]["from"] == 20000
    assert client.patch("/api/desk/portfolios/default", json={"capital": 5}).status_code == 422
    # Another account cannot see, edit or trade from it.
    user["id"] = "user_b"
    assert [p["id"] for p in client.get("/api/desk/portfolios").json()] == ["default"]
    assert client.patch(f"/api/desk/portfolios/{pf['id']}", json={"name": "mine"}).status_code == 404
    assert client.get(f"/api/desk/paper?portfolio_id={pf['id']}").status_code == 404
    plan = client.post("/api/desk/plans", json=LEVEL_PLAN).json()
    assert client.post("/api/desk/tickets", json={"plan_id": plan["id"],
                                                  "portfolio_id": pf["id"]}).status_code == 404


def test_concurrent_duplicate_creates_make_one_portfolio(api):
    from concurrent.futures import ThreadPoolExecutor

    client, _, _, _ = api
    with ThreadPoolExecutor(4) as pool:
        codes = list(pool.map(lambda _: client.post("/api/desk/portfolios", json={
            "name": "Race", "capital": 1000}).status_code, range(4)))
    assert sorted(codes) == [201, 409, 409, 409]
    assert [p["name"] for p in client.get("/api/desk/portfolios").json()] == ["Default", "Race"]


# ---------------------------------------------------------------- independent books

def test_named_portfolio_has_independent_sizing_ledger_risk_and_performance(api):
    from apps.api.evaluation.performance import performance

    client, user, clock, scan = api
    pf = client.post("/api/desk/portfolios", json={"name": "Small", "capital": 20000}).json()
    plan = client.post("/api/desk/plans", json=LEVEL_PLAN).json()
    # Sizing uses the bound portfolio's capital (position cap 50% of capital).
    big = client.post("/api/desk/size", json={"plan_id": plan["id"]}).json()
    small = client.post("/api/desk/size", json={"plan_id": plan["id"],
                                                "portfolio_id": pf["id"]}).json()
    assert small["qty"] < big["qty"] and small["qty"] == 12
    tk = client.post("/api/desk/tickets", json={"plan_id": plan["id"],
                                                "portfolio_id": pf["id"]}).json()
    assert tk["status"] == "ready" and tk["portfolio_id"] == pf["id"]
    assert tk["order"]["portfolio_id"] == pf["id"]
    # Selection mismatch at confirm never moves the ticket.
    r = client.post(f"/api/desk/tickets/{tk['id']}/confirm",
                    json={"mode": "paper", "portfolio_id": "default"})
    assert r.status_code == 409
    # Named portfolios never go to the broker path.
    assert client.post(f"/api/desk/tickets/{tk['id']}/confirm",
                       json={"mode": "broker"}).status_code == 409
    r = client.post(f"/api/desk/tickets/{tk['id']}/confirm",
                    json={"mode": "paper", "portfolio_id": pf["id"]})
    assert r.status_code == 200 and r.json()["status"] == "paper"
    named = client.get(f"/api/desk/paper?portfolio_id={pf['id']}").json()
    default = client.get("/api/desk/paper").json()
    assert len(named["trades"]) == 1 and named["trades"][0]["portfolio_id"] == pf["id"]
    assert named["capital"] == 20000 and named["cash"] == round(20000 - 12 * 800.0, 2)
    assert default["trades"] == [] and default["cash"] == 100000
    # Duplicate/daily-trade accounting is per portfolio: default still trades SBIN.
    tk2 = client.post("/api/desk/tickets", json={"plan_id": plan["id"]}).json()
    assert tk2["status"] == "ready" and tk2.get("portfolio_id") == "default"
    tk3 = client.post("/api/desk/tickets", json={"plan_id": plan["id"],
                                                 "portfolio_id": pf["id"]}).json()
    assert tk3["status"] == "blocked"
    assert any("duplicate" in x for x in tk3["verdict"]["reasons"])
    # Capital is frozen once the portfolio has history.
    assert client.patch(f"/api/desk/portfolios/{pf['id']}", json={"capital": 50000}).status_code == 409
    assert client.patch(f"/api/desk/portfolios/{pf['id']}", json={"name": "Renamed"}).status_code == 200
    # Background refresh advances the named trade in place and keeps its binding.
    _add_bars(scan, 12)
    clock["now"] = ist(10, 16)
    desk_routes.refresh_paper(user["id"])
    [t] = get_desk_store().list_paper(user["id"], portfolio_id=pf["id"])
    assert t["status"] == "open" and t["portfolio_id"] == pf["id"]
    client.post(f"/api/desk/paper/{t['id']}/close", json={"price": 810.0})
    perf = performance(user["id"], portfolio_id=pf["id"])
    assert perf["capital"] == 20000 and perf["stats"]["trades"] == 1
    assert performance(user["id"])["stats"]["trades"] == 0


def test_fingerprint_and_default_recheck_bind_the_portfolio(api):
    client, _, _, _ = api
    order = {"symbol": "SBIN", "exchange": "NSE", "side": "buy", "qty": 1,
             "order_type": "LIMIT", "product": "MIS", "validity": "DAY", "price": 800.0,
             "stop": 796.0, "target": 808.0}
    legacy = risk.fingerprint(order)
    assert risk.fingerprint(dict(order, portfolio_id="p1")) != legacy
    # A named-portfolio order checked against the default account (the live
    # broker path's call) is always blocked.
    pf = client.post("/api/desk/portfolios", json={"name": "P", "capital": 20000}).json()
    out = desk_routes._risk_for([dict(order, portfolio_id=pf["id"])], "user_a")
    assert out["results"][0]["verdict"] == "BLOCK"
    assert any("portfolio" in r for r in out["results"][0]["reasons"])
    out = desk_routes._risk_for([dict(order, portfolio_id=pf["id"])], "user_a", pf["id"])
    assert out["results"][0]["verdict"] == "PASS"


def test_unknown_portfolio_everywhere_is_404(api):
    client, _, _, _ = api
    plan = client.post("/api/desk/plans", json=LEVEL_PLAN).json()
    for method, url, body in [
        ("post", "/api/desk/size", {"plan_id": plan["id"], "portfolio_id": "nope"}),
        ("post", "/api/desk/tickets", {"plan_id": plan["id"], "portfolio_id": "nope"}),
        ("post", "/api/desk/risk", {"orders": [{"symbol": "SBIN"}], "portfolio_id": "nope"}),
        ("get", "/api/desk/paper?portfolio_id=nope", None),
        ("get", "/api/desk/tickets?portfolio_id=nope", None),
    ]:
        r = getattr(client, method)(url, **({"json": body} if body else {}))
        assert r.status_code == 404, url


# ---------------------------------------------------------------- Telegram approval

def test_messaging_approval_rechecks_and_fills_the_bound_portfolio(api, monkeypatch, tmp_path):
    from cryptography.fernet import Fernet

    from apps.api.jobs.store import reset_store_for_tests
    from apps.api.messaging import actions, store as mstore
    from tests.test_messaging_actions import token_for

    client, user, _, _ = api
    reset_store_for_tests(tmp_path / "jobs.sqlite")
    monkeypatch.setenv("WEBAPP_KEY_ENCRYPTION_SECRET", Fernet.generate_key().decode())
    monkeypatch.setenv("MESSAGING_PUBLIC_BASE_URL", "https://app.example")
    monkeypatch.setattr(mstore, "now", lambda: ist(10, 0))
    pf = client.post("/api/desk/portfolios", json={"name": "Tele", "capital": 20000}).json()
    plan = client.post("/api/desk/plans", json=LEVEL_PLAN).json()
    tk = client.post("/api/desk/tickets", json={"plan_id": plan["id"],
                                                "portfolio_id": pf["id"]}).json()
    seen = []
    real = desk_routes._risk_for
    monkeypatch.setattr(desk_routes, "_risk_for", lambda o, u, portfolio_id="default":
                        seen.append(portfolio_id) or real(o, u, portfolio_id))
    for ch in ["telegram", "whatsapp"]:
        row = mstore.new(user["id"], ch)
        row.update(connected=True, verified=True, inbound_verified=True, approvals=True,
                   chat="42", sender="42", recipient="919876543210")
        mstore.save(row)
    actions.propose(user["id"], tk["id"])
    text = mstore.deliveries(mstore.connection(user["id"], "whatsapp")["id"])[0]["text"]
    assert "Portfolio: Tele" in text
    token = token_for(user["id"], tk["id"])
    assert actions.decide(user["id"], "telegram", token, "approve", "42")["state"] == "approved"
    assert seen and set(seen) == {pf["id"]}
    assert len(get_desk_store().list_paper(user["id"], portfolio_id=pf["id"])) == 1
    assert get_desk_store().list_paper(user["id"]) == []
