"""Mobile supervision: verified status/performance, confirmed pause/resume,
deduplicated daily summaries and live-signal deep links. No provider traffic,
no paid models, no arming, no orders."""
import re
from datetime import datetime, timedelta

import pytest

from tests.test_messaging_connections import env  # noqa: F401
from apps.api.desk.market.session import NSE
from apps.api.desk.store import get_desk_store
from apps.api.kite import execution
from apps.api.kite.store import get_kite_store, reset_kite_store_for_tests
from apps.api.messaging import connections, service, store, supervision

NOW = NSE.local(datetime(2026, 10, 29).date(), 11 * 60)


@pytest.fixture
def sup(env, tmp_path, monkeypatch):
    c, s, calls = env
    reset_kite_store_for_tests(tmp_path / 'kite.sqlite')
    clock = {'now': NOW}
    monkeypatch.setattr(s, 'now', lambda: clock['now'])
    monkeypatch.setattr(execution, '_now', lambda: clock['now'], raising=False)
    from apps.api.jobs import runner
    monkeypatch.setattr(runner.JobRunner, 'submit',
                        lambda *a, **k: pytest.fail('a supervision command started a paid run'))
    for ch in ('telegram', 'whatsapp'):
        row = s.new('a', ch)
        row.update(connected=True, verified=True, inbound_verified=True, hook_ready=True,
                   supervision=True, chat='42', sender='42', recipient='919876543210',
                   instance='drishti-' + 'a' * 32)
        s.save(row)
        s.secret('a', ch, {'hook_secret': 'hook', 'token': '1:x'})
    ks = get_kite_store()
    strat = ks.put_strategy('a', {'name': 'ORB 15', 'mode': 'paper', 'version': 3, 'symbols': ['SBIN'],
                                  'portfolio_id': 'default', 'signals': []})
    return s, ks, strat, clock


def tg(text=None, data=None, uid=42, chat=42, mid=[100]):
    mid[0] += 1
    msg = {'chat': {'id': chat, 'type': 'private'}, 'from': {'id': uid}}
    if data is not None:
        return {'update_id': mid[0], 'callback_query': {'id': f'cb{mid[0]}', 'from': {'id': uid},
                                                        'data': data, 'message': msg}}
    return {'update_id': mid[0], 'message': {**msg, 'text': text}}


def send(payload, ch='telegram'):
    row = store.connection('a', ch)
    return service.receive(row['id'], 'hook', payload)


def last(ch='telegram'):
    return store.deliveries(store.connection('a', ch)['id'])[0]


def code(action, ch='telegram'):
    d = last(ch)
    for row in d.get('buttons') or []:
        for b in row:
            if b.get('callback_data', '').startswith('c:') and action in b['text'].lower():
                return b['callback_data'][2:]
    raise AssertionError(f'no {action} button in {d}')


def test_status_lists_owned_strategies_with_controls_and_no_paid_run(sup):
    s, ks, strat, _ = sup
    ks.put_strategy('b', {'name': 'Other user', 'mode': 'paper', 'version': 1, 'symbols': ['TCS'], 'signals': []})
    send(tg('/status'))
    d = last()
    assert 'ORB 15' in d['text'] and 'v3' in d['text'] and 'PAPER' in d['text']
    assert 'Other user' not in d['text']
    assert 'not armed' in d['text'].lower()           # shown, never changed
    assert code('pause')


def test_performance_uses_analytics_and_keeps_paper_and_live_apart(sup, monkeypatch):
    from apps.api.evaluation import analytics
    seen = []

    def fake(uid, **kw):
        seen.append((uid, kw['mode'], kw['portfolio_id']))
        return {'realized': {'pnl_net': 120.0 if kw['mode'] == 'paper' else -5.0, 'trades': 2},
                'open': {'positions': 1, 'marked': 0, 'unmarked': 1, 'pnl': 0.0},
                'trades': [], 'by_strategy': [], 'charges_note': 'estimates',
                'benchmark': {'available': False, 'reason': 'feed down', 'name': 'NIFTY 50'}}
    monkeypatch.setattr(analytics, 'trading_performance', fake)
    send(tg('performance'))
    text = last()['text']
    assert seen == [('a', 'paper', 'all'), ('a', 'live', 'default')]
    assert 'PAPER' in text and 'LIVE' in text and '120.00' in text and '-5.00' in text
    assert '1 open position without a fresh price' in text and 'feed down' in text


