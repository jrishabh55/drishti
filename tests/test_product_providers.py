"""Product credentials and model tiers must remain isolated per user/provider."""
import json
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from apps.api.integrations import product_providers as providers
from apps.api.jobs.store import reset_store_for_tests
from tradingagents.default_config import DEFAULT_CONFIG


@pytest.fixture
def store(tmp_path, monkeypatch):
    store = reset_store_for_tests(tmp_path / 'runs.sqlite')
    monkeypatch.setenv('WEBAPP_KEY_ENCRYPTION_SECRET', Fernet.generate_key().decode())
    return store


def test_keys_are_encrypted_scoped_and_never_returned(store):
    providers.save_key('alice', 'anthropic', 'alice-private-key')
    providers.save_key('bob', 'anthropic', 'bob-private-key')
    assert providers.load_key('alice', 'anthropic') == 'alice-private-key'
    assert providers.load_key('bob', 'anthropic') == 'bob-private-key'
    assert providers.load_key('alice', 'deepseek') is None
    assert 'alice-private-key' not in store.get_user_key('alice', 'provider:anthropic')
    state = providers.key_status('alice', 'anthropic')
    assert state['last4'] == '-key'
    assert 'alice-private-key' not in json.dumps(state)


def test_storage_requires_encryption_and_known_provider(store, monkeypatch):
    monkeypatch.delenv('WEBAPP_KEY_ENCRYPTION_SECRET')
    with pytest.raises(RuntimeError):
        providers.save_key('alice', 'anthropic', 'private-key')
    with pytest.raises(ValueError):
        providers.save_key('alice', 'https://evil.example', 'private-key')


def test_tier_credentials_do_not_cross_provider_or_user(store, monkeypatch, tmp_path):
    providers.save_key('alice', 'anthropic', 'alice-anthropic')
    providers.save_key('alice', 'deepseek', 'alice-deepseek')
    seen = []
    def client(config, tier, **kwargs):
        seen.append((tier, providers.tier_provider(config, tier), kwargs))
        return SimpleNamespace(get_llm=lambda: SimpleNamespace(name=tier))
    monkeypatch.setattr(providers, 'create_tier_client', client)
    workflow = SimpleNamespace(compile=lambda: 'compiled')
    monkeypatch.setattr(providers, 'GraphSetup', lambda *a: SimpleNamespace(setup_graph=lambda *a, **k: workflow))
    cfg = {**DEFAULT_CONFIG, 'llm_provider': 'anthropic', 'quick_think_provider': 'anthropic',
           'deep_think_provider': 'deepseek', 'data_cache_dir': str(tmp_path/'cache'),
           'results_dir': str(tmp_path/'results'), 'memory_log_path': str(tmp_path/'memory.md')}
    graph = providers.PerTierGraph(config=cfg, user_id='alice')
    assert {(tier, provider, kw['api_key']) for tier, provider, kw in seen} == {
        ('quick', 'anthropic', 'alice-anthropic'), ('deep', 'deepseek', 'alice-deepseek')}
    assert 'alice-anthropic' not in json.dumps(graph.config)
    with pytest.raises(RuntimeError, match='key'):
        providers.provider_kwargs('bob', 'anthropic')


def test_server_key_is_not_implicitly_shared_for_extra_providers(store, monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'admin-private-key')
    with pytest.raises(RuntimeError):
        providers.provider_kwargs('alice', 'anthropic')


def test_tier_selection_changes_cache_identity_without_secret_fields():
    from apps.api.schemas import RunRequest, request_hash
    base = dict(ticker='RELIANCE.NS', analysis_date='2026-10-28', analysts=['market'],
                llm_provider='openai', shallow_thinker='model1', deep_thinker='model2')
    a = RunRequest(**base)
    b = RunRequest(**base, deep_think_provider='anthropic')
    assert request_hash(a) != request_hash(b)
    assert request_hash(a) == request_hash(RunRequest(**base, deep_think_provider='openai'))
