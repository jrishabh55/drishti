from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
import pytest
from tests.test_messaging_connections import env
from apps.api.messaging import store
from apps.api.desk.store import get_desk_store
from apps.api.desk.market.session import NSE
from apps.api.desk.risk import fingerprint
from apps.api.routes import desk


@pytest.fixture
def ready(env,monkeypatch):
    c,s,calls=env
    now=NSE.local(datetime(2026,10,29).date(),10*60)
    monkeypatch.setattr(s,'now',lambda:now)
    monkeypatch.setattr(desk,'_now',lambda:now)
    order={'symbol':'RELIANCE','qty':1,'price':1000,'order_type':'LIMIT','product':'MIS','stop':990,'target':1020,
           'plan':{'date':'2026-10-29','flat_by':'15:10','entry':1000,'risk_per_share':10}}
    tk=get_desk_store().create_ticket('a',{'symbol':'RELIANCE','status':'ready','order':order,
        'fingerprint':fingerprint(order),'sizing':{'max_loss_net':11},'words':'Buy 1 RELIANCE', 'plan_id':None})
    monkeypatch.setattr(desk,'_risk_for',lambda orders,uid:{'results':[{'verdict':'PASS','fingerprint':fingerprint(orders[0])}],'warnings':[]})
    for ch in ['telegram','whatsapp']:
        row=s.new('a',ch);row.update(connected=True,verified=True,inbound_verified=True,approvals=True,chat='42',sender='42',recipient='919876543210');s.save(row)
    return tk,now


def token_for(uid,tid):
    import re
    row=store.connection(uid,'whatsapp')
    text=store.deliveries(row['id'])[0]['text']
    return re.search(r'APPROVE ([A-Za-z0-9_-]+)',text).group(1)


def test_cross_channel_concurrent_approval_creates_exactly_one_real_paper_trade(ready):
    from apps.api.messaging import actions
    tk,now=ready
    actions.propose('a',tk['id']);token=token_for('a',tk['id'])
    with ThreadPoolExecutor(2) as p:
        rs=list(p.map(lambda ch:actions.decide('a',ch,token,'approve','42' if ch=='telegram' else '919876543210'),['telegram','whatsapp']))
    assert all(r['state']=='approved' for r in rs)
    trades=get_desk_store().list_paper('a')
    assert len(trades)==1 and trades[0]['symbol']=='RELIANCE' and trades[0]['qty']==1
    assert store.action('a',tk['id'])['state']=='approved'


def test_unknown_user_wrong_sender_and_revoked_connection_cannot_approve(ready):
    from apps.api.messaging import actions
    tk,_=ready;actions.propose('a',tk['id']);token=token_for('a',tk['id'])
    for uid,sender in [('b','42'),('a','43')]:
        with pytest.raises(ValueError):actions.decide(uid,'telegram',token,'approve',sender)
    row=store.connection('a','telegram');row['active']=False;store.save(row)
    with pytest.raises(ValueError):actions.decide('a','telegram',token,'approve','42')
    assert get_desk_store().list_paper('a')==[]


def test_expired_and_changed_ticket_cannot_execute(ready,monkeypatch):
    from apps.api.messaging import actions
    tk,now=ready;actions.propose('a',tk['id']);token=token_for('a',tk['id'])
    monkeypatch.setattr(store,'now',lambda:now+timedelta(minutes=6))
    assert actions.decide('a','telegram',token,'approve','42')['state']=='expired'
    assert get_desk_store().list_paper('a')==[]


def test_changed_order_is_blocked_even_with_still_valid_token(ready):
    from apps.api.messaging import actions
    tk,_=ready;actions.propose('a',tk['id']);token=token_for('a',tk['id'])
    tk['order']['qty']=50;get_desk_store().update_ticket(tk['id'],'a',tk)
    assert actions.decide('a','telegram',token,'approve','42')['state']=='blocked'
    assert get_desk_store().list_paper('a')==[]


def test_reject_then_approve_is_harmless_and_resend_keeps_expiry(ready):
    from apps.api.messaging import actions
    tk,_=ready;first=actions.propose('a',tk['id']);second=actions.propose('a',tk['id'])
    assert first['expires_at']==second['expires_at']
    token=token_for('a',tk['id'])
    assert actions.decide('a','whatsapp',token,'reject','919876543210')['state']=='rejected'
    assert actions.decide('a','telegram',token,'approve','42')['state']=='rejected'
    assert get_desk_store().get_ticket(tk['id'],'a')['status']=='cancelled'
    assert get_desk_store().list_paper('a')==[]


def test_crash_after_paper_insert_reconciles_without_second_insert(ready,monkeypatch):
    from apps.api.messaging import actions
    tk,_=ready;actions.propose('a',tk['id']);token=token_for('a',tk['id'])
    real=store.save_action
    def crash(doc):
        if doc['state']=='approved':raise RuntimeError('process crash after trade')
        real(doc)
    monkeypatch.setattr(store,'save_action',crash)
    with pytest.raises(RuntimeError):actions.decide('a','telegram',token,'approve','42')
    monkeypatch.setattr(store,'save_action',real)
    assert actions.decide('a','telegram',token,'approve','42')['state']=='approved'
    assert len(get_desk_store().list_paper('a'))==1


def test_live_strategy_ticket_is_never_offered_as_paper(ready):
    from apps.api.messaging import actions
    tk,_=ready;tk['source']='strategy:abc';get_desk_store().update_ticket(tk['id'],'a',tk)
    with pytest.raises(ValueError,match='strategy'):actions.propose('a',tk['id'])


def test_paper_transition_messages_are_deduplicated_and_include_net_pnl(ready):
    from apps.api.messaging import actions
    tk,_=ready;actions.propose('a',tk['id']);token=token_for('a',tk['id'])
    actions.decide('a','telegram',token,'approve','42')
    t=get_desk_store().list_paper('a')[0]
    t.update(status='closed',exit_reason='target',pnl_net=19.5)
    get_desk_store().update_paper(t['id'],'a',t)
    actions.update_transitions();actions.update_transitions()
    ds=store.deliveries(store.connection('a','telegram')['id'])
    assert len([d for d in ds if 'Net P&L' in d['text'] and '19.50' in d['text']])==1