def test_pause_needs_confirmation_keeps_version_and_is_idempotent(sup):
    s, ks, strat, _ = sup
    send(tg('status'))
    tok = code('pause')
    send(tg(data='c:' + tok))
    assert ks.get_strategy(strat['id'], 'a')['mode'] == 'paper'      # not yet
    assert 'y:' + tok in str(last()['buttons'])
    send(tg(data='y:' + tok))
    got = ks.get_strategy(strat['id'], 'a')
    assert got['mode'] == 'off' and got['version'] == 3 and got['paused_from'] == 'paper'
    send(tg(data='y:' + tok))                                          # replayed tap
    assert ks.get_strategy(strat['id'], 'a')['mode'] == 'off'
    assert 'already' in last()['text'].lower() or 'paused' in last()['text'].lower()


def test_edit_since_card_or_kill_wins_over_remote_control(sup):
    s, ks, strat, clock = sup
    send(tg('status'))
    tok = code('pause')
    send(tg(data='c:' + tok))
    ks.put_strategy('a', dict(ks.get_strategy(strat['id'], 'a'), version=4))   # edited in app
    send(tg(data='y:' + tok))
    assert ks.get_strategy(strat['id'], 'a')['mode'] == 'paper'
    assert 'changed' in last()['text'].lower()

    ks.put_strategy('a', dict(ks.get_strategy(strat['id'], 'a'), mode='off', paused_from='confirm'))
    ks.put_control('a', {'killed_on': '2026-10-29'})
    send(tg('status'))
    tok = code('resume')
    send(tg(data='c:' + tok))
    send(tg(data='y:' + tok))
    assert ks.get_strategy(strat['id'], 'a')['mode'] == 'off'
    assert 'kill' in last()['text'].lower()


def test_resume_is_paper_by_default_confirm_only_explicitly_and_never_arms(sup):
    s, ks, strat, _ = sup
    ks.put_strategy('a', dict(strat, mode='off', paused_from='auto'))
    send(tg('status'))
    buttons = str(last()['buttons']).lower()
    assert 'auto' not in buttons
    tok = code('resume paper')
    send(tg(data='c:' + tok))
    send(tg(data='y:' + tok))
    got = ks.get_strategy(strat['id'], 'a')
    assert got['mode'] == 'paper' and got['version'] == 3
    assert not execution.control('a', NOW)['armed']

    ks.put_strategy('a', dict(got, mode='off', paused_from='confirm'))
    send(tg('status'))
    tok = code('resume confirm')
    send(tg(data='c:' + tok))
    send(tg(data='y:' + tok))
    assert ks.get_strategy(strat['id'], 'a')['mode'] == 'confirm'
    assert not execution.control('a', NOW)['armed']
    assert 'arm' in last()['text'].lower()                      # tells the user to arm in the app

    other = ks.put_strategy('a', {'name': 'Named', 'mode': 'off', 'paused_from': 'confirm', 'version': 1,
                                  'symbols': ['TCS'], 'portfolio_id': 'swing', 'signals': []})
    send(tg('status'))
    assert 'resume confirm' not in str([b['text'].lower() for r in last()['buttons'] for b in r
                                        if other['name'] in b['text']])


