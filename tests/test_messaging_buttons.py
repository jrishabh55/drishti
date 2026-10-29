"""Evolution 2.3.7 wire contract and real paper decisions; no provider traffic."""
import json
from datetime import timedelta
import pytest
from tests.test_messaging_connections import env
from tests.test_messaging_actions import ready, token_for
from apps.api.messaging import actions, providers, service, store
from apps.api.desk.store import get_desk_store


def proposal(ready):
    tk, now = ready
    row = store.connection('a', 'whatsapp')
    row.update(instance='drishti-'+'a'*32, hook_ready=True)
    store.save(row)
    store.secret('a', 'whatsapp', {'hook_secret': 'test-hook'})
    actions.propose('a', tk['id'])
    return row, tk, token_for('a', tk['id'])


def inbound(row, message, mid='reply', jid='919876543210@s.whatsapp.net', instance=None):
    return {'instance': instance or row['instance'], 'event': 'messages.upsert',
            'data': {'key': {'id': mid, 'remoteJid': jid, 'fromMe': False}, 'message': message}}


def native(value):
    return {'interactiveResponseMessage': {'nativeFlowResponseMessage':
            {'name': 'quick_reply', 'paramsJson': json.dumps({'id': value})}}}


def test_proposal_sends_three_evolution_reply_buttons_with_original_codes_and_plan(ready, env, monkeypatch):
    row, tk, token = proposal(ready)
    emitted = []
    def provider(base, key, path, method='GET', body=None, params=None):
        emitted.append((path, body))
        return {'key': {'id': 'outbound-1'}}
    monkeypatch.setattr(providers, 'evolution', provider)
    service.flush(row['id'])
    assert len(emitted) == 1
    path, body = emitted[0]
    assert path == '/message/sendButtons/'+row['instance']
    assert body['number'] == '919876543210'
    assert body['buttons'] == [
        {'type': 'reply', 'displayText': 'Approve paper trade', 'id': 'a:'+token},
        {'type': 'reply', 'displayText': 'Reject', 'id': 'r:'+token},
        {'type': 'reply', 'displayText': 'Status', 'id': 's:'+token}]
    assert 'APPROVE '+token in body['description']
    assert 'https://app.example/desk/plans' in body['description']
    assert store.deliveries(row['id'])[0]['message_id'] == 'outbound-1'


@pytest.mark.parametrize('kind', ['native', 'legacy'])
def test_button_callback_creates_one_paper_trade_across_duplicate_and_other_channel_replies(ready, kind):
    row, tk, token = proposal(ready)
    message = native('a:'+token) if kind == 'native' else {'buttonsResponseMessage': {'selectedButtonId': 'a:'+token}}
    payload = inbound(row, message)
    service.receive(row['id'], 'test-hook', payload)
    assert store.action('a', tk['id'])['state'] == 'approved'
    assert len(get_desk_store().list_paper('a')) == 1
    service.receive(row['id'], 'test-hook', payload)
    service.receive(row['id'], 'test-hook', inbound(row, message, mid='second-click'))
    assert actions.decide('a', 'telegram', token, 'approve', '42')['state'] == 'approved'
    assert store.action('a', tk['id'])['state'] == 'approved'
    assert len(get_desk_store().list_paper('a')) == 1


@pytest.mark.parametrize('command, expected', [('r:', 'rejected'), ('s:', 'pending')])
def test_native_reject_and_status_use_existing_paper_state_machine(ready, command, expected):
    row, tk, token = proposal(ready)
    service.receive(row['id'], 'test-hook', inbound(row, native(command+token)))
    assert store.action('a', tk['id'])['state'] == expected
    assert get_desk_store().list_paper('a') == []
    if expected == 'rejected':
        assert get_desk_store().get_ticket(tk['id'], 'a')['status'] == 'cancelled'
    else:
        assert any('Waiting for your paper-trade decision.' in d['text'] for d in store.deliveries(row['id']))


