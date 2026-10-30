"""Explicit market-data grants never delegate a broker session or order authority."""
import json

import pytest

from apps.api.kite import auth, live, rest
from apps.api.kite.store import get_kite_store
from tests.test_kite_connection import env, _connect, _no_secret, ist


def grant(client, recipient='user_b'):
    r = client.post('/api/kite/sharing/grants', json={'recipient_id': recipient, 'label': 'Test account'})
    assert r.status_code == 201, r.text


def select(client, owner='user_a'):
    r = client.put('/api/kite/sharing/source', json={'owner_id': owner})
    assert r.status_code == 200, r.text
    return r.json()


def test_explicit_grant_and_selection_share_quotes_without_sharing_credentials(env):
    client, user, fake, clock = env
    _connect(client, fake)
    grant(client)
    user['id'] = 'user_b'
    # A grant is not an implicit source switch.
    assert client.get('/api/kite/quote?symbols=SBIN').status_code == 409
    out = select(client)
    assert out['selected_owner_id'] == 'user_a'
    status = client.get('/api/kite/status')
    _no_secret(status.text, fake)
    assert status.json()['session'] == 'none'
    assert status.json()['app_configured'] is False
    assert status.json()['data_access']['shared'] is True
    assert status.json()['data_access']['session'] == 'active'
    quote = client.get('/api/kite/quote?symbols=SBIN')
    assert quote.status_code == 200, quote.text
    assert quote.json()['quotes']['SBIN']['last_price'] == 801.5
    _no_secret(quote.text, fake)
    with pytest.raises(auth.KiteAuthError):
        auth.client_for('user_b', clock['now'], fake)
    assert client.post('/api/kite/login').status_code == 409
    assert client.post('/api/kite/arm', json={'ack': 'ARM LIVE'}).status_code != 200
    assert client.post('/api/kite/live/start', json={'symbols': ['SBIN']}).status_code == 409
    assert client.post('/api/kite/live/stop').status_code == 200  # only stops caller's own hub


def test_ungranted_users_cannot_select_enumerate_or_transitively_share(env):
    client, user, fake, _ = env
    _connect(client, fake)
    grant(client)
    user['id'] = 'user_c'
    assert client.put('/api/kite/sharing/source', json={'owner_id': 'user_a'}).status_code == 404
    out = client.get('/api/kite/sharing').json()
    assert out['grants'] == [] and out['received'] == []
    user['id'] = 'user_b'
    select(client)
    assert client.post('/api/kite/sharing/grants', json={'recipient_id': 'user_c'}).status_code == 409
    assert client.delete('/api/kite/sharing/grants/user_b').status_code == 404


def test_owner_revocation_removes_selection_and_immediately_stops_shared_reads(env):
    client, user, fake, _ = env
    _connect(client, fake)
    grant(client)
    user['id'] = 'user_b'
    select(client)
    user['id'] = 'user_a'
    assert client.delete('/api/kite/sharing/grants/user_b').status_code == 204
    user['id'] = 'user_b'
    assert client.get('/api/kite/sharing').json()['selected_owner_id'] is None
    assert client.get('/api/kite/quote?symbols=SBIN').status_code == 409


def test_shared_quote_rechecks_access_after_the_upstream_call(env, monkeypatch):
    client, user, fake, _ = env
    _connect(client, fake)
    grant(client)
    user['id'] = 'user_b'
    select(client)
    def upstream(_self, _symbols):
        get_kite_store().revoke_data_access('user_a', 'user_b')
        return {'SBIN': {'last_price': 777.0}}
    monkeypatch.setattr(rest.KiteClient, 'quote', upstream)
    r = client.get('/api/kite/quote?symbols=SBIN')
    assert r.status_code == 409
    assert '777' not in r.text


def test_expired_owner_session_cannot_supply_shared_quotes(env):
    client, user, fake, clock = env
    _connect(client, fake)
    grant(client)
    user['id'] = 'user_b'
    select(client)
    clock['now'] = ist(6, 0, d=clock['now'].astimezone(live.IST).date()).replace(day=29)
    assert client.get('/api/kite/quote?symbols=SBIN').status_code == 409
    assert client.get('/api/kite/status').json()['data_access']['session'] == 'expired'


def test_shared_token_error_revokes_the_owner_only(env):
    client, user, fake, _ = env
    _connect(client, fake)
    grant(client)
    user['id'] = 'user_b'
    select(client)
    fake.revoked.add(fake.tokens_issued[-1])
    assert client.get('/api/kite/quote?symbols=SBIN').status_code == 409
    assert client.get('/api/kite/status').json()['session'] == 'none'
    user['id'] = 'user_a'
    assert client.get('/api/kite/status').json()['session'] == 'revoked'


def test_forgetting_or_replacing_owner_credentials_removes_old_grants(env):
    client, user, fake, _ = env
    _connect(client, fake)
    grant(client)
    assert client.put('/api/kite/app', json={'api_key': fake.api_key, 'api_secret': fake.api_secret}).status_code == 200
    assert client.get('/api/kite/sharing').json()['grants'] == []
    grant(client)
    assert client.delete('/api/kite/app').status_code == 200
    assert client.get('/api/kite/sharing').json()['grants'] == []