def test_tokens_are_bound_to_sender_connection_generation_and_expire(sup):
    s, ks, strat, clock = sup
    send(tg('status'))
    tok = code('pause')
    send(tg(data='c:' + tok, uid=43, chat=43))                    # stranger: ignored
    send(tg(data='y:' + tok, uid=43, chat=43))
    assert ks.get_strategy(strat['id'], 'a')['mode'] == 'paper'
    with pytest.raises(ValueError):                              # other user's code
        supervision.decide('b', 'telegram', tok, 'confirm')
    send(tg('PAUSE ' + tok))                                    # text form of the request
    clock['now'] = NOW + timedelta(minutes=5)
    send(tg('CONFIRM ' + tok))
    assert ks.get_strategy(strat['id'], 'a')['mode'] == 'paper'
    assert 'expired' in last()['text'].lower()

    send(tg('status'))
    tok = code('pause')
    row = store.connection('a', 'telegram'); row['generation'] = 'new'; store.save(row)   # re-paired
    send(tg(data='c:' + tok))
    send(tg(data='y:' + tok))
    assert ks.get_strategy(strat['id'], 'a')['mode'] == 'paper'


def test_supervision_requires_its_own_app_opt_in(sup):
    s, ks, strat, _ = sup
    row = store.connection('a', 'telegram'); row['supervision'] = False; store.save(row)
    send(tg('status'))
    assert 'ORB 15' not in last()['text'] and 'enable' in last()['text'].lower()
    s.save(s.new('c', 'telegram'))                               # unverified connection
    for pref in ('supervision', 'daily_summary', 'live_links'):
        with pytest.raises(ValueError):
            connections.preferences('c', 'telegram', False, False, **{pref: True})


def test_preferences_keep_old_two_flag_callers_and_show_new_state(sup):
    connections.preferences('a', 'telegram', True, False, supervision=True, daily_summary=True, live_links=True)
    x = connections.preferences('a', 'telegram', False, False)        # legacy call shape
    assert x['supervision'] and x['daily_summary'] and x['live_links'] and not x['alerts']


def test_daily_summary_once_per_trading_day_across_restarts(sup, monkeypatch):
    s, ks, strat, clock = sup
    from apps.api.evaluation import analytics
    calls = []
    monkeypatch.setattr(analytics, 'trading_performance', lambda uid, **kw: calls.append(kw) or {
        'realized': {'pnl_net': 0.0, 'trades': 0}, 'open': {'positions': 0, 'marked': 0, 'unmarked': 0, 'pnl': 0.0},
        'trades': [{'trade_date': '2026-10-29', 'status': 'closed', 'pnl_net': 50.0, 'mode': kw['mode']}],
        'by_strategy': [], 'benchmark': {'available': False, 'reason': 'x', 'name': 'NIFTY 50'}, 'charges_note': ''})
    row = store.connection('a', 'telegram'); row['daily_summary'] = True; store.save(row)
    supervision.daily_summaries()
    assert not [d for d in store.deliveries(row['id']) if 'summary' in d['id']]    # before close
    clock['now'] = NSE.local(datetime(2026, 10, 29).date(), 15 * 60 + 50)
    supervision.daily_summaries()
    supervision.daily_summaries()                       # another sweep / process restart
    sums = [d for d in store.deliveries(row['id']) if ':summary-' in d['id']]
    assert len(sums) == 1 and '2026-10-29' in sums[0]['text'] and '50.00' in sums[0]['text']
    n = len(calls)
    supervision.daily_summaries()
    assert len(calls) == n                              # already queued: no recompute
    wa = store.connection('a', 'whatsapp')
    assert not [d for d in store.deliveries(wa['id']) if ':summary-' in d['id']]   # not opted in
    clock['now'] = NSE.local(datetime(2026, 10, 31).date(), 16 * 60)              # Saturday
    supervision.daily_summaries()
    assert len([d for d in store.deliveries(row['id']) if ':summary-' in d['id']]) == 1


def test_live_signal_card_deep_links_to_typed_confirmation_without_approve(sup):
    s, ks, strat, clock = sup
    exp = (NOW + timedelta(minutes=2)).isoformat()
    ks.put_strategy('a', dict(strat, mode='confirm', signals=[
        {'id': 'sig1', 'status': 'await_confirm', 'symbol': 'SBIN', 'ticket_id': 't1', 'expires_at': exp,
         'at': NOW.isoformat(), 'date': '2026-10-29', 'order': {'qty': 3, 'price': 800, 'stop': 790, 'target': 820}}]))
    row = store.connection('a', 'telegram')
    supervision.live_signal_cards()
    assert not [d for d in store.deliveries(row['id']) if ':live-' in d['id']]   # needs its own opt-in
    row['live_links'] = True; store.save(row)
    supervision.live_signal_cards(); supervision.live_signal_cards()
    [card] = [d for d in store.deliveries(row['id']) if ':live-' in d['id']]
    assert 'SBIN' in card['text'] and 'typed' in card['text'].lower()
    flat = [b for r in card['buttons'] for b in r]
    assert all('callback_data' not in b for b in flat)
    assert flat[0]['url'] == 'https://app.example/desk/strategies'
    assert card['expires_at'] == pytest.approx((NOW + timedelta(minutes=2)).timestamp())


