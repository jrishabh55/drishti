from tests.test_messaging_connections import env
from apps.api.messaging import connections, store
import pytest


def _tg(env):
    c,s,calls=env
    c.configure_telegram('a','123:secret')
    pair=c.begin_telegram_pair('a')
    row=s.connection('a','telegram')
    return row,s.secret('a','telegram')['hook_secret'],pair['pair_url'].split('start=')[1]


def test_telegram_private_pair_requires_valid_secret_and_app_confirmation(env):
    from apps.api.messaging import service
    row,key,token=_tg(env)
    payload={'update_id':1,'message':{'chat':{'id':42,'type':'private'},'from':{'id':42,'first_name':'R'},'text':'/start '+token}}
    with pytest.raises(PermissionError):service.receive(row['id'],'wrong',payload)
    assert not store.connection('a','telegram').get('pending_sender')
    service.receive(row['id'],key,payload)
    assert not connections.status('a','telegram')['verified']
    assert connections.status('a','telegram')['pending_identity']=='R (42)'
    connections.confirm_pair('a','telegram')
    assert connections.status('a','telegram')['verified']
    service.receive(row['id'],key,payload)
    assert store.connection('a','telegram')['sender']=='42'


def test_pairing_rejects_groups_and_wrong_or_expired_token(env):
    from apps.api.messaging import service
    row,key,token=_tg(env)
    for t,typ in [(token,'group'),('wrong','private')]:
        service.receive(row['id'],key,{'update_id':t+typ,'message':{'chat':{'id':42,'type':typ},'from':{'id':42},'text':'/start '+t}})
    assert not store.connection('a','telegram').get('pending_sender')
    conf=store.secret('a','telegram');conf['pair_expires']=0;store.secret('a','telegram',conf)
    service.receive(row['id'],key,{'update_id':3,'message':{'chat':{'id':42,'type':'private'},'from':{'id':42},'text':'/start '+token}})
    assert not store.connection('a','telegram').get('pending_sender')


def test_telegram_webhook_and_setup_api_authentication(env,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from apps.api.routes.messaging import router
    from apps.api.auth import auth_middleware
    monkeypatch.setenv('WEBAPP_AUTH_TOKEN','app-token')
    app=FastAPI();app.middleware('http')(auth_middleware);app.include_router(router,prefix='/api')
    cl=TestClient(app)
    assert cl.get('/api/messaging/telegram').status_code==401
    row,key,token=_tg(env)
    path='/api/messaging/hooks/'+row['id']
    assert cl.post(path,json={}).status_code==403
    assert cl.post(path,json={},headers={'X-Telegram-Bot-Api-Secret-Token':key}).status_code==200
    assert cl.post(path,content='x'*66000,headers={'X-Telegram-Bot-Api-Secret-Token':key}).status_code==413
    assert cl.post(path,content='{',headers={'X-Telegram-Bot-Api-Secret-Token':key}).status_code==400


def test_whatsapp_incoming_link_requires_exact_instance_and_recipient_not_outgoing_echo(env):
    from apps.api.messaging import service
    c,s,_=env;c.configure_whatsapp('a','','');c.pair_whatsapp('a');c.refresh_whatsapp('a')
    doc=s.connection('a','whatsapp');doc.update(verified=True,recipient='919876543210');s.save(doc)
    secret=s.secret('a','whatsapp');secret.update(inbound_hash=c.digest('proof'),inbound_expires=s.now().timestamp()+300);s.secret('a','whatsapp',secret)
    def payload(instance,mid,jid,text):return {'event':'messages.upsert','instance':instance,'data':{'key':{'id':mid,'remoteJid':jid,'fromMe':True},'message':{'conversation':text}}}
    s.mark_seen(doc['id'],'echo')
    for p in [payload('other','1','919876543210@s.whatsapp.net','LINK proof'),
              payload(doc['instance'],'2','919800000000@s.whatsapp.net','LINK proof'),
              payload(doc['instance'],'echo','919876543210@s.whatsapp.net','LINK proof')]:
        service.receive(doc['id'],secret['hook_secret'],p)
    assert not c.status('a','whatsapp')['inbound_verified']
    service.receive(doc['id'],secret['hook_secret'],payload(doc['instance'],'valid','919876543210@s.whatsapp.net','LINK proof'))
    assert c.status('a','whatsapp')['inbound_verified']


def test_outbox_retry_cannot_duplicate_delivered_message_or_send_after_disconnect(env,monkeypatch):
    from apps.api.messaging import service,providers
    row,key,token=_tg(env)
    row.update(verified=True,chat='42',sender='42',alerts=True);store.save(row)
    store.enqueue(row['id'],'event','One event')
    sends=[]
    def fail(*a,**k):raise providers.ProviderError()
    monkeypatch.setattr(providers,'telegram',fail)
    service.flush(row['id'])
    assert not store.deliveries(row['id'])[0]['sent']
    d=store.deliveries(row['id'])[0];d['next_attempt']=0;store.save_delivery(d)
    def send(*a,**k):sends.append(a);return {'message_id':99}
    monkeypatch.setattr(providers,'telegram',send)
    service.flush(row['id']);service.flush(row['id'])
    assert len(sends)==1
    row['active']=False;store.save(row)
    service.flush(row['id'])
    assert len(sends)==1


def test_application_registers_messaging_routes(env):
    from apps.api.app import create_app
    assert '/api/messaging/{channel}' in create_app().openapi()['paths']


def test_delivery_loop_recovers_after_one_failed_sweep(env,monkeypatch):
    import asyncio
    from apps.api.messaging import service
    calls=[]
    async def sleep(_):
        if len(calls)>=2:raise asyncio.CancelledError()
    def sweep():
        calls.append(1)
        if len(calls)==1:raise RuntimeError('transient upkeep failure')
    monkeypatch.setattr(service.asyncio,'sleep',sleep)
    monkeypatch.setattr(service,'sweep',sweep)
    with pytest.raises(asyncio.CancelledError):asyncio.run(service.messaging_loop())
    assert len(calls)==2


def test_queued_alert_is_cancelled_when_user_opts_out_before_delivery(env,monkeypatch):
    from apps.api.messaging import service,providers
    row,key,token=_tg(env)
    row.update(verified=True,chat='42',sender='42',alerts=False);store.save(row)
    store.enqueue(row['id'],'alert-1','Old alert')
    sent=[];monkeypatch.setattr(providers,'telegram',lambda *a,**k:sent.append(a))
    service.flush(row['id'])
    assert sent==[]


def test_whatsapp_connection_update_revokes_approval_capability_when_offline(env):
    from apps.api.messaging import service
    c,s,_=env;c.configure_whatsapp('a','','');c.pair_whatsapp('a');c.refresh_whatsapp('a')
    doc=s.connection('a','whatsapp');doc.update(verified=True,approvals=True,inbound_verified=True);s.save(doc)
    key=s.secret('a','whatsapp')['hook_secret']
    service.receive(doc['id'],key,{'instance':doc['instance'],'event':'connection.update','data':{'state':'close'}})
    assert not c.status('a','whatsapp')['connected']
    assert not c.status('a','whatsapp')['approvals']
