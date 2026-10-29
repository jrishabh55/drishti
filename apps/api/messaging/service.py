"""Authenticated inbound events and bounded, retryable message delivery."""
import asyncio
import json
import logging
import secrets
import threading
from apps.api.desk.store import get_desk_store
from apps.api.kite.execution import user_lock
from apps.api.messaging import connections as c, providers, store

logger=logging.getLogger(__name__)
_DELIVERY_GUARD=threading.Lock()
_DELIVERY_LOCKS={}


def _whatsapp_text(message):
    """Normalize reply IDs without treating display text as an action."""
    if not isinstance(message,dict):
        return ''
    for name in ('extendedTextMessage','buttonsResponseMessage','interactiveResponseMessage'):
        part=message.get(name) or {}
        if not isinstance(part,dict):
            return ''
        context=part.get('contextInfo') or {}
        if not isinstance(context,dict) or context.get('isForwarded'):
            return ''
    if 'interactiveResponseMessage' in message:
        flow=(message.get('interactiveResponseMessage') or {}).get('nativeFlowResponseMessage') or {}
        if not isinstance(flow,dict) or flow.get('name')!='quick_reply':
            return ''
        raw=flow.get('paramsJson')
        if not isinstance(raw,str) or len(raw)>1024:
            return ''
        try:
            params=json.loads(raw)
        except ValueError:
            return ''
        text=params.get('id') if isinstance(params,dict) else None
        return text if isinstance(text,str) and len(text)<=128 else ''
    return (message.get('conversation') or (message.get('extendedTextMessage') or {}).get('text') or
            (message.get('buttonsResponseMessage') or {}).get('selectedButtonId') or '')


def authorize(cid, secret):
    doc=store.by_id(cid)
    conf=store.secret(doc['user_id'],doc['channel']) if doc else {}
    if not doc or not doc.get('hook_ready') or not secret or not secrets.compare_digest(conf.get('hook_secret',''),secret):
        raise PermissionError('Invalid messaging callback')
    return doc


def receive(cid, secret, payload):
    doc=authorize(cid,secret)
    acknowledgement=None
    with user_lock(doc['user_id']):
        doc=authorize(cid,secret)  # disconnect may have won the lock
        if not isinstance(payload,dict):
            raise ValueError('Expected a callback object')
        conf=store.secret(doc['user_id'],doc['channel'])
        if doc['channel']=='telegram':
            mid=payload.get('update_id')
            if mid is None or store.seen(cid,str(mid)):
                return {'ok':True}
            cb=payload.get('callback_query') or {}
            msg=cb.get('message') or payload.get('message') or {}
            sender=cb.get('from') or msg.get('from') or {}
            chat=msg.get('chat') or {}
            if chat.get('type')!='private' or str(chat.get('id'))!=str(sender.get('id')) or msg.get('forward_origin'):
                return {'ok':True}
            text=cb.get('data') or msg.get('text') or ''
            if not isinstance(text,str):
                return {'ok':True}
            if text.startswith('/start '):
                token=text.split(' ',1)[1]
                if (conf.get('pair_hash') and conf.get('pair_expires',0)>store.now().timestamp()
                        and secrets.compare_digest(conf['pair_hash'],c.digest(token))):
                    doc.update(pending_sender=str(sender['id']),pending_chat=str(chat['id']),
                               pending_identity=str(sender.get('first_name') or sender.get('username') or 'Telegram')[:80]+' ('+str(sender['id'])+')')
                    conf.pop('pair_hash',None)
                    store.secret(doc['user_id'],'telegram',conf)
                    store.save(doc)
                store.mark_seen(cid,str(mid))
                return {'ok':True}
            if not doc.get('verified') or str(chat.get('id'))!=doc.get('chat') or str(sender.get('id'))!=doc.get('sender'):
                return {'ok':True}
            reply=_command(doc,text,str(sender['id']))
            if cb.get('id'):
                # Telegram supports Bot API methods in webhook responses.
                # Acknowledge without holding the trade lock for HTTP I/O.
                acknowledgement={'method':'answerCallbackQuery','callback_query_id':cb['id'],'text':reply[:180]}
        else:
            if payload.get('instance')==doc.get('instance') and payload.get('event') in ('connection.update','CONNECTION_UPDATE'):
                state=(payload.get('data') or {}).get('state')
                if state in ('open','close','connecting'):
                    doc['connected']=state=='open'
                    if state!='open':
                        doc.update(verified=False,inbound_verified=False,approvals=False)
                    store.save(doc)
                return {'ok':True}
            if payload.get('instance')!=doc.get('instance') or payload.get('event') not in ('messages.upsert','MESSAGES_UPSERT'):
                return {'ok':True}
            data=payload.get('data') or {}
            if not isinstance(data,dict):
                return {'ok':True}
            key=data.get('key') or {}
            mid=key.get('id')
            jid=key.get('remoteJid') or ''
            if jid.endswith('@lid'):
                jid=key.get('remoteJidAlt') or ''
            recipient=doc.get('recipient')
            if not mid or not recipient or jid!=recipient+'@s.whatsapp.net' or store.seen(cid,str(mid)):
                return {'ok':True}
            # Provider IDs from successful outbound sends are also retained in
            # the outbox. Neither self-chat echoes nor forwarded text act.
            if any(d.get('message_id')==str(mid) for d in store.deliveries(cid)):
                return {'ok':True}
            message=data.get('message') or {}
            text=_whatsapp_text(message)
            if not isinstance(text,str):
                return {'ok':True}
            if (doc.get('verified') and text.startswith('LINK ') and conf.get('inbound_hash')
                    and conf.get('inbound_expires',0)>store.now().timestamp()
                    and secrets.compare_digest(conf['inbound_hash'],c.digest(text[5:].strip()))):
                doc['inbound_verified']=True
                conf.pop('inbound_hash',None)
                store.secret(doc['user_id'],'whatsapp',conf)
                store.save(doc)
                store.enqueue(cid,'linked','WhatsApp replies verified. You can enable paper approvals in Drishti.')
            elif doc.get('verified') and doc.get('inbound_verified'):
                _command(doc,text,recipient)
        store.mark_seen(cid,str(mid))
        return acknowledgement or {'ok':True}