@pytest.mark.parametrize('invalid', ['{', 'null', '[]', '42', '{"id":42}', '{"id":{}}', 'x'*1100])
def test_malformed_native_reply_is_ignored_without_a_trade_or_callback_crash(ready, invalid):
    row, tk, token = proposal(ready)
    message = {'interactiveResponseMessage': {'nativeFlowResponseMessage': {'name': 'quick_reply', 'paramsJson': invalid}}}
    assert service.receive(row['id'], 'test-hook', inbound(row, message)) == {'ok': True}
    assert store.action('a', tk['id'])['state'] == 'pending'
    assert get_desk_store().list_paper('a') == []


@pytest.mark.parametrize('kind', ['native', 'legacy'])
def test_forwarded_button_reply_cannot_approve(ready, kind):
    row, tk, token = proposal(ready)
    message = native('a:'+token) if kind == 'native' else {'buttonsResponseMessage': {'selectedButtonId': 'a:'+token}}
    message[next(iter(message))]['contextInfo'] = {'isForwarded': True}
    service.receive(row['id'], 'test-hook', inbound(row, message))
    assert store.action('a', tk['id'])['state'] == 'pending'
    assert get_desk_store().list_paper('a') == []


@pytest.mark.parametrize('reason', ['sender', 'instance', 'generation', 'expired', 'secret', 'disabled', 'risk'])
def test_native_approval_preserves_existing_authorization_expiry_and_risk_gates(ready, monkeypatch, reason):
    row, tk, token = proposal(ready)
    payload = inbound(row, native('a:'+token))
    if reason == 'sender': payload['data']['key']['remoteJid'] = '919800000000@s.whatsapp.net'
    if reason == 'instance': payload['instance'] = 'other'
    if reason == 'generation': row['generation'] = 'changed'; store.save(row)
    if reason == 'expired': monkeypatch.setattr(store, 'now', lambda: ready[1]+timedelta(minutes=6))
    if reason == 'disabled': row['approvals'] = False; store.save(row)
    if reason == 'risk':
        from apps.api.routes import desk
        monkeypatch.setattr(desk, '_risk_for', lambda *a: {'results': [
            {'verdict': 'BLOCK', 'fingerprint': tk['fingerprint'], 'reasons': ['Current risk limit']}], 'warnings': []})
    if reason == 'secret':
        with pytest.raises(PermissionError): service.receive(row['id'], 'wrong', payload)
    else:
        service.receive(row['id'], 'test-hook', payload)
    assert store.action('a', tk['id'])['state'] != 'approved'
    assert get_desk_store().list_paper('a') == []
    if reason in ('risk', 'expired'):
        assert store.action('a', tk['id'])['state'] == ('blocked' if reason == 'risk' else 'expired')


@pytest.mark.parametrize('status', [404, 405, 501])
def test_unsupported_buttons_fall_back_to_original_text_codes(ready, monkeypatch, status):
    row, tk, token = proposal(ready)
    emitted = []
    def provider(doc, path, method='GET', body=None):
        emitted.append((path, body))
        if '/sendButtons/' in path: raise providers.ProviderError(status=status)
        return {'key': {'id': 'text-fallback'}}
    monkeypatch.setattr(service.c, 'evolution', provider)
    service.flush(row['id'])
    assert [path.split('/')[2] for path, body in emitted] == ['sendButtons', 'sendText']
    assert 'APPROVE '+token in emitted[1][1]['text']
    assert store.deliveries(row['id'])[0]['message_id'] == 'text-fallback'


@pytest.mark.parametrize('status', [None, 400, 401, 403, 408, 429, 500, 502])
def test_ambiguous_or_authentication_failure_does_not_send_a_second_text(ready, monkeypatch, status):
    row, tk, token = proposal(ready)
    emitted = []
    def provider(doc, path, method='GET', body=None):
        emitted.append(path)
        raise providers.ProviderError(status=status)
    monkeypatch.setattr(service.c, 'evolution', provider)
    service.flush(row['id'])
    assert emitted == ['/message/sendButtons/'+row['instance']]
    delivery = store.deliveries(row['id'])[0]
    assert not delivery['sent'] and delivery['attempts'] == 1
    assert store.by_id(row['id'])['last_error']
