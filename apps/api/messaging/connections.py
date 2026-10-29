"""User-owned connection lifecycle; remote account linking always needs the phone."""
import base64
import hashlib
import io
import os
import re
import secrets
from apps.api import user_keys
from apps.api.kite.execution import user_lock
from apps.api.messaging import providers, store


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def number(value):
    n = re.sub(r'[ +()\-]', '', value)
    if not re.fullmatch(r'[1-9][0-9]{7,14}', n):
        raise ValueError('Enter a phone number with country code, for example 919876543210')
    return n


def _owned(uid, channel):
    doc = store.connection(uid,channel)
    if not doc:
        raise ValueError('Connect this messaging channel first')
    return doc


def _save(doc):
    store.save(doc)
    return status(doc['user_id'],doc['channel'])


def status(uid, channel):
    doc = store.connection(uid,channel) or {}
    conf = store.secret(uid,channel)
    x = {k:doc.get(k) for k in ('id','connected','verified','alerts','approvals','last_error','bot_username',
                               'phone','recipient','pending_identity','inbound_verified','pairing_expires_at','cleanup_pending')}
    x.update(configured=bool(doc),encryption_available=user_keys.encryption_enabled())
    if channel == 'whatsapp':
        x.update(server_url=conf.get('url') or os.getenv('EVOLUTION_API_URL',''),
                 server_configured=bool((conf.get('key') or os.getenv('EVOLUTION_API_KEY','')) and
                                        (conf.get('url') or os.getenv('EVOLUTION_API_URL',''))),
                 credential_source='personal' if conf.get('key') else 'server')
        if conf.get('pairing_expires_at',0) > store.now().timestamp():
            x.update(qr=conf.get('qr'),pairing_code=conf.get('pairing_code'))
    elif conf.get('pair_expires',0)>store.now().timestamp():
        x['pair_url']=conf.get('pair_url')
    return x


def evolution(doc, path, method='GET', body=None, params=None):
    conf=store.secret(doc['user_id'],'whatsapp')
    return providers.evolution(conf.get('url',''),conf.get('key') or os.getenv('EVOLUTION_API_KEY',''),
                               path,method,body,params)


def hook_url(doc):
    return providers.public_base()+'/api/messaging/hooks/'+doc['id']


def configure_whatsapp(uid, url='', key=''):
    with user_lock(uid):
        base = providers.trusted_base(url)
        old=store.connection(uid,'whatsapp')
        conf=store.secret(uid,'whatsapp')
        if old and (base != conf.get('url') or key.strip() and key.strip()!=conf.get('key')):
            raise ValueError('Disconnect WhatsApp before changing its server or credentials')
        if not (key.strip() or conf.get('key') or os.getenv('EVOLUTION_API_KEY','')):
            raise ValueError('Enter an Evolution API key or ask the administrator to configure it')
        providers.public_base()
        doc=old or store.new(uid,'whatsapp')
        doc.setdefault('instance','drishti-'+secrets.token_hex(16))
        conf.update(url=base,key=key.strip() or conf.get('key',''))
        conf.setdefault('hook_secret',secrets.token_urlsafe(32))
        store.secret(uid,'whatsapp',conf)
        return _save(doc)


def _qr(result):
    candidate=result.get('base64') or (result.get('qrcode') or {}).get('base64')
    if candidate:
        if not isinstance(candidate,str) or len(candidate)>500000:
            raise providers.ProviderError('Evolution returned an invalid QR image')
        raw=candidate.removeprefix('data:image/png;base64,')
        try:
            base64.b64decode(raw,validate=True)
        except Exception:
            raise providers.ProviderError('Evolution returned an invalid QR image') from None
        return 'data:image/png;base64,'+raw
    code=result.get('code') or (result.get('qrcode') or {}).get('code')
    if code and isinstance(code,str) and len(code)<10000:
        import qrcode
        buf=io.BytesIO()
        qrcode.make(code).save(buf,format='PNG')
        return 'data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()
    return None


