"""SQLite persistence for the Kite integration (data/kite.sqlite, env KITE_DB_PATH).

Same conventions as desk/store.py: WAL, per-call connections, singleton plus
a reset hook for tests, JSON documents with a few indexed columns. Every
secret, control and order read filters on user_id. Separate explicit grants
and recipient selections authorize market-data reads without sharing secrets.

Secrets (the Kite app's api_secret, the daily access_token) are stored only
as Fernet ciphertext — see kite/auth.py. Nothing here is market data: live
ticks/bars never touch disk.
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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kite_secrets (
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    ciphertext TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, kind)
);
CREATE TABLE IF NOT EXISTS kite_login_states (
    nonce TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kite_control (
    user_id TEXT PRIMARY KEY,
    doc TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kite_trades (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    doc TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kite_trades_user ON kite_trades (user_id, created_at);
CREATE TABLE IF NOT EXISTS kite_strategies (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    doc TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kite_strategies_user ON kite_strategies (user_id, created_at);
CREATE TABLE IF NOT EXISTS kite_strategy_backtests (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    doc TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kite_backtests_user ON kite_strategy_backtests (user_id, created_at);
CREATE TABLE IF NOT EXISTS kite_data_grants (
    owner_id TEXT NOT NULL,
    recipient_id TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (owner_id, recipient_id)
);
CREATE TABLE IF NOT EXISTS kite_data_sources (
    recipient_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL
);
"""

