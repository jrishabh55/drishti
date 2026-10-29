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
        if '/connect/' in path: return {'base64':'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC', 'pairingCode':'12345678'}
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


def test_guided_telegram_alert_opt_in_prevents_duplicate_legacy_alerts(env):
    c,s,calls=env
    from apps.api.desk import notifications
    notifications.configure('a','123:legacy-secret','42',True)
    c.configure_telegram('a','123:secret')
    row=s.connection('a','telegram');row.update(verified=True,inbound_verified=True);s.save(row)
    c.preferences('a','telegram',True,False)
    assert not notifications.status('a')['enabled']


def test_legacy_bot_cannot_be_taken_over_by_another_user(env):
    c,s,calls=env
    from apps.api.desk import notifications
    notifications.configure('a','123:legacy-secret','42',True)
    with pytest.raises(ValueError,match='another'):c.configure_telegram('b','123:secret')


def test_qr_string_is_rendered_locally_as_a_real_png(env,monkeypatch):
    c,s,calls=env
    from apps.api.messaging import providers
    orig=providers.evolution
    monkeypatch.setattr(providers,'evolution',lambda base,key,path,*a,**kw: {'code':'2@test-real-qr-payload'} if '/connect/' in path else orig(base,key,path,*a,**kw))
    c.configure_whatsapp('a','','')
    import base64
    qr=c.pair_whatsapp('a')['qr']
    assert base64.b64decode(qr.split(',')[1]).startswith(b'\x89PNG\r\n\x1a\n')


def test_arbitrary_base64_is_not_certified_as_a_qr_image(env):
    c,s,calls=env
    from apps.api.messaging import providers
    with pytest.raises(providers.ProviderError):c._qr({'base64':'aGVsbG8='})


def test_failed_remote_cleanup_remains_visible_and_can_be_retried(env,monkeypatch):
    c,s,calls=env
    from apps.api.messaging import providers
    c.configure_telegram('a','123:secret');doc=s.connection('a','telegram')
    old=providers.telegram
    def fail(token,method,body=None):
        if method=='getWebhookInfo':return {'url':c.hook_url(doc)}
        if method=='deleteWebhook':raise providers.ProviderError()
        return old(token,method,body)
    monkeypatch.setattr(providers,'telegram',fail)
    c.disconnect('a','telegram')
    assert c.status('a','telegram')['cleanup_pending']
    with pytest.raises(ValueError,match='cleanup'):c.configure_telegram('a','123:secret',True)
    monkeypatch.setattr(providers,'telegram',old)
    assert not c.disconnect('a','telegram')['cleanup_pending']


def test_unsupported_nested_qr_shape_is_a_sanitized_provider_error(env):
    c,s,calls=env
    from apps.api.messaging import providers
    with pytest.raises(providers.ProviderError):c._qr({'qrcode':'unexpected payload'})


def test_recipient_verification_messages_are_throttled(env):
    c,s,calls=env
    c.configure_whatsapp('a','','');c.pair_whatsapp('a');c.refresh_whatsapp('a')
    c.verify_recipient('a','919876543210')
    with pytest.raises(ValueError,match='Wait'):c.verify_recipient('a','919876543210')


def test_refresh_before_qr_provisioning_is_a_local_setup_state_not_provider_error(env):
    c,s,calls=env;c.configure_whatsapp('a','','')
    assert not c.refresh_whatsapp('a')['connected']
    assert calls==[]