def test_existing_paper_approvals_still_work_alongside_supervision(sup, monkeypatch):
    from apps.api.desk.risk import fingerprint
    from apps.api.messaging import actions
    from apps.api.routes import desk
    monkeypatch.setattr(desk, '_now', lambda: NOW)
    order = {'symbol': 'RELIANCE', 'qty': 1, 'price': 1000, 'order_type': 'LIMIT', 'product': 'MIS',
             'stop': 990, 'target': 1020, 'plan': {'date': '2026-10-29', 'flat_by': '15:10', 'entry': 1000,
                                                   'risk_per_share': 10}}
    tk = get_desk_store().create_ticket('a', {'symbol': 'RELIANCE', 'status': 'ready', 'order': order,
                                              'fingerprint': fingerprint(order), 'plan_id': None})
    monkeypatch.setattr(desk, '_risk_for', lambda orders, uid: {
        'results': [{'verdict': 'PASS', 'fingerprint': fingerprint(orders[0])}], 'warnings': []})
    for ch in ('telegram', 'whatsapp'):
        row = store.connection('a', ch); row['approvals'] = True; store.save(row)
    actions.propose('a', tk['id'])
    tok = re.search(r'APPROVE ([A-Za-z0-9_-]+)', last()['text']).group(1)
    send(tg(data='a:' + tok))
    assert store.action('a', tk['id'])['state'] == 'approved'
    assert len(get_desk_store().list_paper('a')) == 1


def test_whatsapp_text_commands_use_the_same_confirmed_flow(sup, monkeypatch):
    s, ks, strat, _ = sup
    from apps.api.evaluation import analytics
    monkeypatch.setattr(analytics, 'trading_performance', lambda uid, **kw: {
        'realized': {'pnl_net': 1.0, 'trades': 1}, 'open': {'positions': 0, 'marked': 0, 'unmarked': 0, 'pnl': 0.0},
        'trades': [], 'benchmark': {'available': False, 'reason': 'x', 'name': 'NIFTY 50'}, 'charges_note': ''})
    row = store.connection('a', 'whatsapp')
    n = [0]

    def wa(text):
        n[0] += 1
        return service.receive(row['id'], 'hook', {'instance': row['instance'], 'event': 'messages.upsert', 'data': {
            'key': {'id': f'w{n[0]}', 'remoteJid': '919876543210@s.whatsapp.net'}, 'message': {'conversation': text}}})
    wa('PERFORMANCE')
    assert 'PAPER' in last('whatsapp')['text']
    wa('STATUS')
    tok = re.search(r'PAUSE ([A-Za-z0-9_-]{24})', last('whatsapp')['text']).group(1)
    wa('RESUME ' + tok)                                        # wrong word for a pause code
    assert ks.get_strategy(strat['id'], 'a')['mode'] == 'paper' and 'different action' in last('whatsapp')['text']
    wa('PAUSE ' + tok)
    wa('CONFIRM ' + tok)
    assert ks.get_strategy(strat['id'], 'a')['mode'] == 'off'


# ---- fix round 1: T4-1 generation pinning, T4-2 single confirmation window

def _repair(ch='telegram', chat='99'):
    row = store.connection('a', ch)
    row.update(generation='B-' + chat, chat=chat, sender=chat, supervision=True,
               daily_summary=True, live_links=True)
    store.save(row)
    return row