#: Trade states that still need the engine (anything not terminal).
OPEN_STATES = ("entry_sent", "protected", "exit_sent", "needs_attention")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class KiteStore:
    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)

    @property
    def path(self) -> str:
        return self._path

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

    # ------------------------------------------------------------ secrets
    def put_secret(self, user_id: str, kind: str, ciphertext: str) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO kite_secrets (user_id, kind, ciphertext, updated_at) "
                      "VALUES (?,?,?,?) ON CONFLICT(user_id, kind) DO UPDATE SET "
                      "ciphertext=excluded.ciphertext, updated_at=excluded.updated_at",
                      (user_id, kind, ciphertext, now_iso()))

    def get_secret(self, user_id: str, kind: str) -> Optional[str]:
        with self._conn() as c:
            row = c.execute("SELECT ciphertext FROM kite_secrets WHERE user_id=? AND kind=?",
                            (user_id, kind)).fetchone()
        return row["ciphertext"] if row else None

    def delete_secret(self, user_id: str, kind: str) -> bool:
        with self._conn() as c:
            return c.execute("DELETE FROM kite_secrets WHERE user_id=? AND kind=?",
                             (user_id, kind)).rowcount > 0

    def users_with_secret(self, kind: str) -> List[str]:
        with self._conn() as c:
            return [r["user_id"] for r in
                    c.execute("SELECT user_id FROM kite_secrets WHERE kind=?", (kind,))]

    # Data grants contain identities only. Credentials remain owner-scoped.
    def grant_data_access(self, owner_id: str, recipient_id: str, label: str = "") -> None:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            count = c.execute("SELECT COUNT(*) FROM kite_data_grants WHERE owner_id=?",
                              (owner_id,)).fetchone()[0]
            exists = c.execute("SELECT 1 FROM kite_data_grants WHERE owner_id=? AND recipient_id=?",
                               (owner_id, recipient_id)).fetchone()
            if count >= 50 and not exists:
                raise ValueError("share with at most 50 accounts")
            c.execute("INSERT INTO kite_data_grants VALUES (?,?,?,?) ON CONFLICT(owner_id,recipient_id) "
                      "DO UPDATE SET label=excluded.label", (owner_id, recipient_id, label, now_iso()))

    def revoke_data_access(self, owner_id: str, recipient_id: str) -> bool:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            removed = c.execute("DELETE FROM kite_data_grants WHERE owner_id=? AND recipient_id=?",
                                (owner_id, recipient_id)).rowcount > 0
            c.execute("DELETE FROM kite_data_sources WHERE owner_id=? AND recipient_id=?",
                      (owner_id, recipient_id))
            return removed

    def revoke_all_data_access(self, owner_id: str) -> None:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("DELETE FROM kite_data_grants WHERE owner_id=?", (owner_id,))
            c.execute("DELETE FROM kite_data_sources WHERE owner_id=?", (owner_id,))

    def data_grants(self, user_id: str, *, received: bool = False) -> List[Dict[str, Any]]:
        col = "recipient_id" if received else "owner_id"
        with self._conn() as c:
            return [dict(r) for r in c.execute(f"SELECT * FROM kite_data_grants WHERE {col}=? "
                                              "ORDER BY created_at", (user_id,))]

    def select_data_source(self, recipient_id: str, owner_id: Optional[str]) -> bool:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            if owner_id is None:
                c.execute("DELETE FROM kite_data_sources WHERE recipient_id=?", (recipient_id,))
                return True
            if not c.execute("SELECT 1 FROM kite_data_grants WHERE owner_id=? AND recipient_id=?",
                             (owner_id, recipient_id)).fetchone():
                return False
            c.execute("INSERT INTO kite_data_sources VALUES (?,?) ON CONFLICT(recipient_id) "
                      "DO UPDATE SET owner_id=excluded.owner_id", (recipient_id, owner_id))
            return True

    def selected_data_source(self, recipient_id: str) -> Optional[str]:
        with self._conn() as c:
            row = c.execute("SELECT s.owner_id FROM kite_data_sources s JOIN kite_data_grants g "
                            "ON g.owner_id=s.owner_id AND g.recipient_id=s.recipient_id "
                            "WHERE s.recipient_id=?", (recipient_id,)).fetchone()
            return row["owner_id"] if row else None

    # ------------------------------------------------------------ login states
    def add_state(self, nonce: str, user_id: str, expires_at: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM kite_login_states WHERE expires_at < ?", (now_iso(),))
            c.execute("INSERT INTO kite_login_states (nonce, user_id, expires_at) VALUES (?,?,?)",
                      (nonce, user_id, expires_at))

    def consume_state(self, nonce: str) -> Optional[Dict[str, Any]]:
        """Mark the nonce used and return its row as it was BEFORE (atomic)."""
        with self._conn() as c:
            row = c.execute("SELECT * FROM kite_login_states WHERE nonce=?", (nonce,)).fetchone()
            if row is None:
                return None
            c.execute("UPDATE kite_login_states SET used=1 WHERE nonce=?", (nonce,))
            return dict(row)

    # ------------------------------------------------------------ control
    def get_control(self, user_id: str) -> Dict[str, Any]:
        with self._conn() as c:
            row = c.execute("SELECT doc FROM kite_control WHERE user_id=?", (user_id,)).fetchone()
        return json.loads(row["doc"]) if row else {}

    def put_control(self, user_id: str, doc: Dict[str, Any]) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO kite_control (user_id, doc, updated_at) VALUES (?,?,?) "
                      "ON CONFLICT(user_id) DO UPDATE SET doc=excluded.doc, "
                      "updated_at=excluded.updated_at", (user_id, json.dumps(doc), now_iso()))

    # ------------------------------------------------------------ trades
    def create_trade(self, user_id: str, doc: Dict[str, Any]) -> Dict[str, Any]:
        doc = dict(doc, id=doc.get("id") or uuid.uuid4().hex)
        ts = now_iso()
        with self._conn() as c:
            c.execute("INSERT INTO kite_trades (id, user_id, status, trade_date, doc, created_at,"
                      " updated_at) VALUES (?,?,?,?,?,?,?)",
                      (doc["id"], user_id, doc["status"], doc["trade_date"], json.dumps(doc), ts, ts))
        return doc

    def update_trade(self, user_id: str, doc: Dict[str, Any]) -> None:
        with self._conn() as c:
            c.execute("UPDATE kite_trades SET status=?, doc=?, updated_at=? WHERE id=? AND user_id=?",
                      (doc["status"], json.dumps(doc), now_iso(), doc["id"], user_id))

    def get_trade(self, tid: str, user_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as c:
            row = c.execute("SELECT doc FROM kite_trades WHERE id=? AND user_id=?",
                            (tid, user_id)).fetchone()
        return json.loads(row["doc"]) if row else None

    def list_trades(self, user_id: str, *, open_only: bool = False, limit: int = 200
                    ) -> List[Dict[str, Any]]:
        q = "SELECT doc FROM kite_trades WHERE user_id=?"
        args: list = [user_id]
        if open_only:
            q += " AND status IN (%s)" % ",".join("?" * len(OPEN_STATES))
            args += list(OPEN_STATES)
        q += " ORDER BY created_at DESC LIMIT ?"
        with self._conn() as c:
            return [json.loads(r["doc"]) for r in c.execute(q, (*args, limit))]

    def users_with_open_trades(self) -> List[str]:
        with self._conn() as c:
            return [r["user_id"] for r in c.execute(
                "SELECT DISTINCT user_id FROM kite_trades WHERE status IN (%s)"
                % ",".join("?" * len(OPEN_STATES)), OPEN_STATES)]

    # ------------------------------------------------------------ strategies
    def put_strategy(self, user_id: str, doc: Dict[str, Any]) -> Dict[str, Any]:
        doc = dict(doc, id=doc.get("id") or uuid.uuid4().hex)
        ts = now_iso()
        with self._conn() as c:
            c.execute("INSERT INTO kite_strategies (id, user_id, doc, created_at, updated_at) "
                      "VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET doc=excluded.doc, "
                      "updated_at=excluded.updated_at WHERE kite_strategies.user_id=excluded.user_id",
                      (doc["id"], user_id, json.dumps(doc), ts, ts))
        return doc

    def get_strategy(self, sid: str, user_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as c:
            row = c.execute("SELECT doc FROM kite_strategies WHERE id=? AND user_id=?",
                            (sid, user_id)).fetchone()
        return json.loads(row["doc"]) if row else None

    def list_strategies(self, user_id: str) -> List[Dict[str, Any]]:
        with self._conn() as c:
            return [json.loads(r["doc"]) for r in c.execute(
                "SELECT doc FROM kite_strategies WHERE user_id=? ORDER BY created_at", (user_id,))]

    def delete_strategy(self, sid: str, user_id: str) -> bool:
        with self._conn() as c:
            return c.execute("DELETE FROM kite_strategies WHERE id=? AND user_id=?",
                             (sid, user_id)).rowcount > 0

    def users_with_active_strategies(self) -> List[str]:
        with self._conn() as c:
            rows = c.execute("SELECT user_id, doc FROM kite_strategies").fetchall()
        return sorted({r["user_id"] for r in rows if json.loads(r["doc"]).get("mode") != "off"})


    # ------------------------------------------------------------ backtests (insert-only)
    def create_backtest(self, user_id: str, doc: Dict[str, Any]) -> Dict[str, Any]:
        doc = dict(doc, id=uuid.uuid4().hex, created_at=now_iso())
        with self._conn() as c:
            c.execute("INSERT INTO kite_strategy_backtests (id, user_id, strategy_id, doc, "
                      "created_at) VALUES (?,?,?,?,?)",
                      (doc["id"], user_id, doc["strategy_id"], json.dumps(doc), doc["created_at"]))
        return doc

    def get_backtest(self, bid: str, user_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as c:
            row = c.execute("SELECT doc FROM kite_strategy_backtests WHERE id=? AND user_id=?",
                            (bid, user_id)).fetchone()
        return json.loads(row["doc"]) if row else None

    def list_backtests(self, user_id: str, strategy_id: Optional[str] = None,
                       limit: int = 50) -> List[Dict[str, Any]]:
        q, args = "SELECT doc FROM kite_strategy_backtests WHERE user_id=?", [user_id]
        if strategy_id:
            q, args = q + " AND strategy_id=?", args + [strategy_id]
        with self._conn() as c:
            return [json.loads(r["doc"]) for r in c.execute(
                q + " ORDER BY created_at DESC LIMIT ?", (*args, limit))]


_store: Optional[KiteStore] = None
_lock = threading.Lock()


def get_kite_store() -> KiteStore:
    global _store
    with _lock:
        if _store is None:
            _store = KiteStore(os.environ.get("KITE_DB_PATH", "data/kite.sqlite"))
        return _store


def reset_kite_store_for_tests(path: Path | str) -> KiteStore:
    global _store
    with _lock:
        _store = KiteStore(path)
        return _store
