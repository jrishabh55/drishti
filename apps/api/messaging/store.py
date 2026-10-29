"""Additive messaging persistence on the existing Desk volume."""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from apps.api.desk.store import get_desk_store
from apps.api.jobs.store import get_store
from apps.api import user_keys

SCHEMA = '''
CREATE TABLE IF NOT EXISTS messaging_connections (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL, channel TEXT NOT NULL,
 bot_id TEXT UNIQUE, active INTEGER NOT NULL, doc TEXT NOT NULL,
 UNIQUE(user_id,channel));
CREATE TABLE IF NOT EXISTS messaging_actions (
 id TEXT PRIMARY KEY,user_id TEXT NOT NULL,ticket_id TEXT NOT NULL,doc TEXT NOT NULL,
 UNIQUE(user_id,ticket_id));
CREATE TABLE IF NOT EXISTS messaging_outbox (
 id TEXT PRIMARY KEY,user_id TEXT NOT NULL,connection_id TEXT NOT NULL,
 created_at REAL NOT NULL,doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messaging_inbound (
 connection_id TEXT NOT NULL,message_id TEXT NOT NULL,created_at REAL NOT NULL,
 PRIMARY KEY(connection_id,message_id));
'''


def now():
    return datetime.now(timezone.utc)


def db():
    return get_desk_store()._conn()


def ensure():
    with db() as c:
        c.executescript(SCHEMA)


def connection(uid, channel, include_inactive=False):
    ensure()
    with db() as c:
        r = c.execute('SELECT doc FROM messaging_connections WHERE user_id=? AND channel=?'+('' if include_inactive else ' AND active=1'), (uid,channel)).fetchone()
    return json.loads(r['doc']) if r else None


def by_id(cid):
    ensure()
    with db() as c:
        r = c.execute('SELECT doc FROM messaging_connections WHERE id=? AND active=1', (cid,)).fetchone()
    return json.loads(r['doc']) if r else None


def save(doc):
    ensure()
    try:
        with db() as c:
            c.execute('INSERT INTO messaging_connections VALUES (?,?,?,?,?,?) '
                      'ON CONFLICT(user_id,channel) DO UPDATE SET id=excluded.id,bot_id=excluded.bot_id,active=excluded.active,doc=excluded.doc',
                      (doc['id'],doc['user_id'],doc['channel'],doc.get('bot_id') if doc.get('active',True) or doc.get('cleanup_pending') else None,
                       int(doc.get('active',True)),json.dumps(doc)))
    except sqlite3.IntegrityError:
        raise ValueError('This bot is already connected to another user') from None
    return doc


def new(uid, channel):
    return {'id':uuid.uuid4().hex,'user_id':uid,'channel':channel,'active':True,
            'generation':uuid.uuid4().hex,
            'verified':False,'connected':False,'alerts':False,'approvals':False,
            'enabled_since':now().timestamp(),'last_error':None}


def secret(uid, channel, value=None):
    f = user_keys._fernet()
    if not f:
        if value is not None:
            raise ValueError('Encrypted credential storage needs administrator setup')
        return {}
    key = 'messaging.' + channel
    if value is not None:
        get_store().set_user_key(uid,key,f.encrypt(json.dumps(value).encode()).decode())
        return value
    raw = get_store().get_user_key(uid,key)
    try:
        return json.loads(f.decrypt(raw.encode())) if raw else {}
    except Exception:
        return {}


def all_connections():
    ensure()
    with db() as c:
        return [json.loads(r['doc']) for r in c.execute('SELECT doc FROM messaging_connections WHERE active=1').fetchall()]


def action(uid, tid):
    ensure()
    with db() as c:
        r = c.execute('SELECT doc FROM messaging_actions WHERE user_id=? AND ticket_id=?',(uid,tid)).fetchone()
    return json.loads(r['doc']) if r else None


def save_action(doc):
    ensure()
    with db() as c:
        c.execute('INSERT INTO messaging_actions VALUES (?,?,?,?) ON CONFLICT(user_id,ticket_id) DO UPDATE SET doc=excluded.doc',
                  (doc['id'],doc['user_id'],doc['ticket_id'],json.dumps(doc)))


def all_actions():
    ensure()
    with db() as c:
        return [json.loads(r['doc']) for r in c.execute('SELECT doc FROM messaging_actions').fetchall()]


def enqueue(cid, key, text, buttons=None, expires_at=None):
    conn = by_id(cid)
    if not conn:
        return
    doc = {'id':cid+':'+key,'generation':conn.get('generation'),'created_at':now().timestamp(),'text':text,'buttons':buttons,'sent':False,'attempts':0,
           'next_attempt':0,'expires_at':expires_at or now().timestamp()+900,'message_id':None}
    with db() as c:
        c.execute('INSERT OR IGNORE INTO messaging_outbox VALUES (?,?,?,?,?)',
                  (doc['id'],conn['user_id'],cid,now().timestamp(),json.dumps(doc)))


def deliveries(cid, unsent=False):
    ensure()
    with db() as c:
        if unsent:
            # Filter before LIMIT: expired/exhausted rows cannot starve a
            # valid older message. SQLite JSON is already used in Desk stores.
            rows=c.execute("SELECT doc FROM messaging_outbox WHERE connection_id=? "
                "AND json_extract(doc,'$.sent')=0 AND coalesce(json_extract(doc,'$.cancelled'),0)=0 "
                "AND json_extract(doc,'$.expires_at')>? AND json_extract(doc,'$.attempts')<5 "
                "AND json_extract(doc,'$.next_attempt')<=? ORDER BY created_at LIMIT 10",
                (cid,now().timestamp(),now().timestamp())).fetchall()
        else:
            rows=c.execute('SELECT doc FROM messaging_outbox WHERE connection_id=? ORDER BY created_at DESC LIMIT 100',(cid,)).fetchall()
    return [json.loads(r['doc']) for r in rows]


def save_delivery(doc):
    with db() as c:
        c.execute('UPDATE messaging_outbox SET doc=? WHERE id=?',(json.dumps(doc),doc['id']))


def seen(cid, mid):
    ensure()
    with db() as c:
        return bool(c.execute('SELECT 1 FROM messaging_inbound WHERE connection_id=? AND message_id=?',(cid,str(mid))).fetchone())


def mark_seen(cid, mid):
    with db() as c:
        c.execute('INSERT OR IGNORE INTO messaging_inbound VALUES (?,?,?)',(cid,str(mid),now().timestamp()))
        c.execute('DELETE FROM messaging_inbound WHERE created_at<?',(now().timestamp()-86400,))
        c.execute('DELETE FROM messaging_outbox WHERE created_at<?',(now().timestamp()-86400*7,))


def cleanup():
    ensure()
    with db() as c:
        c.execute('DELETE FROM messaging_inbound WHERE created_at<?',(now().timestamp()-86400,))
        c.execute('DELETE FROM messaging_outbox WHERE created_at<?',(now().timestamp()-86400*7,))