def _sent(monkeypatch):
    out = []
    monkeypatch.setattr(service, 'send', lambda doc, text, buttons=None: out.append((doc['chat'], text)) or 'm1')
    return out


def test_repair_during_deferred_performance_drops_the_old_reply(sup, monkeypatch):
    def compute(uid):
        _repair()                     # app re-pairs to chat 99 while marks are fetched
        return 'old chat 42 request result'
    monkeypatch.setattr(supervision, 'performance_text', compute)
    sent = _sent(monkeypatch)
    send(tg('performance'))
    cid = store.connection('a', 'telegram')['id']
    assert not [d for d in store.deliveries(cid) if 'old chat 42' in d['text']]
    service.flush(cid)
    assert not [x for x in sent if 'old chat 42' in x[1]]


def test_enqueue_refuses_a_changed_generation_and_keeps_current_work(sup):
    row = store.connection('a', 'telegram')
    assert store.enqueue(row['id'], 'sup-x', 'stale', generation='old') is False
    assert store.enqueue(row['id'], 'sup-y', 'fresh', generation=row['generation']) is True
    assert store.enqueue(row['id'], 'sup-z', 'legacy caller') is True     # default: current generation
    got = {d['id'].split(':', 1)[1]: d for d in store.deliveries(row['id'])}
    assert 'sup-x' not in got and got['sup-y']['generation'] == got['sup-z']['generation'] == row['generation']


def test_repair_during_summary_or_live_card_generation_queues_nothing(sup, monkeypatch):
    s, ks, strat, clock = sup
    from apps.api.evaluation import analytics
    row = store.connection('a', 'telegram')
    row.update(daily_summary=True, live_links=True); store.save(row)
    clock['now'] = NSE.local(datetime(2026, 10, 29).date(), 15 * 60 + 50)

    def perf(uid, **kw):
        _repair()
        return {'open': {'positions': 0}, 'trades': []}
    monkeypatch.setattr(analytics, 'trading_performance', perf)
    supervision.daily_summaries()
    assert not [d for d in store.deliveries(row['id']) if ':summary-' in d['id']]

    exp = (clock['now'] + timedelta(minutes=2)).isoformat()
    ks.put_strategy('a', dict(strat, mode='confirm', signals=[
        {'id': 'sig1', 'status': 'await_confirm', 'symbol': 'SBIN', 'expires_at': exp,
         'date': '2026-10-29', 'order': {'qty': 3}}]))
    stale = [dict(store.connection('a', 'telegram'), generation='A-before')]
    monkeypatch.setattr(store, 'all_connections', lambda: stale)   # producer read before the re-pair
    supervision.live_signal_cards()
    assert not [d for d in store.deliveries(row['id']) if ':live-' in d['id']]


@pytest.mark.parametrize('form', ['button', 'text'])
def test_repeated_requests_cannot_extend_the_confirmation(sup, form):
    s, ks, strat, clock = sup
    send(tg('status'))
    tok = code('pause')
    for i in range(11):                         # every 90 s, well past both deadlines
        clock['now'] = NOW + timedelta(seconds=90 * i)
        send(tg(data='c:' + tok) if form == 'button' else tg('PAUSE ' + tok))   # distinct updates
    clock['now'] = NOW + timedelta(minutes=16)
    send(tg(data='y:' + tok) if form == 'button' else tg('CONFIRM ' + tok))
    assert ks.get_strategy(strat['id'], 'a')['mode'] == 'paper'
    assert 'expired' in last()['text'].lower()


def test_repeat_request_inside_the_window_keeps_the_first_deadline(sup):
    s, ks, strat, clock = sup
    send(tg('status'))
    tok = code('pause')
    supervision.decide('a', 'telegram', tok, 'request')
    first = supervision._load('a', tok)['expires_at']
    clock['now'] = NOW + timedelta(seconds=90)
    supervision.decide('a', 'telegram', tok, 'request')
    assert supervision._load('a', tok)['expires_at'] == first
    clock['now'] = NOW + timedelta(seconds=110)
    supervision.decide('a', 'telegram', tok, 'confirm')
    assert ks.get_strategy(strat['id'], 'a')['mode'] == 'off'
