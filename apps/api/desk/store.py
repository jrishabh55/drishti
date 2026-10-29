"""SQLite persistence for the Trade Desk (data/desk.sqlite, env DESK_DB_PATH).

Separate file from the runs DB and the scanner DB. Same conventions as
scanner/store.py: WAL, per-call connections, a module singleton plus a
reset hook for tests.

Entities are JSON documents with a few indexed columns — the shapes are
owned by the pydantic models in desk/schemas.py, and SQLite only needs to
find rows by user / status. Every per-user query filters on user_id; the
routes layer turns "not yours" into 404 (never 403) so ids don't leak.

Shared (not per-user) tables hold public market data only:
``history_cache`` (Yahoo bars) and ``feed_cache`` (NSE/BSE datasets).
"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS desk_profiles (
    user_id TEXT PRIMARY KEY,
    doc TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS desk_watchlist (
    user_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (user_id, symbol)
);
CREATE TABLE IF NOT EXISTS desk_plans (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL,
    doc TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plans_user ON desk_plans (user_id, created_at);
CREATE TABLE IF NOT EXISTS desk_tm_results (
    key TEXT NOT NULL,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    doc TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS desk_tickets (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    plan_id TEXT,
    status TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    doc TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tickets_user ON desk_tickets (user_id, created_at);
CREATE TABLE IF NOT EXISTS paper_trades (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    ticket_id TEXT,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    doc TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_paper_user ON paper_trades (user_id, trade_date);
CREATE TABLE IF NOT EXISTS desk_alerts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    doc TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_active ON desk_alerts (active, user_id);
CREATE TABLE IF NOT EXISTS desk_alert_events (
    id TEXT PRIMARY KEY,
    alert_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    at TEXT NOT NULL,
    seen INTEGER NOT NULL DEFAULT 0,
    doc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alert_events_user ON desk_alert_events (user_id, at);
CREATE TABLE IF NOT EXISTS history_cache (
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    doc TEXT NOT NULL,
    PRIMARY KEY (symbol, interval)
);
CREATE TABLE IF NOT EXISTS feed_cache (
    dataset TEXT NOT NULL,
    params_hash TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    doc TEXT NOT NULL,
    PRIMARY KEY (dataset, params_hash)
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


class DeskStore:
    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)
            c.execute("INSERT INTO meta (k, v) VALUES ('schema_version', ?) "
                      "ON CONFLICT(k) DO NOTHING", (str(SCHEMA_VERSION),))

    @contextlib.contextmanager
    def _conn(self):
        conn = sqlite3.connect(self._path, timeout=15)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def schema_version(self) -> int:
        with self._conn() as c:
            row = c.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()
        return int(row["v"]) if row else 0

    # -- profile ----------------------------------------------------------
    def get_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as c:
            row = c.execute("SELECT doc FROM desk_profiles WHERE user_id=?",
                            (user_id,)).fetchone()
        return json.loads(row["doc"]) if row else None

    def put_profile(self, user_id: str, doc: Dict[str, Any]) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO desk_profiles (user_id, doc, updated_at) VALUES (?,?,?) "
                      "ON CONFLICT(user_id) DO UPDATE SET doc=excluded.doc,"
                      " updated_at=excluded.updated_at",
                      (user_id, json.dumps(doc), now_iso()))

    # -- watchlist --------------------------------------------------------
    def watchlist(self, user_id: str) -> List[str]:
        with self._conn() as c:
            rows = c.execute("SELECT symbol FROM desk_watchlist WHERE user_id=? "
                             "ORDER BY added_at", (user_id,)).fetchall()
        return [r["symbol"] for r in rows]

    def add_watch(self, user_id: str, symbol: str) -> None:
        with self._conn() as c:
            c.execute("INSERT OR IGNORE INTO desk_watchlist VALUES (?,?,?)",
                      (user_id, symbol, now_iso()))

    def remove_watch(self, user_id: str, symbol: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM desk_watchlist WHERE user_id=? AND symbol=?",
                      (user_id, symbol))

    # -- generic per-user documents ----------------------------------------
    def _insert(self, table: str, cols: Dict[str, Any]) -> None:
        names = ",".join(cols)
        marks = ",".join("?" for _ in cols)
        with self._conn() as c:
            c.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", tuple(cols.values()))

    def _get(self, table: str, row_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as c:
            row = c.execute(f"SELECT * FROM {table} WHERE id=? AND user_id=?",
                            (row_id, user_id)).fetchone()
        return _row_doc(row) if row else None

    def _update(self, table: str, row_id: str, user_id: str, cols: Dict[str, Any]) -> bool:
        sets = ",".join(f"{k}=?" for k in cols)
        with self._conn() as c:
            cur = c.execute(f"UPDATE {table} SET {sets} WHERE id=? AND user_id=?",
                            (*cols.values(), row_id, user_id))
            return cur.rowcount > 0

    def _delete(self, table: str, row_id: str, user_id: str) -> bool:
        with self._conn() as c:
            cur = c.execute(f"DELETE FROM {table} WHERE id=? AND user_id=?", (row_id, user_id))
            return cur.rowcount > 0

    # -- plans ------------------------------------------------------------
    def create_plan(self, user_id: str, doc: Dict[str, Any]) -> Dict[str, Any]:
        pid, now = new_id(), now_iso()
        doc = dict(doc, id=pid)
        self._insert("desk_plans", {"id": pid, "user_id": user_id, "symbol": doc["symbol"],
                                    "status": doc.get("status", "draft"), "doc": json.dumps(doc),
                                    "created_at": now, "updated_at": now})
        return self.get_plan(pid, user_id)  # type: ignore[return-value]

    def get_plan(self, pid: str, user_id: str) -> Optional[Dict[str, Any]]:
        return self._get("desk_plans", pid, user_id)

    def update_plan(self, pid: str, user_id: str, doc: Dict[str, Any]) -> bool:
        doc = dict(doc, id=pid)
        return self._update("desk_plans", pid, user_id, {
            "symbol": doc["symbol"], "status": doc.get("status", "draft"),
            "doc": json.dumps(doc), "updated_at": now_iso()})

    def delete_plan(self, pid: str, user_id: str) -> bool:
        return self._delete("desk_plans", pid, user_id)

    def list_plans(self, user_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM desk_plans WHERE user_id=? "
                             "ORDER BY created_at DESC LIMIT ?", (user_id, limit)).fetchall()
        return [_row_doc(r) for r in rows]

    # -- time machine results ---------------------------------------------
    def get_tm(self, user_id: str, key: str) -> Optional[Dict[str, Any]]:
        with self._conn() as c:
            row = c.execute("SELECT doc FROM desk_tm_results WHERE user_id=? AND key=?",
                            (user_id, key)).fetchone()
        return json.loads(row["doc"]) if row else None

    def put_tm(self, user_id: str, key: str, doc: Dict[str, Any]) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO desk_tm_results VALUES (?,?,?,?)",
                      (key, user_id, now_iso(), json.dumps(doc)))

    # -- tickets ----------------------------------------------------------
    def create_ticket(self, user_id: str, doc: Dict[str, Any]) -> Dict[str, Any]:
        tid, now = new_id(), now_iso()
        doc = dict(doc, id=tid)
        self._insert("desk_tickets", {"id": tid, "user_id": user_id,
                                      "plan_id": doc.get("plan_id"),
                                      "status": doc["status"], "fingerprint": doc["fingerprint"],
                                      "doc": json.dumps(doc), "created_at": now,
                                      "updated_at": now})
        return self.get_ticket(tid, user_id)  # type: ignore[return-value]

    def get_ticket(self, tid: str, user_id: str) -> Optional[Dict[str, Any]]:
        return self._get("desk_tickets", tid, user_id)

    def update_ticket(self, tid: str, user_id: str, doc: Dict[str, Any]) -> bool:
        doc = dict(doc, id=tid)
        return self._update("desk_tickets", tid, user_id, {
            "status": doc["status"], "fingerprint": doc["fingerprint"],
            "doc": json.dumps(doc), "updated_at": now_iso()})

    def transition_ticket(self, tid: str, user_id: str, from_statuses: tuple,
                          doc: Dict[str, Any]) -> bool:
        """Compare-and-set: write ``doc`` only while the stored ticket is still
        in one of ``from_statuses``. False when another request got there first."""
        doc = dict(doc, id=tid)
        marks = ",".join("?" for _ in from_statuses)
        with self._conn() as c:
            cur = c.execute(f"UPDATE desk_tickets SET status=?, fingerprint=?, doc=?, updated_at=? "
                            f"WHERE id=? AND user_id=? AND status IN ({marks})",
                            (doc["status"], doc["fingerprint"], json.dumps(doc), now_iso(),
                             tid, user_id, *from_statuses))
            return cur.rowcount == 1

    def confirm_paper(self, user_id: str, tid: str, ticket_doc: Dict[str, Any],
                      trade_doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Claim a READY ticket and create its paper trade in ONE transaction:
        either both happen or neither (no orphan trade, no double fill).
        None when the ticket was no longer ready."""
        pid, now = new_id(), now_iso()
        trade = dict(trade_doc, id=pid)
        tk = dict(ticket_doc, id=tid, status="paper", paper_trade_id=pid)
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            cur = c.execute("UPDATE desk_tickets SET status=?, doc=?, updated_at=? "
                            "WHERE id=? AND user_id=? AND status='ready'",
                            ("paper", json.dumps(tk), now, tid, user_id))
            if cur.rowcount != 1:
                c.rollback()
                return None
            c.execute("INSERT INTO paper_trades (id, user_id, ticket_id, symbol, status, "
                      "trade_date, doc, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                      (pid, user_id, tid, trade["symbol"], trade["status"], trade["trade_date"],
                       json.dumps(trade), now, now))
        return self.get_paper(pid, user_id)

    def list_tickets(self, user_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM desk_tickets WHERE user_id=? "
                             "ORDER BY created_at DESC LIMIT ?", (user_id, limit)).fetchall()
        return [_row_doc(r) for r in rows]

    # -- paper trades -----------------------------------------------------
    def create_paper(self, user_id: str, doc: Dict[str, Any]) -> Dict[str, Any]:
        pid, now = new_id(), now_iso()
        doc = dict(doc, id=pid)
        self._insert("paper_trades", {"id": pid, "user_id": user_id,
                                      "ticket_id": doc.get("ticket_id"), "symbol": doc["symbol"],
                                      "status": doc["status"], "trade_date": doc["trade_date"],
                                      "doc": json.dumps(doc), "created_at": now,
                                      "updated_at": now})
        return self.get_paper(pid, user_id)  # type: ignore[return-value]

    def get_paper(self, pid: str, user_id: str) -> Optional[Dict[str, Any]]:
        return self._get("paper_trades", pid, user_id)

    def update_paper(self, pid: str, user_id: str, doc: Dict[str, Any]) -> bool:
        doc = dict(doc, id=pid)
        return self._update("paper_trades", pid, user_id, {
            "status": doc["status"], "doc": json.dumps(doc), "updated_at": now_iso()})

    def list_paper(self, user_id: str, *, status: Optional[str] = None,
                   trade_date: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
        q, args = "SELECT * FROM paper_trades WHERE user_id=?", [user_id]
        if status:
            q += " AND status=?"
            args.append(status)
        if trade_date:
            q += " AND trade_date=?"
            args.append(trade_date)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            rows = c.execute(q, args).fetchall()
        return [_row_doc(r) for r in rows]

    def open_paper_all_users(self) -> List[Dict[str, Any]]:
        """Every open paper trade across users — for the background updater."""
        with self._conn() as c:
            rows = c.execute("SELECT * FROM paper_trades WHERE status='open'").fetchall()
        return [_row_doc(r) for r in rows]

    # -- alerts -----------------------------------------------------------
    def create_alert(self, user_id: str, doc: Dict[str, Any]) -> Dict[str, Any]:
        aid, now = new_id(), now_iso()
        doc = dict(doc, id=aid)
        self._insert("desk_alerts", {"id": aid, "user_id": user_id,
                                     "active": int(doc.get("active", True)),
                                     "doc": json.dumps(doc), "created_at": now,
                                     "updated_at": now})
        return self.get_alert(aid, user_id)  # type: ignore[return-value]

    def get_alert(self, aid: str, user_id: str) -> Optional[Dict[str, Any]]:
        return self._get("desk_alerts", aid, user_id)

    def update_alert(self, aid: str, user_id: str, doc: Dict[str, Any]) -> bool:
        doc = dict(doc, id=aid)
        return self._update("desk_alerts", aid, user_id, {
            "active": int(doc.get("active", True)), "doc": json.dumps(doc),
            "updated_at": now_iso()})

    def delete_alert(self, aid: str, user_id: str) -> bool:
        return self._delete("desk_alerts", aid, user_id)

    def list_alerts(self, user_id: str) -> List[Dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM desk_alerts WHERE user_id=? "
                             "ORDER BY created_at DESC", (user_id,)).fetchall()
        return [_row_doc(r) for r in rows]

    def active_alerts_all_users(self) -> List[Dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM desk_alerts WHERE active=1").fetchall()
        return [_row_doc(r) for r in rows]

    def add_alert_event(self, user_id: str, alert_id: str, doc: Dict[str, Any]) -> Dict[str, Any]:
        eid, now = new_id(), now_iso()
        doc = dict(doc, id=eid, alert_id=alert_id, at=now)
        with self._conn() as c:
            c.execute("INSERT INTO desk_alert_events VALUES (?,?,?,?,0,?)",
                      (eid, alert_id, user_id, now, json.dumps(doc)))
        return doc

    def list_alert_events(self, user_id: str, limit: int = 50,
                          unseen_only: bool = False) -> List[Dict[str, Any]]:
        q = "SELECT * FROM desk_alert_events WHERE user_id=?"
        if unseen_only:
            q += " AND seen=0"
        q += " ORDER BY at DESC LIMIT ?"
        with self._conn() as c:
            rows = c.execute(q, (user_id, limit)).fetchall()
        out = []
        for r in rows:
            d = json.loads(r["doc"])
            d["seen"] = bool(r["seen"])
            out.append(d)
        return out

    def mark_alert_events_seen(self, user_id: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE desk_alert_events SET seen=1 WHERE user_id=?", (user_id,))

    # -- shared public-data caches -----------------------------------------
    def get_history(self, symbol: str, interval: str) -> Optional[Dict[str, Any]]:
        with self._conn() as c:
            row = c.execute("SELECT fetched_at, doc FROM history_cache WHERE symbol=? "
                            "AND interval=?", (symbol, interval)).fetchone()
        if not row:
            return None
        return {"fetched_at": row["fetched_at"], "bars": json.loads(row["doc"])}

    def put_history(self, symbol: str, interval: str, bars: List[Any],
                    fetched_at: Optional[str] = None) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO history_cache VALUES (?,?,?,?)",
                      (symbol, interval, fetched_at or now_iso(), json.dumps(bars)))

    def get_feed(self, dataset: str, params_hash: str) -> Optional[Dict[str, Any]]:
        with self._conn() as c:
            row = c.execute("SELECT fetched_at, doc FROM feed_cache WHERE dataset=? "
                            "AND params_hash=?", (dataset, params_hash)).fetchone()
        if not row:
            return None
        return {"fetched_at": row["fetched_at"], "payload": json.loads(row["doc"])}

    def put_feed(self, dataset: str, params_hash: str, payload: Any,
                 fetched_at: Optional[str] = None) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO feed_cache VALUES (?,?,?,?)",
                      (dataset, params_hash, fetched_at or now_iso(), json.dumps(payload)))


def _row_doc(row: sqlite3.Row) -> Dict[str, Any]:
    doc = json.loads(row["doc"])
    keys = row.keys()
    for meta in ("created_at", "updated_at", "user_id"):
        if meta in keys:
            doc[meta] = row[meta]
    return doc


_store: Optional[DeskStore] = None
_lock = threading.Lock()


def get_desk_store() -> DeskStore:
    global _store
    with _lock:
        if _store is None:
            _store = DeskStore(os.environ.get("DESK_DB_PATH", "data/desk.sqlite"))
        return _store


def reset_desk_store_for_tests(path: Path | str) -> DeskStore:
    global _store
    with _lock:
        _store = DeskStore(path)
        return _store