def _command(doc, text, sender):
    from apps.api.messaging import actions
    parts=text.strip().replace(':',' ',1).split()
    names={'a':'approve','r':'reject','s':'status','approve':'approve','reject':'reject','status':'status'}
    if len(parts)!=2 or parts[0].lower() not in names:
        return 'Use the buttons or APPROVE / REJECT / STATUS followed by the proposal code.'
    try:
        result=actions.decide(doc['user_id'],doc['channel'],parts[1],names[parts[0].lower()],sender)
        message=result['message']
    except ValueError as exc:
        message=str(exc)
    store.enqueue(doc['id'],'reply-'+secrets.token_hex(8),message)
    return message


def send(doc, text, buttons=None):
    if doc['channel']=='telegram':
        conf=store.secret(doc['user_id'],'telegram')
        body={'chat_id':doc['chat'],'text':text[:4000]}
        if buttons:
            body['reply_markup']={'inline_keyboard':buttons}
        r=providers.telegram(conf['token'],'sendMessage',body)
        return str(r.get('message_id','')) if isinstance(r,dict) else ''
    replies=[{'type':'reply','displayText':b['text'][:20],'id':b['callback_data']}
             for row in (buttons or []) for b in row if b.get('callback_data')][:3]
    if replies:
        try:
            r=c.evolution(doc,'/message/sendButtons/'+doc['instance'],'POST',
                          {'number':doc['recipient'],'title':'Drishti paper trade',
                           'description':text[:4000],'footer':'Paper trading only','buttons':replies})
            return str((r.get('key') or {}).get('id','')) if isinstance(r,dict) else ''
        except providers.ProviderError as exc:
            # Only a definitive unsupported endpoint permits a second send.
            # Timeouts/server failures may already have delivered the buttons.
            if exc.status not in (404,405,501):
                raise
    r=c.evolution(doc,'/message/sendText/'+doc['instance'],'POST',{'number':doc['recipient'],'text':text[:4000]})
    return str((r.get('key') or {}).get('id','')) if isinstance(r,dict) else ''


def flush(cid):
    doc=store.by_id(cid)
    if not doc:
        return
    with _DELIVERY_GUARD:
        lock=_DELIVERY_LOCKS.setdefault((doc['user_id'],doc['channel']),threading.Lock())
    if not lock.acquire(blocking=False):
        return
    try:
        for d in store.deliveries(cid,True):
            with user_lock(doc['user_id']):
                doc=store.by_id(cid)
                if not doc or not doc.get('verified') or not doc.get('connected'):
                    return
                now=store.now().timestamp()
                key=d['id'].split(':',1)[1]
                if (d.get('generation')!=doc.get('generation') or
                        (key.startswith('alert-') and (not doc.get('alerts') or d.get('created_at',0)<doc.get('enabled_since',0)))
                        or (key.startswith(('proposal-','decision-','trade-')) and not doc.get('approvals'))):
                    d['cancelled']=True
                    store.save_delivery(d)
                    continue
                if d['expires_at']<=now or d['attempts']>=5 or d['next_attempt']>now:
                    continue
                d['attempts']+=1
                d['next_attempt']=now+min(300,15*2**d['attempts'])
                store.save_delivery(d)
                generation=doc.get('generation')
            # This lock isolates delivery only. Trading, cancellation and
            # settlement remain free while the provider request is in flight.
            failed=False
            try:
                d['message_id']=send(doc,d['text'],d.get('buttons'))
                d['sent']=True
            except Exception:
                failed=True
            with user_lock(doc['user_id']):
                store.save_delivery(d)
                if d['message_id']:
                    store.mark_seen(cid,d['message_id'])
                current=store.by_id(cid)
                if current and current.get('generation')==generation:
                    current['last_error']=('Message delivery failed; Drishti still has the authoritative trade status.' if failed else None)
                    store.save(current)
    finally:
        lock.release()


def test_message(uid, channel):
    doc=c._owned(uid,channel)
    if not doc.get('verified') or not doc.get('connected'):
        raise ValueError('Verify the connection before sending a test message')
    store.enqueue(doc['id'],'test-'+secrets.token_hex(8),'Drishti test message. Your private connection is working. This does not place a trade.')
    flush(doc['id'])
    return c.status(uid,channel)


def sweep():
    from apps.api.messaging import actions
    actions.update_transitions()
    store.cleanup()
    ds=get_desk_store()
    for doc in store.all_connections():
        try:
            if doc.get('alerts') and doc.get('verified'):
                for e in ds.list_alert_events(doc['user_id'],limit=30):
                    from datetime import datetime
                    at=datetime.fromisoformat(e['at']).timestamp()
                    if at>=max(doc.get('enabled_since',0),store.now().timestamp()-900):
                        store.enqueue(doc['id'],'alert-'+e['id'],str(e['message']),expires_at=at+900)
            flush(doc['id'])
        except Exception:
            logger.warning('Messaging delivery upkeep failed; will retry without affecting trades')


async def messaging_loop():
    while True:
        await asyncio.sleep(15)
        try:
            await asyncio.to_thread(sweep)
        except Exception:
            logger.warning('Messaging upkeep failed; retrying on the next sweep')
