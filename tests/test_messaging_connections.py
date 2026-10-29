import json
import pytest
from cryptography.fernet import Fernet
from apps.api.jobs.store import reset_store_for_tests
from apps.api.desk.store import reset_desk_store_for_tests


@pytest.fixture
def env(tmp_path, monkeypatch):
    reset_store_for_tests(tmp_path/'jobs.sqlite')
    reset_desk_store_for_tests(tmp_path/'desk.sqlite')
    monkeypatch.setenv('WEBAPP_KEY_ENCRYPTION_SECRET', Fernet.generate_key().decode())
    monkeypatch.setenv('EVOLUTION_API_URL', 'https://evo.example')
    monkeypatch.setenv('EVOLUTION_API_KEY', 'server-secret')
    monkeypatch.setenv('MESSAGING_PUBLIC_BASE_URL', 'https://app.example')
    from apps.api.messaging import connections as c, providers, store
    calls=[]
    def evolution(base,key,path,method='GET',body=None,params=None):
        calls.append((path,method,body,params))
        if path=='/instance/create': return {'instance': {'instanceName':body['instanceName']}}
        if '/connect/' in path: return {'base64':'data:image/png;base64,aGVsbG8=', 'pairingCode':'12345678'}
        if '/connectionState/' in path:return {'instance':{'state':'open'}}
        if path=='/instance/fetchInstances': return [{'name':params['instanceName'], 'ownerJid':'919876543210@s.whatsapp.net'}]
        return {'status':'SUCCESS'}
    def telegram(token,method,body=None):
        calls.append((method,body))
        if method=='getMe':return {'id':123,'username':'drishti_test_bot'}
        if method=='getWebhookInfo':return {'url':''}
        return True
    monkeypatch.setattr(providers,'evolution',evolution)
    monkeypatch.setattr(providers,'telegram',telegram)
    return c,store,calls


def test_status_is_scoped_and_does_not_expose_server_key(env):
    c,s,calls=env
    x=c.status('a','whatsapp')
    assert x['server_url']=='https://evo.example' and x['server_configured']
    assert 'server-secret' not in json.dumps(x)
    c.configure_whatsapp('a','','')
    assert c.status('a','whatsapp')['configured']
    assert not c.status('b','whatsapp')['configured']


@pytest.mark.parametrize('url',['http://127.0.0.1','https://evo.example@evil.example',
    'https://evo.example/?x=y','https://evil.example','https://evo.example/#bad'])
def test_untrusted_url_is_rejected_before_network(env,url):
    c,s,calls=env
    with pytest.raises(ValueError):c.configure_whatsapp('a',url,'key')
    assert calls==[]


def test_whatsapp_instance_is_reused_owned_and_pairing_expires(env):
    c,s,calls=env
    c.configure_whatsapp('a','','')
    first=c.pair_whatsapp('a','919876543210')
    assert first['pairing_code']=='12345678' and first['qr'].startswith('data:image/png;base64,')
    row=s.connection('a','whatsapp')
    assert row['instance'].startswith('drishti-')
    c.pair_whatsapp('a','919876543210')
    assert len([x for x in calls if x[0]=='/instance/create'])==1
    assert first['pairing_expires_at']
    assert c.status('b','whatsapp').get('instance') is None
    assert c.refresh_whatsapp('a')['connected']
    assert c.status('a','whatsapp')['phone']=='919876543210'


def test_telegram_foreign_webhook_requires_explicit_replacement(env,monkeypatch):
    c,s,calls=env
    from apps.api.messaging import providers
    def telegram(token,method,body=None):
        if method=='getMe':return {'id':123,'username':'test_bot'}
        if method=='getWebhookInfo':return {'url':'https://another-app.example/hook'}
        return True
    monkeypatch.setattr(providers,'telegram',telegram)
    with pytest.raises(ValueError,match='webhook'):c.configure_telegram('a','123:secret')
    assert not c.status('a','telegram')['configured']
    assert c.configure_telegram('a','123:secret',True)['bot_username']=='test_bot'


def test_bot_cannot_be_claimed_by_two_users_and_pair_token_is_revoked(env):
    c,s,calls=env
    c.configure_telegram('a','123:secret')
    with pytest.raises(ValueError,match='another'):c.configure_telegram('b','123:secret')
    pair=c.begin_telegram_pair('a')
    assert pair['pair_url'].startswith('https://t.me/drishti_test_bot?start=')
    assert '123:secret' not in json.dumps(pair)
    row=s.connection('a','telegram')
    c.disconnect('a','telegram')
    assert not c.status('a','telegram')['configured']
    assert s.by_id(row['id']) is None


def test_provider_rejects_redirect_and_never_leaks_exception_url(monkeypatch):
    from apps.api.messaging import providers
    import requests
    monkeypatch.setenv('EVOLUTION_API_URL','https://evo.example')
    def fail(*a,**k):raise requests.Timeout('https://api.telegram.org/bot123:secret/getMe')
    monkeypatch.setattr(requests,'request',fail)
    with pytest.raises(providers.ProviderError) as e:providers.telegram('123:secret','getMe')
    assert 'secret' not in str(e.value)
    class R:
        status_code=302
    monkeypatch.setattr(requests,'request',lambda *a,**k:R())
    with pytest.raises(providers.ProviderError):providers.evolution('https://evo.example','key','/instance/create')
