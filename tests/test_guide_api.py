"""Guide questions: personal keys only, grounded citations, bounded and isolated."""
import json
import logging
import threading
import time
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api import user_keys
from apps.api.auth import current_user_id
from apps.api.guide import service
from apps.api.integrations import product_providers as providers
from apps.api.jobs.store import reset_store_for_tests
from apps.api.routes.guide import router

MODEL = 'claude-sonnet-5'


class StubLLM:
    def __init__(self, reply=None, error=None, gate=None, started=None):
        self.reply, self.error, self.gate, self.started = reply, error, gate, started
        self.prompts = []

    def invoke(self, messages):
        self.prompts.append(messages)
        if self.started:
            self.started.set()
        if self.gate:
            self.gate.wait(5)
        if self.error:
            raise self.error
        return SimpleNamespace(content=self.reply)

    def bind_tools(self, *a, **k):  # pragma: no cover - must never be called
        raise AssertionError('guide answers must not use tools')


@pytest.fixture
def env(tmp_path, monkeypatch):
    reset_store_for_tests(tmp_path / 'runs.sqlite')
    monkeypatch.setenv('WEBAPP_KEY_ENCRYPTION_SECRET', Fernet.generate_key().decode())
    monkeypatch.setenv('OPENAI_API_KEY', 'server-openai-secret')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'server-anthropic-secret')
    monkeypatch.setenv('WEBAPP_SERVER_PROVIDERS', 'azure,bedrock')
    service.reset_limits_for_tests()
    built = []
    llm = StubLLM(json.dumps({'answer': 'Open Limits first.', 'sources': ['limits']}))

    def build(provider, model, api_key):
        built.append((provider, model, api_key))
        return llm
    monkeypatch.setattr(service, '_build_llm', build)
    app = FastAPI()
    app.include_router(router, prefix='/api')
    def uid(request: Request) -> str:
        return request.headers['x-user']
    app.dependency_overrides[current_user_id] = uid
    return SimpleNamespace(client=TestClient(app), built=built, llm=llm)


def ask(env, user='alice', **body):
    payload = {'question': 'How do I set loss limits?', 'route': '/desk/settings',
               'provider': 'anthropic', 'model': MODEL, 'consent': True, **body}
    return env.client.post('/api/guide/ask', json=payload, headers={'x-user': user})


def test_unknown_help_has_no_paid_provider_call_and_releases_user_slot(env):
    providers.save_key('alice', 'anthropic', 'alice-personal-key')
    response = ask(env, question='xyzzzyplugh', route='/unknown')
    assert response.status_code == 200
    assert response.json()['mode'] == 'help'
    assert response.json()['sources'] == []
    assert env.built == []
    service.prepare('alice', 'anthropic', MODEL)
    service._release('alice')


def test_providers_lists_only_the_users_configured_personal_keys(env):
    providers.save_key('alice', 'anthropic', 'alice-anthropic-secret')
    user_keys.save_gemini_key('alice', 'alice-gemini-secret')
    body = env.client.get('/api/guide/providers', headers={'x-user': 'alice'}).json()
    ids = [p['id'] for p in body['providers']]
    assert ids == ['google', 'anthropic']
    for p in body['providers']:
        assert p['label'] and p['models']
        assert all(set(m) == {'id', 'label'} and m['id'] != 'custom' for m in p['models'])
    assert 'secret' not in json.dumps(body)
    assert env.client.get('/api/guide/providers', headers={'x-user': 'bob'}).json() == {'providers': []}
    assert env.built == []  # listing never builds or calls a model


def test_gemini_oauth_without_a_manual_key_is_not_offered(env, monkeypatch):
    monkeypatch.setattr(user_keys, 'gemini_credential_available', lambda uid: True)
    assert env.client.get('/api/guide/providers', headers={'x-user': 'alice'}).json() == {'providers': []}


@pytest.mark.parametrize('body', [{'consent': False}, {'consent': None}, {'consent': 'yes'},
                                  {'question': ''}, {'question': '   '}, {'question': 'x' * 1001},
                                  {'route': '/' + 'a' * 400}, {'unexpected': 1}])
