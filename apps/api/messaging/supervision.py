"""Mobile supervision over a verified private chat: strategy status,
performance, confirmed pause/resume, a daily summary and live-signal links.

* Read-only: STATUS and PERFORMANCE. Performance reads
  ``evaluation.analytics.trading_performance`` (paper and live separately);
  nothing here starts an analysis run or any paid model call.
* Control: a status card offers single-use codes bound to the user, this
  connection's generation, one strategy and its version. A code is first
  requested (PAUSE/RESUME or button) and then confirmed within two minutes;
  replays return the stored result. Pause/resume runs under the same
  ``execution.user_lock`` as scans and edits: a newer version (an edit) or
  today's kill switch wins. Resume is Paper by default, Confirm only on an
  explicit Default-portfolio button; it never arms live trading and never
  enables Auto.
* Live signals are never approved from chat: an opt-in card deep-links to the
  app's exact-ticket typed confirmation.
"""
import json
import secrets

from apps.api.desk.market.session import NSE
from apps.api.kite import execution
from apps.api.kite.execution import user_lock
from apps.api.messaging import store

OFFER_S = 600
CONFIRM_S = 120
SUMMARY_AFTER_MIN = 15 * 60 + 45
MAX_STRATEGIES = 8
_SCHEMA = ('CREATE TABLE IF NOT EXISTS messaging_controls (token TEXT PRIMARY KEY, '
           'user_id TEXT NOT NULL, doc TEXT NOT NULL, created_at REAL NOT NULL)')
_WORDS = {'pause': 'pause', 'resume_paper': 'resume in PAPER mode',
          'resume_confirm': 'resume in CONFIRM mode'}


def _ensure():
    with store.db() as c:
        c.execute(_SCHEMA)


def _save(doc):
    _ensure()
    with store.db() as c:
        c.execute('INSERT INTO messaging_controls VALUES (?,?,?,?) ON CONFLICT(token) DO UPDATE SET doc=excluded.doc',
                  (doc['token'], doc['user_id'], json.dumps(doc), store.now().timestamp()))
        c.execute('DELETE FROM messaging_controls WHERE created_at<?', (store.now().timestamp() - 86400,))


def _load(uid, token):
    _ensure()
    with store.db() as c:
        r = c.execute('SELECT doc FROM messaging_controls WHERE token=? AND user_id=?', (token, uid)).fetchone()
    return json.loads(r['doc']) if r else None


def _inr(x):
    return '—' if x is None else f'₹{x:.2f}'


def _ver(s):
    return int(s.get('version') or 1)


# ------------------------------------------------------------ read-only

def status_card(doc):
    from apps.api.desk.portfolios import name_of
    from apps.api.kite.store import get_kite_store

    uid, now = doc['user_id'], store.now()
    ctl = execution.control(uid, now)
    live = ('kill switch used today' if ctl['killed'] else 'armed today' if ctl['armed']
            else 'not armed today')
    today = now.astimezone(NSE.tz).date().isoformat()
    lines = [f"Drishti status — {now.astimezone(NSE.tz):%H:%M} IST",
             f"Live trading: {live} (arming and the kill switch are only in the app)"]
    buttons, codes = [], []
    strategies = get_kite_store().list_strategies(uid)
    if not strategies:
        lines.append('No strategies yet.')
    for s in strategies[:MAX_STRATEGIES]:
        sig = [g for g in s.get('signals') or [] if g.get('date') == today]
        scan = s.get('last_scan') or {}
        scan_txt = (f" · last scan {str(scan.get('at', ''))[11:16]} {scan.get('source', '')}".rstrip()
                    if isinstance(scan, dict) and scan.get('at') else '')
        name = str(s.get('name', 'Strategy'))[:30]
        lines.append(f"• {name} v{_ver(s)} — {str(s.get('mode', 'off')).upper()} · "
                     f"{name_of(uid, s.get('portfolio_id') or 'default')} · today {len(sig)} signal(s){scan_txt}")
        if s.get('mode') != 'off':
            actions = ['pause']
        else:
            actions = ['resume_paper']
            if s.get('paused_from') == 'confirm' and (s.get('portfolio_id') or 'default') == 'default':
                actions.append('resume_confirm')
        row = []
        for action in actions:
            tok = secrets.token_urlsafe(18)
            _save({'token': tok, 'user_id': uid, 'connection_id': doc['id'],
                   'generation': doc.get('generation'), 'strategy_id': s['id'], 'name': name,
                   'version': _ver(s), 'action': action, 'stage': 'offered',
                   'expires_at': now.timestamp() + OFFER_S, 'result': None})
            label = {'pause': 'Pause', 'resume_paper': 'Resume paper',
                     'resume_confirm': 'Resume confirm'}[action]
            row.append({'text': f'{label} · {name}', 'callback_data': 'c:' + tok})
            codes.append(f"{'PAUSE' if action == 'pause' else 'RESUME'} {tok} — {label.lower()} {name}")
        buttons.append(row)
    if codes:
        lines += ['', 'Codes (valid 10 minutes, each needs a confirmation):'] + codes
    return '\n'.join(lines), buttons or None