@pytest.mark.parametrize('recipient', ['user_a', 'anonymous', 'shared-bearer'])
def test_sharing_rejects_self_and_non_individual_identities(env, recipient):
    client, _, fake, _ = env
    _connect(client, fake)
    assert client.post('/api/kite/sharing/grants', json={'recipient_id': recipient}).status_code == 422


def test_shared_live_bars_are_private_fresh_and_revocable(env):
    client, user, fake, clock = env
    _connect(client, fake)
    grant(client)
    user['id'] = 'user_b'
    select(client)
    clock['now'] = ist(9, 20, 3)
    hub = live.Hub('user_a', ['SBIN'], state='live', backfilled={'SBIN'})
    hub.builder.on_tick('SBIN', 800.0, 1000, ist(9, 19, 30))
    live._hubs['user_a'] = hub
    series = live.provider('user_b', 'SBIN', '1m', 'session', now=clock['now'])
    assert series is not None and series.source == 'live:kite'
    assert series.rows[-1][4] == 800.0
    assert live.provider('user_c', 'SBIN', '1m', 'session', now=clock['now']) is None
    assert live.provider('user_b', 'TCS', '1m', 'session', now=clock['now']) is None
    assert live.provider('user_b', 'SBIN', '1m', 'session', now=ist(9, 22)) is None
    get_kite_store().revoke_data_access('user_a', 'user_b')
    assert live.provider('user_b', 'SBIN', '1m', 'session', now=clock['now']) is None


def test_shared_history_is_rechecked_after_blocking_fetch(env, monkeypatch):
    client, user, fake, clock = env
    _connect(client, fake)
    grant(client)
    user['id'] = 'user_b'
    select(client)
    fake.candles = [[ist(9, 15).isoformat(), 100, 101, 99, 100.5, 1000]]
    series = live.provider('user_b', 'SBIN', '5m', 'history', now=clock['now'], transport=fake)
    assert series is not None and series.rows[0][4] == 100.5
    def revoked(*args, **kwargs):
        get_kite_store().revoke_data_access('user_a', 'user_b')
        return fake.candles
    monkeypatch.setattr(rest.KiteClient, 'historical', revoked)
    assert live.provider('user_b', 'SBIN', '5m', 'history', now=clock['now'], transport=fake) is None


def test_shared_freshness_redacts_owner_failure_details(env):
    client, user, fake, clock = env
    _connect(client, fake)
    grant(client)
    user['id'] = 'user_b'
    select(client)
    hub = live.Hub('user_a', ['SBIN'], state='reconnecting', detail=fake.api_secret)
    hub.backfill_errors['SBIN'] = fake.tokens_issued[0]
    live._hubs['user_a'] = hub
    response = client.get('/api/kite/status')
    _no_secret(response.text, fake)
    assert response.json()['data_access']['live']['detail'] is None


def test_status_rechecks_grant_after_reading_stream(env, monkeypatch):
    client, user, fake, _ = env
    _connect(client, fake)
    grant(client)
    user['id'] = 'user_b'
    select(client)
    original = live.freshness
    def revoked(owner_id, now):
        result = original(owner_id, now)
        if owner_id == 'user_a':
            get_kite_store().revoke_data_access('user_a', 'user_b')
        return result
    monkeypatch.setattr(live, 'freshness', revoked)
    access = client.get('/api/kite/status').json()['data_access']
    assert access['owner_id'] == 'user_b'
    assert access['shared'] is False
    assert access['session'] == 'none'


def test_history_crossing_daily_expiry_does_not_return_shared_bars(env, monkeypatch):
    client, user, fake, clock = env
    _connect(client, fake)
    grant(client)
    user['id'] = 'user_b'
    select(client)
    session = auth.load_session('user_a', clock['now'])
    from datetime import datetime, timedelta
    expires = datetime.fromisoformat(session['expires_at'])
    clock['now'] = expires - timedelta(seconds=1)
    monkeypatch.setattr(live, '_now', lambda: clock['now'])
    def expires_during_fetch(*args, **kwargs):
        clock['now'] = expires + timedelta(seconds=1)
        return [[ist(9, 15).isoformat(), 100, 101, 99, 100.5, 1000]]
    monkeypatch.setattr(rest.KiteClient, 'historical', expires_during_fetch)
    assert live.provider('user_b', 'SBIN', '5m', 'history', transport=fake) is None


def test_recipient_cannot_read_owners_private_grant_label(env):
    client, user, fake, _ = env
    _connect(client, fake)
    grant(client)
    user['id'] = 'user_b'
    received = client.get('/api/kite/sharing').json()['received']
    assert received and received[0]['owner_id'] == 'user_a'
    assert 'label' not in received[0]


def test_recipient_can_stop_only_their_own_existing_stream(env):
    client, user, fake, _ = env
    _connect(client, fake)
    grant(client)
    owner_hub = live.Hub('user_a', ['SBIN'], state='live')
    own_hub = live.Hub('user_b', ['SBIN'], state='live')
    live._hubs.update(user_a=owner_hub, user_b=own_hub)
    user['id'] = 'user_b'
    select(client)
    assert client.post('/api/kite/live/stop').status_code == 200
    assert live.get_hub('user_b') is None
    assert live.get_hub('user_a') is owner_hub