def pair_whatsapp(uid, phone=''):
    with user_lock(uid):
        doc=_owned(uid,'whatsapp')
        conf=store.secret(uid,'whatsapp')
        n=number(phone) if phone else None
        if conf.get('pairing_expires_at',0)>store.now().timestamp()+60:
            return status(uid,'whatsapp')
        if not doc.get('created'):
            # Ownership/name is durable before POST; an ambiguous create is
            # recovered by querying only this exact instance on the next try.
            if doc.get('create_attempted'):
                try:
                    evolution(doc,'/instance/connectionState/'+doc['instance'])
                    doc['created']=True
                except providers.ProviderError as exc:
                    if exc.status != 404:
                        raise
            if not doc.get('created'):
                doc['create_attempted']=True
                store.save(doc)
                evolution(doc,'/instance/create','POST',{'instanceName':doc['instance'],
                          'integration':'WHATSAPP-BAILEYS','qrcode':False,'syncFullHistory':False})
                doc['created']=True
                store.save(doc)
        evolution(doc,'/webhook/set/'+doc['instance'],'POST',{'webhook':{
            'enabled':True,'url':hook_url(doc),'byEvents':False,'base64':False,
            'headers':{'X-Drishti-Webhook-Secret':conf['hook_secret']},
            'events':['MESSAGES_UPSERT','CONNECTION_UPDATE']}})
        doc['hook_ready']=True
        result=evolution(doc,'/instance/connect/'+doc['instance'],params={'number':n} if n else None)
        if not isinstance(result,dict):
            raise providers.ProviderError('Evolution returned an unsupported pairing response')
        qr=_qr(result)
        pairing=result.get('pairingCode') or (result.get('qrcode') or {}).get('pairingCode')
        if not qr and not pairing:
            return refresh_whatsapp(uid)
        expiry=store.now().timestamp()+90
        conf.update(qr=qr,pairing_code=str(pairing) if pairing else None,pairing_expires_at=expiry)
        doc.update(pairing_expires_at=expiry,last_error=None)
        store.secret(uid,'whatsapp',conf)
        return _save(doc)


def refresh_whatsapp(uid):
    with user_lock(uid):
        doc=_owned(uid,'whatsapp')
        r=evolution(doc,'/instance/connectionState/'+doc['instance'])
        state=r.get('instance',{}).get('state') if isinstance(r,dict) else None
        if state not in ('open','close','connecting'):
            raise providers.ProviderError('Evolution returned an unsupported connection state')
        doc['connected']=state=='open'
        if doc['connected']:
            rows=evolution(doc,'/instance/fetchInstances',params={'instanceName':doc['instance']})
            if not isinstance(rows,list):
                raise providers.ProviderError('Evolution returned unsupported instance details')
            for r in rows:
                item=r.get('instance',r)
                if (item.get('name') or item.get('instanceName'))!=doc['instance']:
                    continue
                owner=item.get('ownerJid') or item.get('owner') or ''
                if re.fullmatch(r'[1-9][0-9]{7,14}@s\.whatsapp\.net',owner):
                    doc['phone']=owner.split('@')[0]
                    doc.setdefault('recipient',doc['phone'])
            conf=store.secret(uid,'whatsapp')
            for k in ('qr','pairing_code','pairing_expires_at'):
                conf.pop(k,None)
            store.secret(uid,'whatsapp',conf)
        else:
            doc.update(verified=False,approvals=False,inbound_verified=False)
        return _save(doc)


def configure_telegram(uid, token, replace_webhook=False):
    with user_lock(uid):
        if not user_keys.encryption_enabled():
            raise ValueError('Encrypted credential storage needs administrator setup')
        old=store.connection(uid,'telegram')
        conf=store.secret(uid,'telegram')
        token=token.strip() or conf.get('token','')
        if old and token != conf.get('token'):
            raise ValueError('Disconnect Telegram before replacing its bot')
        me=providers.telegram(token,'getMe')
        if not me.get('id') or not re.fullmatch(r'[A-Za-z0-9_]+',me.get('username','')):
            raise providers.ProviderError('Telegram returned unsupported bot details')
        from apps.api.jobs.store import get_store
        from apps.api.desk import notifications
        with get_store()._conn() as db:
            legacy_users=[r['user_id'] for r in db.execute("SELECT user_id FROM user_api_keys WHERE provider='telegram' AND user_id<>?",(uid,))]
        if any((notifications._config(other) or {}).get('token','').split(':')[0]==str(me['id']) for other in legacy_users):
            raise ValueError('This bot is already connected to another user')
        doc=old or store.new(uid,'telegram')
        doc.update(bot_id=str(me['id']),bot_username=me['username'])
        url=hook_url(doc)
        hook=providers.telegram(token,'getWebhookInfo')
        if hook.get('url') and hook['url']!=url and not replace_webhook:
            raise ValueError('This bot already has a webhook in another app. Explicitly allow replacement or create a separate bot.')
        conf.update(token=token)
        conf.setdefault('hook_secret',secrets.token_urlsafe(32))
        # The unique bot claim happens before altering its remote webhook.
        store.save(doc)
        store.secret(uid,'telegram',conf)
        providers.telegram(token,'setWebhook',{'url':url,'secret_token':conf['hook_secret'],
                                             'allowed_updates':['message','callback_query']})
        doc.update(connected=True,hook_ready=True,last_error=None)
        return _save(doc)


def begin_telegram_pair(uid):
    with user_lock(uid):
        doc=_owned(uid,'telegram')
        if not doc.get('hook_ready'):
            raise ValueError('Save and validate the Telegram bot first')
        token=secrets.token_urlsafe(24)
        conf=store.secret(uid,'telegram')
        conf.update(pair_hash=digest(token),pair_expires=store.now().timestamp()+300,
                    pair_url='https://t.me/'+doc['bot_username']+'?start='+token)
        doc.pop('pending_identity',None)
        doc.pop('pending_sender',None)
        store.secret(uid,'telegram',conf)
        return _save(doc)