def _book_line(label, p):
    o = p['open']
    unmarked = o.get('unmarked') or 0
    line = (f"{label}: realized {_inr(p['realized']['pnl_net'])} over {p['realized']['trades']} closed trade(s); "
            f"open {_inr(o['pnl'])} gross on {o['positions']} position(s)")
    if unmarked:
        line += f"; {unmarked} open position{'s' if unmarked > 1 else ''} without a fresh price"
    return line


def performance_text(uid):
    from apps.api.evaluation import analytics

    paper = analytics.trading_performance(uid, mode='paper', portfolio_id='all')
    live = analytics.trading_performance(uid, mode='live', portfolio_id='default')
    b = paper['benchmark']
    bench = (f"{b['name']}: {b['return_pct']:+.2f}% ({b['start']} → {b['end']})" if b.get('available')
             else f"{b.get('name', 'Benchmark')}: unavailable — {b.get('reason')}")
    return '\n'.join(['Drishti performance (paper and live are separate; paper is not money)',
                      _book_line('PAPER, all portfolios', paper), _book_line('LIVE, Default', live),
                      bench, paper.get('charges_note', '')]).strip()


# ------------------------------------------------------------ controls

def _reply(text, buttons=None):
    return text, buttons


def decide(uid, channel, token, step, word=None):
    """step: request | confirm | cancel. Raises ValueError when not authorized."""
    with user_lock(uid):
        doc = store.connection(uid, channel)
        if not doc or not all(doc.get(k) for k in ('verified', 'connected', 'inbound_verified', 'supervision')):
            raise ValueError('Strategy controls are not enabled for this private chat')
        t = _load(uid, token)
        if not t or t['connection_id'] != doc['id'] or t['generation'] != doc.get('generation'):
            raise ValueError('Unknown or revoked control code. Send STATUS for a new card.')
        if word and (word == 'pause') != (t['action'] == 'pause'):
            raise ValueError('That code is for a different action. Send STATUS for a new card.')
        if t['stage'] in ('done', 'cancelled'):
            return _reply(t['result'] or 'Cancelled. Nothing changed.')
        now = store.now().timestamp()
        if t['expires_at'] <= now:
            t.update(stage='done', result='This control code expired. Nothing changed; send STATUS again.')
            _save(t)
            return _reply(t['result'])
        if step == 'cancel':
            t.update(stage='cancelled', result='Cancelled. Nothing changed.')
            _save(t)
            return _reply(t['result'])
        if step == 'request':
            # offered -> confirming happens once; a repeated request re-shows
            # the prompt but never moves the first confirmation deadline.
            if t['stage'] == 'offered':
                t.update(stage='confirming', expires_at=now + CONFIRM_S)
                _save(t)
            left = max(1, round((t['expires_at'] - now) / 60))
            return _reply(f"Confirm: {_WORDS[t['action']]} “{t['name']}” v{t['version']}? "
                          f"Expires in {left} minute{'s' if left > 1 else ''}. Reply CONFIRM {token} or CANCEL {token}.",
                          [[{'text': 'Confirm', 'callback_data': 'y:' + token},
                            {'text': 'Cancel', 'callback_data': 'n:' + token}]])
        if t['stage'] != 'confirming':
            return _reply('Request this action first, then confirm it.')
        t.update(stage='done', result=_apply(uid, t, channel))
        _save(t)
        return _reply(t['result'])


def _apply(uid, t, channel):
    """Under the user's lock (held by ``decide``): the stored strategy decides."""
    from apps.api.kite.store import get_kite_store

    ks = get_kite_store()
    s = ks.get_strategy(t['strategy_id'], uid)
    if s is None:
        return 'That strategy no longer exists. Nothing changed.'
    if _ver(s) != t['version']:
        return (f"“{t['name']}” changed since this card (now v{_ver(s)}). Nothing changed; "
                'send STATUS again.')
    now = store.now()
    if t['action'] == 'pause':
        if s.get('mode') == 'off':
            return f"“{t['name']}” is already paused."
        s.update(mode='off', paused_from=s['mode'], paused_at=now.isoformat(), paused_via=channel)
        ks.put_strategy(uid, s)
        return f"Paused “{t['name']}” v{t['version']}. It will not scan or act until you resume it."
    if execution.control(uid, now)['killed']:
        return 'The kill switch was used today: strategies stay off until tomorrow. Nothing changed.'
    if s.get('mode') != 'off':
        return f"“{t['name']}” is already running in {str(s['mode']).upper()} mode."
    target = 'confirm' if t['action'] == 'resume_confirm' else 'paper'
    if target == 'confirm' and (s.get('portfolio_id') or 'default') != 'default':
        return 'Confirm mode needs the Default portfolio. Nothing changed.'
    s['mode'] = target
    for k in ('paused_from', 'paused_at', 'paused_via', 'paused_reason'):
        s.pop(k, None)
    ks.put_strategy(uid, s)
    if target == 'paper':
        return f"Resumed “{t['name']}” v{t['version']} in PAPER mode. No real orders."
    return (f"Resumed “{t['name']}” v{t['version']} in CONFIRM mode. It only proposes: each live order "
            'still needs your typed confirmation in Drishti, and live trading is armed only in the app.')