def test_invalid_or_unconsented_requests_never_reach_a_model(env, body):
    providers.save_key('alice', 'anthropic', 'alice-anthropic-secret')
    assert ask(env, **body).status_code == 422
    assert env.built == []


def test_missing_consent_field_is_rejected(env):
    providers.save_key('alice', 'anthropic', 'alice-anthropic-secret')
    r = env.client.post('/api/guide/ask', headers={'x-user': 'alice'},
                        json={'question': 'q', 'route': '/', 'provider': 'anthropic', 'model': MODEL})
    assert r.status_code == 422 and env.built == []


@pytest.mark.parametrize('provider,model', [('openai', 'gpt-5.4'), ('azure', 'x'), ('bedrock', 'x'),
                                            ('chatgpt_helper', 'x'), ('anthropic', 'custom'),
                                            ('anthropic', 'gpt-5.4'), ('deepseek', 'deepseek-v4-flash')])
def test_server_admin_unconfigured_providers_and_unknown_models_are_refused(env, provider, model):
    providers.save_key('alice', 'anthropic', 'alice-anthropic-secret')
    r = ask(env, provider=provider, model=model)
    assert r.status_code == 400
    assert 'secret' not in r.text
    assert env.built == []


def test_another_users_key_is_never_used(env):
    providers.save_key('alice', 'anthropic', 'alice-anthropic-secret')
    assert ask(env, user='bob').status_code == 400
    assert ask(env, user='alice').status_code == 200
    assert env.built == [('anthropic', MODEL, 'alice-anthropic-secret')]


def test_grounded_answer_cites_retrieved_catalog_ids_with_bounded_context(env):
    providers.save_key('alice', 'anthropic', 'alice-anthropic-secret')
    r = ask(env, route='/desk/plans/plan_private_123?x=1', question='How do I set loss limits on paper trades?')
    assert r.status_code == 200
    assert r.json() == {'answer': 'Open Limits first.', 'sources': ['limits'], 'mode': 'ai'}
    prompt = '\n'.join(m[1] for m in env.llm.prompts[0])
    assert 'How do I set loss limits on paper trades?' in prompt
    assert '/desk/plans/$id' in prompt and 'plan_private_123' not in prompt
    assert prompt.count('[source:') <= service.MAX_EXCERPTS
    assert len(prompt) < service.MAX_CONTEXT_CHARS + 4000


def test_gemini_manual_key_is_used(env):
    user_keys.save_gemini_key('alice', 'alice-gemini-secret')
    model = service.models_for('google')[0]['id']
    assert ask(env, provider='google', model=model).status_code == 200
    assert env.built == [('google', model, 'alice-gemini-secret')]


@pytest.mark.parametrize('reply', [
    json.dumps({'answer': 'See https://evil.example', 'sources': ['not-a-feature']}),
    json.dumps({'answer': 'Uncited claim.', 'sources': []}),
    json.dumps({'answer': '', 'sources': ['limits']}),
    json.dumps({'answer': 'x', 'sources': 'limits'}),
    'not json at all',
    None,
])
def test_invalid_model_output_falls_back_to_sourced_built_in_help(env, reply):
    providers.save_key('alice', 'anthropic', 'alice-anthropic-secret')
    env.llm.reply = reply
    body = ask(env).json()
    assert body['mode'] == 'help' and body['notice']
    catalog_ids = {f['id'] for f in service.catalog()['features']}
    assert body['sources'] and set(body['sources']) <= catalog_ids
    assert 'evil.example' not in body['answer']


def test_fenced_json_and_overlong_answers_are_bounded(env):
    providers.save_key('alice', 'anthropic', 'alice-anthropic-secret')
    env.llm.reply = '```json\n' + json.dumps({'answer': 'a' * 10000, 'sources': ['limits']}) + '\n```'
    body = ask(env).json()
    assert body['mode'] == 'ai' and len(body['answer']) <= service.MAX_ANSWER_CHARS