def verify_recipient(uid, recipient):
    with user_lock(uid):
        doc=_owned(uid,'whatsapp')
        if not doc.get('connected'):
            raise ValueError('Link WhatsApp first')
        n=number(recipient or doc.get('phone',''))
        code=''.join(secrets.choice('0123456789') for _ in range(6))
        conf=store.secret(uid,'whatsapp')
        conf.update(challenge_hash=digest(code),challenge_expires=store.now().timestamp()+300,challenge_recipient=n,challenge_attempts=0)
        store.secret(uid,'whatsapp',conf)
        result=evolution(doc,'/message/sendText/'+doc['instance'],'POST',{'number':n,'text':'Drishti connection code: '+code+'\nEnter this code in Drishti. Expires in 5 minutes.'})
        mid=(result.get('key') or {}).get('id') if isinstance(result,dict) else None
        if mid:
            store.mark_seen(doc['id'],str(mid))
        return {'sent':True}


def confirm_pair(uid, channel, code=''):
    with user_lock(uid):
        doc=_owned(uid,channel)
        conf=store.secret(uid,channel)
        if channel=='telegram':
            if not doc.get('pending_sender') or conf.get('pair_expires',0)<store.now().timestamp():
                raise ValueError('Open the Telegram connection link first, or generate a new link')
            doc.update(sender=doc.pop('pending_sender'),chat=doc.pop('pending_chat'),verified=True,inbound_verified=True)
            doc.pop('pending_identity',None)
            for k in ('pair_hash','pair_url','pair_expires'):
                conf.pop(k,None)
        else:
            attempts=conf.get('challenge_attempts',0)+1
            conf['challenge_attempts']=attempts
            store.secret(uid,channel,conf)
            if (attempts>5 or conf.get('challenge_expires',0)<store.now().timestamp()
                    or not secrets.compare_digest(conf.get('challenge_hash',''),digest(code.strip()))):
                raise ValueError('Connection code is invalid or expired; request a new code')
            doc.update(recipient=conf['challenge_recipient'],verified=True,inbound_verified=False,approvals=False)
            conf.pop('challenge_hash',None)
            # A phone reply proves inbound self-chat works independently of
            # successful outbound delivery. The app never receives this token.
            link=secrets.token_hex(8)
            conf.update(inbound_hash=digest(link),inbound_expires=store.now().timestamp()+300)
            evolution(doc,'/message/sendText/'+doc['instance'],'POST',{'number':doc['recipient'],
                'text':'Drishti connected. To enable paper-trade replies, send this as a NEW message (do not forward):\nLINK '+link})
        store.secret(uid,channel,conf)
        return _save(doc)


def preferences(uid, channel, alerts, approvals):
    with user_lock(uid):
        doc=_owned(uid,channel)
        if (alerts or approvals) and not (doc.get('verified') and doc.get('connected')):
            raise ValueError('Verify the private messaging account first')
        if approvals and not doc.get('inbound_verified'):
            raise ValueError('Reply to the WhatsApp LINK message first to verify incoming replies')
        if alerts and not doc.get('alerts'):
            doc['enabled_since']=store.now().timestamp()
        doc.update(alerts=alerts,approvals=approvals)
        if channel=='telegram' and alerts:
            from apps.api.desk import notifications
            legacy=notifications._config(uid)
            if legacy and legacy.get('enabled'):
                notifications.configure(uid,legacy['token'],legacy['chat_id'],False)
        return _save(doc)


def disconnect(uid, channel):
    with user_lock(uid):
        doc=_owned(uid,channel)
        doc.update(active=False,connected=False,verified=False,alerts=False,approvals=False)
        store.save(doc)  # revoke before any fallible network operation
        conf=store.secret(uid,channel)
        error=None
        try:
            if channel=='telegram':
                hook=providers.telegram(conf['token'],'getWebhookInfo')
                if hook.get('url')==hook_url(doc):
                    providers.telegram(conf['token'],'deleteWebhook')
            elif doc.get('created'):
                evolution(doc,'/instance/logout/'+doc['instance'],'DELETE')
        except Exception:
            error='Disconnected in Drishti. Remote logout failed; check the provider dashboard.'
        # Preserve encrypted cleanup credentials only while cleanup is pending.
        doc['cleanup_pending']=bool(error)
        doc['last_error']=error
        store.save(doc)
        if not error:
            from apps.api.jobs.store import get_store
            get_store().delete_user_key(uid,'messaging.'+channel)
        x=status(uid,channel)
        x.update(cleanup_pending=bool(error),last_error=error)
        return x