def handle(doc, text):
    """A supervision command's reply (text, buttons), a deferred callable for
    work that must not hold the trade lock, or None when not ours."""
    parts = text.strip().replace(':', ' ', 1).split()
    if not parts:
        return None
    head = parts[0].lower().lstrip('/').split('@')[0]
    one = {'status', 'strategies', 'performance', 'perf', 'pnl', 'help'}
    two = {'c': 'request', 'pause': 'request', 'resume': 'request', 'y': 'confirm', 'confirm': 'confirm',
           'n': 'cancel', 'cancel': 'cancel'}
    if not ((len(parts) == 1 and head in one) or (len(parts) == 2 and head in two)):
        return None
    if not doc.get('supervision'):
        return _reply('Strategy status and controls are off for this chat. Enable them in Drishti → Messages.')
    if head == 'help':
        return _reply('STATUS — strategies and controls\nPERFORMANCE — paper and live results\n'
                      'Pause/resume needs a code from STATUS and a confirmation.')
    if head in ('status', 'strategies'):
        return status_card(doc)
    if len(parts) == 1:
        return lambda: _reply(performance_text(doc['user_id']))
    try:
        return decide(doc['user_id'], doc['channel'], parts[1], two[head],
                      head if head in ('pause', 'resume') else None)
    except ValueError as exc:
        return _reply(str(exc))


# ------------------------------------------------------------ background

def _today_rows(p, day):
    return [r for r in p.get('trades') or [] if r.get('trade_date') == day and r.get('status') == 'closed']


def daily_summaries():
    """One summary per opted-in connection per NSE trading day, after the
    close. The outbox key is the date, so restarts and repeat sweeps dedupe."""
    from apps.api.evaluation import analytics
    from apps.api.kite.store import get_kite_store

    now = store.now()
    local = now.astimezone(NSE.tz)
    if not NSE.is_trading_day(local.date()) or local.hour * 60 + local.minute < SUMMARY_AFTER_MIN:
        return
    day = local.date().isoformat()
    for doc in store.all_connections():
        if not (doc.get('daily_summary') and doc.get('verified') and doc.get('connected')):
            continue
        if store.has_delivery(doc['id'], 'summary-' + day):
            continue
        uid = doc['user_id']
        lines = [f'Drishti daily summary — {day}']
        for mode, pf, label in (('paper', 'all', 'PAPER'), ('live', 'default', 'LIVE')):
            p = analytics.trading_performance(uid, mode=mode, portfolio_id=pf)
            rows = _today_rows(p, day)
            lines.append(f"{label}: {len(rows)} closed today, net {_inr(sum(r['pnl_net'] or 0 for r in rows))}; "
                         f"{p['open']['positions']} open")
        running = [s for s in get_kite_store().list_strategies(uid) if s.get('mode') != 'off']
        lines.append(f"Strategies running: {len(running)}. Charges are estimates.")
        store.enqueue(doc['id'], 'summary-' + day, '\n'.join(lines), expires_at=now.timestamp() + 6 * 3600,
                      generation=doc.get('generation'))


def live_signal_cards():
    """Opt-in cards for strategy signals awaiting a live confirmation. They
    carry no approve action: only a link to the app's typed confirmation."""
    from datetime import datetime
    from apps.api.kite.store import get_kite_store
    from apps.api.messaging.providers import public_base

    now = store.now().timestamp()
    for doc in store.all_connections():
        if not (doc.get('live_links') and doc.get('verified') and doc.get('connected')):
            continue
        url = public_base() + '/desk/strategies'
        for s in get_kite_store().list_strategies(doc['user_id']):
            for g in s.get('signals') or []:
                if g.get('status') != 'await_confirm' or not g.get('expires_at'):
                    continue
                exp = datetime.fromisoformat(g['expires_at']).timestamp()
                if exp <= now:
                    continue
                o = g.get('order') or {}
                text = (f"LIVE signal — {s.get('name')} v{_ver(s)}: BUY {o.get('qty', '?')} {g.get('symbol')} "
                        f"@ {o.get('price', 'market')} · stop {o.get('stop')} · target {o.get('target')}\n"
                        f"Source: {g.get('source', 'unknown')}. This chat cannot place it: open Drishti and "
                        f"enter the exact typed confirmation before {datetime.fromtimestamp(exp, NSE.tz):%H:%M:%S} IST.\n{url}")
                store.enqueue(doc['id'], f"live-{s['id']}-{g['id']}", text,
                              [[{'text': 'Open Drishti to confirm', 'url': url}]], expires_at=exp,
                              generation=doc.get('generation'))

