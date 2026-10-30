from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from apps.api.desk.store import reset_desk_store_for_tests
from apps.api.kite.store import reset_kite_store_for_tests, get_kite_store
from apps.api.routes import strategy_workflow as workflow
from tests.desk_utils import desk_client
from tests.scanner_utils import make_store

REAL_RUN_LEVELS = workflow._run_levels

NOW = datetime(2026, 10, 30, 6, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path, monkeypatch):
    reset_desk_store_for_tests(tmp_path/'desk.sqlite')
    reset_kite_store_for_tests(tmp_path/'kite.sqlite')
    scans = make_store(tmp_path, {'RELIANCE': [100.0], 'SBIN': [80.0]})
    runs = {
        'mine': SimpleNamespace(user_id='user_a', status='completed', ticker='RELIANCE.NS', analysis_date='2026-10-29'),
        'theirs': SimpleNamespace(user_id='user_b', status='completed', ticker='RELIANCE.NS', analysis_date='2026-10-29'),
        'running': SimpleNamespace(user_id='user_a', status='running', ticker='RELIANCE.NS', analysis_date='2026-10-29'),
        'us': SimpleNamespace(user_id='user_a', status='completed', ticker='AAPL', analysis_date='2026-10-29'),
    }
    monkeypatch.setattr(workflow, 'get_store', lambda: SimpleNamespace(get_run=runs.get))
    monkeypatch.setattr(workflow, '_now', lambda: NOW)
    levels = {'currency': 'INR', 'viable': True, 'levels': {
        'entry': {'price': 100}, 'stop': {'price': 98}, 'target': {'price': 104}}}
    monkeypatch.setattr(workflow, '_run_levels', lambda *args, **kwargs: levels)
    client, user = desk_client(workflow.router)
    return client, user, scans, levels


def test_owned_analysis_becomes_reviewable_off_draft_without_creating_strategy(env):
    client, _, _, _ = env
    r = client.post('/api/desk/strategy-drafts', json={'run_id': 'mine'})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d['strategy']['mode'] == 'off'
    assert d['strategy']['symbols'] == ['RELIANCE']
    assert d['strategy']['level'] == 100
    assert d['strategy']['stop_rule'] == 'price'
    assert d['strategy']['stop_param'] == 98
    assert d['strategy']['target_rule'] == 'price'
    assert d['strategy']['target_param'] == 104
    assert d['strategy']['target_r'] == 2
    assert d['source']['analysis_date'] == '2026-10-29'
    assert d['warnings']
    assert get_kite_store().list_strategies('user_a') == []


@pytest.mark.parametrize('run,status', [('theirs',404),('missing',404),('running',409),('us',422)])
def test_analysis_drafts_require_own_completed_nse_research(env, run, status):
    client, _, _, _ = env
    assert client.post('/api/desk/strategy-drafts', json={'run_id':run}).status_code == status


def test_unviable_levels_and_foreign_portfolio_cannot_become_drafts(env):
    client, _, _, levels = env
    levels['viable'] = False
    assert client.post('/api/desk/strategy-drafts', json={'run_id':'mine'}).status_code == 422
    assert client.post('/api/desk/strategy-drafts', json={'symbols':['SBIN'], 'portfolio_id':'not-mine'}).status_code == 404
    assert client.post('/api/desk/strategy-drafts', json={'run_id':'mine','mode':'paper'}).status_code == 422


def test_scanner_draft_requires_owned_definition_and_current_selected_matches(env, monkeypatch):
    client, _, scans, _ = env
    sid = scans.create_scanner('user_a','My scan','',{'logic':'AND','children':[]})
    other = scans.create_scanner('user_b','Private','',{})
    monkeypatch.setattr(workflow,'_scan',lambda sid,uid: {'matches':[{'symbol':'SBIN'}], 'source':'delayed:yahoo','data_as_of':'2026-10-30T10:00:00+05:30'})
    assert client.post('/api/desk/strategy-drafts',json={'scanner_id':other}).status_code == 404
    assert client.post('/api/desk/strategy-drafts',json={'scanner_id':sid,'symbols':['RELIANCE']}).status_code == 422
    d = client.post('/api/desk/strategy-drafts',json={'scanner_id':sid}).json()
    assert d['strategy']['symbols'] == []
    assert d['source']['preview_symbols'] == ['SBIN']
    assert d['source']['scanner_id'] == sid
    assert d['strategy']['mode'] == 'off'


