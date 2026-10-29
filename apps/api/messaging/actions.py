"""One exact, expiring PAPER proposal shared by all verified channels."""
import hashlib
import json
import re
import secrets
from datetime import date
from fastapi import HTTPException
from apps.api.desk.store import get_desk_store
from apps.api.desk.market.session import NSE, parse_clock
from apps.api.desk.profile import load_profile
from apps.api.kite.execution import user_lock
from apps.api.messaging import connections as c, store


def snapshot(order):
    return hashlib.sha256(json.dumps(order,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _result(a):
    words={'pending':'Waiting for your paper-trade decision.','processing':'Checking the paper ticket.',
           'approved':'Paper trade accepted. It is waiting for an eligible entry bar; no real order was placed.',
           'rejected':'Paper proposal rejected. No trade was placed.',
           'expired':'This proposal expired. Create a new ticket to try again.',
           'blocked':'This ticket cannot be approved. Check its current state and risk checks in Drishti.'}
    return {'id':a['id'],'state':a['state'],'expires_at':a['expires_at'],'message':words[a['state']]}


def _eligible_channels(uid):
    return [d for ch in ('telegram','whatsapp') if (d:=store.connection(uid,ch)) and
            all(d.get(k) for k in ('connected','verified','inbound_verified','approvals'))]


def _strategy(tk, uid):
    if str(tk.get('source','')).startswith('strategy'):
        return True
    from apps.api.kite.store import get_kite_store
    return any(sig.get('ticket_id')==tk['id'] for s in get_kite_store().list_strategies(uid)
               for sig in s.get('signals',[]) if sig.get('status')=='await_confirm')


def propose(uid, ticket_id):
    from apps.api.routes import desk
    with user_lock(uid):
        tk=get_desk_store().get_ticket(ticket_id,uid)
        if not tk or tk['status']!='ready' or not tk.get('order'):
            raise ValueError('Only your ready ticket can be sent for paper approval')
        if _strategy(tk,uid):
            raise ValueError('Live strategy tickets are excluded from paper messaging approvals')
        channels=_eligible_channels(uid)
        if not channels:
            raise ValueError('Connect and verify a channel, then enable paper approvals in Messages')
        from apps.api.desk.risk import fingerprint
        if fingerprint(tk['order'])!=tk['fingerprint']:
            raise ValueError('The ticket changed; make a new ticket')
        verdict=desk._risk_for([tk['order']],uid)['results'][0]
        if verdict['verdict']!='PASS' or verdict['fingerprint']!=tk['fingerprint']:
            raise ValueError('The current risk check does not allow this paper proposal')
        a=store.action(uid,ticket_id)
        if a:
            # Resending never silently changes its token, recipients or expiry.
            if a['state']=='pending' and a['expires_at']>store.now().timestamp() and a['snapshot']==snapshot(tk['order']):
                _queue_proposal(a,tk)
            return _result(a)
        now=store.now()
        p=tk['order'].get('plan') or {}
        profile=load_profile(uid)
        clocks=[p.get('flat_by') or profile.flat_by,profile.no_entries_after]
        cutoff=min(parse_clock(v) for v in clocks if v)
        day=date.fromisoformat(p.get('date') or now.astimezone(NSE.tz).date().isoformat())
        expiry=min(now.timestamp()+300,NSE.local(day,cutoff).timestamp())
        if expiry<=now.timestamp():
            raise ValueError('This ticket is past its entry/session cutoff')
        token=secrets.token_urlsafe(18)
        a={'id':secrets.token_hex(16),'user_id':uid,'ticket_id':ticket_id,'state':'pending',
           'token':token,'snapshot':snapshot(tk['order']),'expires_at':expiry,
           'connections':[d['id'] for d in channels],
           'generations':{d['id']:d.get('generation') for d in channels},
           'audit':[], 'last_trade_state':None}
        store.save_action(a)
        _queue_proposal(a,tk)
        return _result(a)


def _queue_proposal(a,tk):
    token=a['token'];expiry=a['expires_at']
    p=tk['order'].get('plan') or {}
    order=tk['order'];s=tk.get('sizing') or {}
    text=(f"PAPER TRADE — {order['symbol']}\n{tk.get('words','')}\n"
          f"Quantity: {order['qty']} · {order['order_type']}\nEntry: {order.get('price','market')}\n"
          f"Stop: {order.get('stop')} · Target: {order.get('target')}\n"
          f"Estimated loss incl. charges: ₹{s.get('max_loss_net','unavailable')}\n"
          f"Freshness: {p.get('data_as_of') or p.get('source') or 'see the plan; delayed bars may apply'}\n"
          f"Expires: {store.now().fromtimestamp(expiry,NSE.tz).strftime('%H:%M:%S IST')}\n"
          f"APPROVE {token}\nREJECT {token}\nSTATUS {token}\n"
          f"Plan: {providers_url(tk)}\nApproval creates a paper trade only; risk is checked again.")
    buttons=[[{'text':'Approve paper trade','callback_data':'a:'+token},
              {'text':'Reject','callback_data':'r:'+token}],
             [{'text':'Status','callback_data':'s:'+token},{'text':'View plan','url':providers_url(tk)}]]
    for cid in a['connections']:
        d=store.by_id(cid)
        if d and d.get('approvals') and d.get('generation')==a.get('generations',{}).get(cid):
            store.enqueue(cid,'proposal-'+a['id'],text,buttons if d['channel']=='telegram' else None,expiry)

def providers_url(tk):
    from apps.api.messaging.providers import public_base
    return public_base()+('/desk/plans/'+tk['plan_id'] if tk.get('plan_id') else '/desk/plans')


def _state(a, state, channel=None, sender=None):
    a['state']=state
    if channel:
        a['audit'].append({'channel':channel,'sender':sender,'decision':state,'at':store.now().isoformat()})
    store.save_action(a)
    if state not in ('processing','pending'):
        for cid in a['connections']:
            if ((d:=store.by_id(cid)) and d.get('approvals')
                    and d.get('generation')==a.get('generations',{}).get(cid)):
                store.enqueue(cid,'decision-'+a['id']+'-'+state,'PAPER proposal: '+_result(a)['message'])
    return _result(a)


def decide(uid, channel, token, decision, sender):
    from apps.api.routes import desk
    if not re.fullmatch(r'[A-Za-z0-9_-]{24}',token):
        raise ValueError('Unknown proposal code')
    with user_lock(uid):
        doc=store.connection(uid,channel)
        if (not doc or not all(doc.get(k) for k in ('verified','connected','inbound_verified'))
                or sender!=doc.get('sender' if channel=='telegram' else 'recipient')):
            raise ValueError('This private account is not authorized')
        a=next((a for a in store.all_actions() if a['user_id']==uid and secrets.compare_digest(a['token'],token)),None)
        if (not a or doc['id'] not in a['connections']
                or doc.get('generation')!=a.get('generations',{}).get(doc['id'])):
            raise ValueError('Unknown or revoked proposal')
        if decision not in ('approve','reject','status'):
            raise ValueError('Unknown proposal action')
        if decision!='status' and not doc.get('approvals'):
            raise ValueError('Paper approvals are disabled for this channel')
        if a['state'] not in ('pending','processing'):
            return _result(a)
        tk=get_desk_store().get_ticket(a['ticket_id'],uid)
        if tk and tk['status']=='paper':
            return _state(a,'approved',channel,sender)
        if not tk or tk['status']!='ready':
            return _state(a,'rejected' if tk and tk['status']=='cancelled' else 'blocked',channel,sender)
        if a['expires_at']<=store.now().timestamp():
            return _state(a,'expired',channel,sender)
        if snapshot(tk.get('order'))!=a['snapshot']:
            return _state(a,'blocked',channel,sender)
        if decision=='status':
            return _result(a)
        _state(a,'processing')
        try:
            if decision=='reject':
                desk.cancel_ticket(tk['id'],uid)
                return _state(a,'rejected',channel,sender)
            desk.confirm_ticket(tk['id'],desk.ConfirmBody(mode='paper'),uid)
        except HTTPException:
            return _state(a,'blocked',channel,sender)
        return _state(a,'approved',channel,sender)


def update_transitions():
    for a in store.all_actions():
        uid=a['user_id']
        with user_lock(uid):
            a=store.action(uid,a['ticket_id'])
            tk=get_desk_store().get_ticket(a['ticket_id'],uid)
            if a['state'] in ('pending','processing'):
                if tk and tk['status']=='paper':
                    _state(a,'approved')
                elif a['expires_at']<=store.now().timestamp():
                    _state(a,'expired')
                elif not tk or tk['status']!='ready':
                    _state(a,'rejected' if tk and tk['status']=='cancelled' else 'blocked')
            if a['state']!='approved' or not tk:
                continue
            trade=get_desk_store().get_paper(tk.get('paper_trade_id',''),uid)
            if not trade:
                continue
            state=trade['status']
            if state==a.get('last_trade_state'):
                continue
            text=f"PAPER {trade['symbol']} — "+{'pending':'waiting for entry','open':'entry filled',
                'closed':'closed','cancelled':'entry cancelled'}.get(state,state)
            if trade.get('entry_fill'):
                text+=f"\nEntry: ₹{trade['entry_fill']['price']:.2f}"
            if state=='closed':
                text+='\nExit: '+str((trade.get('exit_fill') or {}).get('reason') or trade.get('exit_reason','closed'))
                if trade.get('pnl_net') is not None:
                    text+=f"\nNet P&L: ₹{trade['pnl_net']:.2f}"
            for cid in a['connections']:
                if ((d:=store.by_id(cid)) and d.get('approvals')
                        and d.get('generation')==a.get('generations',{}).get(cid)):
                    store.enqueue(cid,'trade-'+a['id']+'-'+state,text)
            a['last_trade_state']=state
            store.save_action(a)
