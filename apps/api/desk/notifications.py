"""Opt-in Telegram delivery; in-app events remain the durable source of truth."""
from __future__ import annotations
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from apps.api import user_keys
from apps.api.jobs.store import get_store
from apps.api.desk.store import get_desk_store, now_iso

_LOCK = threading.Lock()
_QUEUE_LOCK = threading.Lock()
_QUEUED = set()
_LAST_ATTEMPT = {}
#: An alert is only worth sending while it is news; older ones stay in-app only.
EXPIRY = timedelta(minutes=15)
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='desk-notifications')
_SCHEMA = '''CREATE TABLE IF NOT EXISTS desk_notifications (
 user_id TEXT NOT NULL, event_id TEXT NOT NULL, sent_at TEXT NOT NULL,
 PRIMARY KEY(user_id,event_id));
 CREATE TABLE IF NOT EXISTS desk_notification_state (
 user_id TEXT PRIMARY KEY, last_error TEXT);'''


def _db():
    return get_desk_store()._conn()


def _ensure(conn):
    conn.executescript(_SCHEMA)


def _config(uid):
    f = user_keys._fernet()
    raw = get_store().get_user_key(uid, 'telegram')
    if not f or not raw:
        return None
    try:
        return json.loads(f.decrypt(raw.encode()))
    except Exception:
        return None


def configure(uid: str, token: str, chat: str, enabled: bool):
    f = user_keys._fernet()
    if f is None:
        raise RuntimeError('Encrypted credential storage is not configured')
    current = _config(uid) or {}
    token = token.strip() or current.get('token', '')
    if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token):
        raise ValueError('Enter a valid Telegram bot token')
    if not re.fullmatch(r'-?[0-9]+', chat.strip()):
        raise ValueError('Enter a numeric Telegram chat ID')
    doc = {'token':token, 'chat_id':chat.strip(), 'enabled':enabled,
           'enabled_since':current.get('enabled_since') if current.get('enabled') else now_iso()}
    get_store().set_user_key(uid, 'telegram', f.encrypt(json.dumps(doc).encode()).decode())
    return status(uid)


def status(uid: str):
    doc = _config(uid) or {}
    with _db() as conn:
        _ensure(conn)
        row = conn.execute('SELECT last_error FROM desk_notification_state WHERE user_id=?',(uid,)).fetchone()
    return {'configured': bool(doc), 'enabled': doc.get('enabled',False),
            'chat_id':doc.get('chat_id',''), 'encryption_available':user_keys.encryption_enabled(),
            'last_error': row['last_error'] if row else None}


def _send(token: str, chat: str, message: str):
    import requests
    response = requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                             json={'chat_id':chat, 'text':message[:4000]},timeout=6)
    if not response.ok or not response.json().get('ok'):
        raise RuntimeError('Telegram rejected the notification')


def _cutoff(doc: dict) -> str:
    """Oldest event time still deliverable: not before opt-in, not expired."""
    fresh = (datetime.now(timezone.utc) - EXPIRY).isoformat()
    return max(fresh, doc.get('enabled_since', ''))


def deliver(uid: str, event: dict) -> bool:
    doc = _config(uid)
    if not doc or not doc.get('enabled'):
        return False
    if event.get('at') and event['at'] < _cutoff(doc):
        return False
    # Same-user overlapping page/background checks must not duplicate delivery.
    with _LOCK:
        with _db() as conn:
            _ensure(conn)
            if conn.execute('SELECT 1 FROM desk_notifications WHERE user_id=? AND event_id=?',
                            (uid,event['id'])).fetchone():
                return False
        try:
            _send(doc['token'],doc['chat_id'],str(event['message']))
        except Exception:
            with _db() as conn:
                conn.execute('INSERT OR REPLACE INTO desk_notification_state VALUES (?,?)',
                             (uid,'Telegram delivery failed; in-app alerts remain available'))
            return False
        with _db() as conn:
            conn.execute('INSERT OR IGNORE INTO desk_notifications VALUES (?,?,?)',
                         (uid,event['id'],now_iso()))
            conn.execute('INSERT OR REPLACE INTO desk_notification_state VALUES (?,NULL)',(uid,))
        return True


def retry_recent(uid: str):
    doc = _config(uid) or {}
    if not doc.get('enabled'):
        return
    with _db() as conn:
        _ensure(conn)
        rows = conn.execute(
            "SELECT e.doc FROM desk_alert_events e LEFT JOIN desk_notifications n "
            "ON n.user_id=e.user_id AND n.event_id=e.id "
            "WHERE e.user_id=? AND e.at>=? AND n.event_id IS NULL ORDER BY e.at LIMIT 20",
            (uid, _cutoff(doc))).fetchall()
    for row in rows:
        deliver(uid,json.loads(row['doc']))


def pending_users() -> set:
    """Users with Telegram on and an undelivered, unexpired alert event.

    The desk sweep visits these even when no rule is still active (rules ring
    once), so a failed send is retried without the user opening a page.
    """
    with get_store()._conn() as conn:
        uids = [r['user_id'] for r in conn.execute(
            "SELECT user_id FROM user_api_keys WHERE provider='telegram'").fetchall()]
    fresh = (datetime.now(timezone.utc) - EXPIRY).isoformat()
    out = set()
    with _db() as conn:
        _ensure(conn)
        for uid in uids:
            row = conn.execute(
                "SELECT 1 FROM desk_alert_events e LEFT JOIN desk_notifications n "
                "ON n.user_id=e.user_id AND n.event_id=e.id "
                "WHERE e.user_id=? AND e.at>=? AND n.event_id IS NULL LIMIT 1", (uid, fresh)).fetchone()
            if row and (_config(uid) or {}).get('enabled'):
                out.add(uid)
    return out


def queue_delivery(uid: str):
    """Network delivery must not delay the Desk's HTTP response."""
    with _QUEUE_LOCK:
        if uid in _QUEUED or time.monotonic() - _LAST_ATTEMPT.get(uid, -60) < 60:
            return
        if not (_config(uid) or {}).get('enabled'):
            return
        _QUEUED.add(uid)
        _LAST_ATTEMPT[uid] = time.monotonic()
    def work():
        try:
            retry_recent(uid)
        finally:
            with _QUEUE_LOCK:
                _QUEUED.discard(uid)
    _POOL.submit(work)