def test_draft_save_requires_reviewed_off_mode_is_owned_and_idempotent(env):
    client, user, _, _ = env
    d = client.post('/api/desk/strategy-drafts',json={'symbols':['SBIN']}).json()
    path = '/api/desk/strategy-drafts/'+d['id']+'/save'
    body = {'strategy':{'name':'My experiment','symbols':['SBIN'],'setup':'orb','mode':'off'}}
    user['id']='user_b'
    assert client.post(path,json=body).status_code == 404
    user['id']='user_a'
    assert client.post(path,json={'strategy':{**body['strategy'],'mode':'paper'}}).status_code == 409
    r = client.post(path,json=body)
    assert r.status_code == 201,r.text
    sid=r.json()['strategy']['id']
    assert get_kite_store().get_strategy(sid,'user_a')['mode']=='off'
    assert client.post(path,json=body).json()['strategy']['id']==sid
    assert len(get_kite_store().list_strategies('user_a'))==1
    assert client.post(path,json={'strategy':{**body['strategy'],'name':'Different'}}).status_code==409


def test_expired_draft_requires_a_fresh_review(env, monkeypatch):
    client, _, _, _ = env
    d=client.post('/api/desk/strategy-drafts',json={'symbols':['SBIN']}).json()
    monkeypatch.setattr(workflow,'_now',lambda:NOW+timedelta(hours=1))
    r=client.post('/api/desk/strategy-drafts/'+d['id']+'/save',json={'strategy':{'name':'x','symbols':['SBIN'],'mode':'off'}})
    assert r.status_code==409
    assert not get_kite_store().list_strategies('user_a')


def test_nonfinite_review_cannot_claim_or_create_a_strategy(env):
    client, _, _, _ = env
    d=client.post('/api/desk/strategy-drafts',json={'symbols':['SBIN']}).json()
    r=client.post('/api/desk/strategy-drafts/'+d['id']+'/save',content='{"strategy":{"name":"x","symbols":["SBIN"],"target_r":NaN}}',headers={'Content-Type':'application/json'})
    assert r.status_code==422
    assert client.get('/api/desk/strategy-drafts/'+d['id']).json()['state']=='review'
    assert not get_kite_store().list_strategies('user_a')


def test_invalid_review_can_be_corrected_without_consuming_the_draft(env):
    client, _, _, _=env
    d=client.post('/api/desk/strategy-drafts',json={'symbols':['SBIN']}).json()
    path='/api/desk/strategy-drafts/'+d['id']+'/save'
    assert client.post(path,json={'strategy':{'name':'','symbols':['SBIN'],'mode':'off'}}).status_code==422
    assert client.get('/api/desk/strategy-drafts/'+d['id']).json()['state']=='review'
    assert client.post(path,json={'strategy':{'name':'Corrected','symbols':['SBIN'],'mode':'off'}}).status_code==201


def test_readiness_distinguishes_running_loops_from_configured_flags(env, monkeypatch):
    from apps.api.routes import kite as kite_routes
    client, _, _, _=env
    monkeypatch.setattr(kite_routes,'status',lambda uid: {'data_access':{'session':'active','live':{'state':'live','symbols':[{'source':'live:kite'}]}},'orders':{'server_enabled':False}})
    class Running:
        def done(self): return False
    client.app.state.trading_loops={'paper':Running(),'strategies':Running(),'scanner':None}
    r=client.get('/api/desk/readiness')
    assert r.status_code==200,r.text
    d=r.json()
    assert d['loops']=={'paper':'running','strategies':'running','scanner':'disabled'}
    assert d['live_orders_enabled'] is False
    assert d['paper_blockers']==['No paper strategy is enabled']
    assert not get_kite_store().list_strategies('user_a')


def test_active_login_does_not_claim_ready_when_stream_has_no_fresh_symbols(env, monkeypatch):
    from apps.api.routes import kite as kite_routes
    client, _, _, _=env
    monkeypatch.setattr(kite_routes,'status',lambda uid: {'data_access':{'session':'active','live':{'state':'off','symbols':[]}},'orders':{'server_enabled':False}})
    d=client.get('/api/desk/readiness').json()
    assert any('stream' in b.lower() for b in d['paper_blockers'])


@pytest.mark.parametrize('symbol',['INVALID!','','SBIN.BO'])
def test_malformed_symbols_are_actionable_validation_errors(env, symbol):
    client, _, _, _=env
    assert client.post('/api/desk/strategy-drafts',json={'symbols':[symbol]}).status_code==422
    with workflow._db() as c:
        exists=c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='desk_strategy_drafts'").fetchone()[0]
        assert not exists or c.execute('SELECT COUNT(*) FROM desk_strategy_drafts').fetchone()[0]==0


def test_nonfinite_typed_input_returns_422_without_error_serialization_failure(env):
    client, _, _, _=env
    r=client.post('/api/desk/strategy-drafts',content='{"symbols":[NaN]}',headers={'Content-Type':'application/json'})
    assert r.status_code==422