def test_provider_errors_are_redacted_from_responses_and_logs(env, caplog):
    providers.save_key('alice', 'anthropic', 'alice-anthropic-secret')
    env.llm.error = RuntimeError('401 bad key alice-anthropic-secret')
    with caplog.at_level(logging.DEBUG):
        r = ask(env)
    assert r.status_code == 200 and r.json()['mode'] == 'help'
    assert 'alice-anthropic-secret' not in r.text and 'bad key' not in r.text
    assert 'alice-anthropic-secret' not in caplog.text
    assert 'How do I set loss limits' not in caplog.text


def test_one_in_flight_request_per_user_held_through_timeout(env, monkeypatch):
    providers.save_key('alice', 'anthropic', 'alice-anthropic-secret')
    providers.save_key('bob', 'anthropic', 'bob-anthropic-secret')
    monkeypatch.setattr(service, 'TIMEOUT_SECONDS', 0.3)
    gate, started = threading.Event(), threading.Event()
    env.llm.gate, env.llm.started = gate, started
    first = {}
    t = threading.Thread(target=lambda: first.setdefault('r', ask(env)))
    t.start()
    assert started.wait(5)
    assert ask(env).status_code == 429
    t.join(5)
    assert first['r'].json()['mode'] == 'help' and 'timed out' in first['r'].json()['notice']
    # the provider call is still running: the slot is not released early
    assert ask(env).status_code == 429
    env.llm.gate, env.llm.started = None, None
    assert ask(env, user='bob').status_code == 200
    gate.set()
    for _ in range(50):
        if 'alice' not in service._in_flight:
            break
        time.sleep(0.02)
    assert ask(env).status_code == 200


def test_rate_limit_per_user_and_bounded_bookkeeping(env, monkeypatch):
    providers.save_key('alice', 'anthropic', 'alice-anthropic-secret')
    providers.save_key('bob', 'anthropic', 'bob-anthropic-secret')
    for _ in range(service.RATE_LIMIT):
        assert ask(env).status_code == 200
    assert ask(env).status_code == 429
    assert ask(env, user='bob').status_code == 200
    monkeypatch.setattr(service, 'MAX_TRACKED_USERS', 5)
    for i in range(20):
        service._admit(f'user-{i}')
        service._release(f'user-{i}')
    assert len(service._history) <= 5


def test_rejected_requests_do_not_consume_rate_budget(env):
    providers.save_key('alice', 'anthropic', 'alice-anthropic-secret')
    for _ in range(service.RATE_LIMIT + 2):
        assert ask(env, model='custom').status_code == 400
    assert ask(env).status_code == 200


def test_guide_routes_require_authentication(env, monkeypatch):
    from apps.api.auth import auth_middleware
    monkeypatch.setenv('WEBAPP_AUTH_TOKEN', 'app-token')
    app = FastAPI()
    app.middleware('http')(auth_middleware)
    app.include_router(router, prefix='/api')
    cl = TestClient(app)
    assert cl.get('/api/guide/providers').status_code == 401
    assert cl.post('/api/guide/ask', json={}).status_code == 401
    assert cl.get('/api/guide/providers', headers={'Authorization': 'Bearer app-token'}).status_code == 200


def test_build_llm_uses_only_the_personal_key_and_a_fixed_endpoint(monkeypatch):
    seen = []
    monkeypatch.setattr(service, '_tier_client', lambda config, tier, **kw: seen.append((config, tier, kw))
                        or SimpleNamespace(get_llm=lambda: 'llm'))
    assert service._build_llm('anthropic', MODEL, 'alice-key') == 'llm'
    config, tier, kw = seen[0]
    assert tier == 'quick' and config['llm_provider'] == 'anthropic'
    assert config['quick_think_llm'] == MODEL
    assert not config.get('backend_url') and not config.get('quick_think_backend_url')
    assert kw['api_key'] == 'alice-key' and kw['timeout'] == service.TIMEOUT_SECONDS
    assert config['max_tokens'] == service.MAX_OUTPUT_TOKENS
    with pytest.raises(ValueError):
        service._build_llm('anthropic', MODEL, '')


def test_app_registers_guide_routes():
    from apps.api.app import create_app
    paths = set(create_app().openapi()['paths'])
    assert {'/api/guide/providers', '/api/guide/ask'} <= paths
