from datetime import date
from dataclasses import replace
from apps.api.desk import bars_source
from apps.api.desk.market.session import NSE
from apps.api.desk.store import reset_desk_store_for_tests
from apps.api.routes import desk
from tests.desk_utils import session_rows, desk_client
from tests.scanner_utils import make_store


def test_one_minute_entry_plan_and_replay_keep_the_same_interval(tmp_path, monkeypatch):
    reset_desk_store_for_tests(tmp_path/'desk.sqlite')
    make_store(tmp_path, None)
    d=date(2026,10,29)
    now=NSE.local(d,10*60)
    spec=replace(NSE,bar_min=1)
    rows=session_rows(d,[100.0]*15+[101.0]*30,spec=spec)
    monkeypatch.setattr(desk,'_now',lambda:now)
    bars_source.register_live_provider(lambda user,symbol,interval,kind:
        bars_source.BarSeries(symbol,interval,rows,'live:test',rows[-1][0].isoformat()))
    try:
        client,_=desk_client(desk.router)
        status=client.get('/api/desk/entry/RELIANCE?interval=1m').json()
        assert status['trigger']['time']=='09:31'
        assert status['interval']=='1m'
        response=client.post('/api/desk/plans',json={'symbol':'RELIANCE','interval':'1m'})
        assert response.status_code==201, response.text
        plan=response.json()
        assert plan['entry_time']=='09:31' and plan['interval']=='1m'
        def replay(rows, replay_plan, **kwargs):
            assert kwargs['spec'].bar_min == 1
            assert replay_plan['entry_time'] == '09:31'
            return {'plan':replay_plan,'source':'live:test'}
        monkeypatch.setattr(desk.tm, 'run_intraday', replay)
        response=client.post('/api/desk/timemachine',json={
            'symbol':'RELIANCE','date':d.isoformat(),'interval':'1m',
            'entry':plan['entry'],'entry_time':plan['entry_time'],
            'stop':plan['stop'],'target':plan['target'],'replays':100})
        assert response.status_code == 200, response.text
        assert response.json()['plan']['interval'] == '1m'
    finally:
        bars_source.clear_live_providers_for_tests()


def test_watch_entry_rules_are_saved_per_user_and_validate_levels(tmp_path):
    reset_desk_store_for_tests(tmp_path/'desk.sqlite')
    from apps.api.routes.watch_rules import router
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    app=FastAPI()
    user={'id':'a'}
    @app.middleware('http')
    async def auth(request: Request, call_next):
        request.state.user_id=user['id']
        return await call_next(request)
    app.include_router(router,prefix='/api')
    client=TestClient(app)
    rule={'setup':'level','level':1200,'interval':'1m','min_volume_x':1.5}
    assert client.put('/api/desk/watch-rules/RELIANCE',json=rule).status_code==200
    assert client.get('/api/desk/watch-rules').json()['RELIANCE']==rule
    user['id']='b'
    assert client.get('/api/desk/watch-rules').json()=={}
    assert client.put('/api/desk/watch-rules/TCS',json={'setup':'level'}).status_code==422