@pytest.mark.parametrize('state',['disabled','stopped','failed','absent'])
def test_readiness_requires_the_background_paper_settlement_worker(env, monkeypatch, state):
    from apps.api.routes import kite as kite_routes
    client, _, _, _=env
    monkeypatch.setattr(kite_routes,'status',lambda uid: {'data_access':{'session':'active','live':{'state':'live','symbols':[{'symbol':'SBIN','source':'live:kite'}]}},'orders':{'server_enabled':False}})
    get_kite_store().put_strategy('user_a',{'name':'Paper experiment','mode':'paper','symbols':['SBIN']})
    class Running:
        def done(self):return False
    class Finished:
        def done(self):return True
        def cancelled(self):return False
        def exception(self):return RuntimeError('internal detail') if state=='failed' else None
    loops={'strategies':Running()}
    if state!='absent':loops['paper']=None if state=='disabled' else Finished()
    client.app.state.trading_loops=loops
    d=client.get('/api/desk/readiness').json()
    assert any('settlement' in b.lower() for b in d['paper_blockers'])
    assert 'internal detail' not in str(d)


def test_whole_scanner_draft_follows_current_candidates_instead_of_pinning_first_matches(env, monkeypatch):
    client, _, scans, _=env
    sid=scans.create_scanner('user_a','Dynamic scan','',{'op':'and','conditions':[]})
    monkeypatch.setattr(workflow,'_scan',lambda sid,uid: {'matches':[{'symbol':'SBIN'}]})
    d=client.post('/api/desk/strategy-drafts',json={'scanner_id':sid}).json()
    assert d['strategy']['scanner_id']==sid
    assert d['strategy']['symbols']==[]
    assert d['source']['preview_symbols']==['SBIN']
    assert client.post('/api/desk/strategy-drafts/'+d['id']+'/save',json={'strategy':d['strategy']}).status_code==201


def test_reviewed_absolute_research_draft_saves_in_an_owned_named_portfolio(env):
    from apps.api.desk import portfolios
    client, _, _, _=env
    pf=portfolios.create('user_a',portfolios.PortfolioCreate(name='Research lab',capital=20000))
    d=client.post('/api/desk/strategy-drafts',json={'run_id':'mine','portfolio_id':pf['id']}).json()
    r=client.post('/api/desk/strategy-drafts/'+d['id']+'/save',json={'strategy':d['strategy']})
    assert r.status_code==201,r.text
    s=r.json()['strategy']
    assert (s['portfolio_id'],s['mode'],s['stop_param'],s['target_param'])==(pf['id'],'off',98,104)


def test_research_draft_preserves_selected_target_multiple(env, monkeypatch):
    client, _, _, _ = env
    from apps.api.routes import runs
    calls = []
    def calculate(run_id, *, capital, risk_pct, r_multiple, user_id):
        calls.append((run_id, r_multiple, user_id))
        return SimpleNamespace(model_dump=lambda: {'currency':'INR','viable':True,'levels': {
            'entry':{'price':100},'stop':{'price':98},'target':{'price':100+2*r_multiple}}})
    monkeypatch.setattr(runs, 'get_run_levels', calculate)
    # Exercise the real workflow adapter rather than the fixture's fixed levels.
    monkeypatch.setattr(workflow, '_run_levels', REAL_RUN_LEVELS)
    res = client.post('/api/desk/strategy-drafts',json={'run_id':'mine','r_multiple':3})
    assert res.status_code == 201, res.text
    draft=res.json()
    assert calls == [('mine',3,'user_a')]
    assert draft['strategy']['target_param'] == 106
    assert draft['strategy']['target_r'] == 3
    assert draft['source']['r_multiple'] == 3
    saved=client.post('/api/desk/strategy-drafts/'+draft['id']+'/save',json={'strategy':draft['strategy']})
    assert saved.status_code == 201 and saved.json()['strategy']['target_param'] == 106


@pytest.mark.parametrize('body', [
    {'run_id':'mine','r_multiple':0}, {'run_id':'mine','r_multiple':11},
    {'run_id':'mine','r_multiple':float('inf')}, {'symbols':['SBIN'],'r_multiple':3},
])
def test_target_multiple_is_finite_bounded_and_analysis_only(env, body):
    import json
    assert env[0].post('/api/desk/strategy-drafts',content=json.dumps(body),
                       headers={'Content-Type':'application/json'}).status_code == 422


@pytest.mark.parametrize('pid',[[],{},['default']])
def test_invalid_portfolio_shape_is_refused_without_consuming_draft(env, pid):
    client, _, _, _=env
    d=client.post('/api/desk/strategy-drafts',json={'symbols':['SBIN']}).json()
    path='/api/desk/strategy-drafts/'+d['id']+'/save'
    res=client.post(path,json={'strategy':{'name':'x','symbols':['SBIN'],'mode':'off','portfolio_id':pid}})
    assert res.status_code == 422
    assert client.get('/api/desk/strategy-drafts/'+d['id']).json()['state'] == 'review'
    assert not get_kite_store().list_strategies('user_a')
