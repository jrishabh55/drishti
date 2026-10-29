"""Product provider credentials and independently authenticated model tiers.

The upstream constructor forwards one kwargs dictionary to both tiers. This
small product subclass mirrors its initialization so each tier can receive a
separate user's credential without mutating global SDK state or upstream code.
"""
from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import InvalidToken

from apps.api import user_keys
from apps.api.jobs.store import get_store
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.graph.setup import GraphSetup
from tradingagents.graph.conditional_logic import ConditionalLogic
from tradingagents.graph.propagation import Propagator
from tradingagents.llm_clients import create_tier_client, tier_provider
from tradingagents.memory import TradingMemoryLog
from tradingagents.memory.reflection import Reflector
from tradingagents.dataflows.config import set_config

EXTRA_PROVIDERS = {
    'anthropic': 'Anthropic', 'deepseek': 'DeepSeek', 'xai': 'xAI',
    'qwen': 'Qwen (international)', 'qwen-cn': 'Qwen (China)',
    'glm': 'GLM (international)', 'glm-cn': 'GLM (China)',
    'minimax': 'MiniMax (international)', 'minimax-cn': 'MiniMax (China)',
    'openrouter': 'OpenRouter', 'mistral': 'Mistral', 'kimi': 'Kimi',
    'groq': 'Groq', 'nvidia': 'NVIDIA NIM',
}
ADMIN_PROVIDERS = {'azure': 'Azure OpenAI', 'bedrock': 'AWS Bedrock'}
ALL_PROVIDERS = {'openai', 'google', 'chatgpt_helper', *EXTRA_PROVIDERS, *ADMIN_PROVIDERS}


def _known(provider: str) -> str:
    if provider not in EXTRA_PROVIDERS:
        raise ValueError('This provider does not support personal key storage')
    return provider


def save_key(user_id: str, provider: str, key: str) -> None:
    provider = _known(provider)
    f = user_keys._fernet()
    if f is None:
        raise RuntimeError('Encrypted key storage is not configured on this server')
    get_store().set_user_key(user_id, 'provider:' + provider, f.encrypt(key.strip().encode()).decode())


def load_key(user_id: str, provider: str) -> str | None:
    provider = _known(provider)
    f = user_keys._fernet()
    value = get_store().get_user_key(user_id, 'provider:' + provider)
    if not f or not value:
        return None
    try:
        return f.decrypt(value.encode()).decode()
    except InvalidToken:
        return None


def key_status(user_id: str, provider: str) -> dict:
    key = load_key(user_id, provider)
    return {'provider': provider, 'configured': bool(key), 'last4': key[-4:] if key else None,
            'encryption_available': user_keys.encryption_enabled()}


def server_provider_enabled(provider: str) -> bool:
    return provider in {x.strip() for x in os.getenv('WEBAPP_SERVER_PROVIDERS', '').split(',')}


def provider_kwargs(user_id: str, provider: str) -> dict:
    if provider == 'openai':
        return {}  # existing credit-funded server provider
    if provider == 'google':
        return user_keys.resolve_gemini_provider_kwargs(user_id)
    if provider in EXTRA_PROVIDERS:
        key = load_key(user_id, provider)
        if not key:
            raise RuntimeError(f'Add your {EXTRA_PROVIDERS[provider]} API key in Model connections')
        return {'api_key': key}
    if provider in ADMIN_PROVIDERS:
        if not server_provider_enabled(provider):
            raise RuntimeError(f'{ADMIN_PROVIDERS[provider]} requires administrator connection setup')
        if provider == 'bedrock':
            import importlib.util
            if importlib.util.find_spec('langchain_aws') is None:
                raise RuntimeError('AWS Bedrock support is not installed on this server')
        return {}
    raise RuntimeError(f'Unsupported provider {provider}')


def providers_for(request) -> set[str]:
    """Each tier's provider, normalised: policy must not depend on a caller's casing."""
    return {(getattr(request, f'{tier}_think_provider', None) or request.llm_provider or '').strip().lower()
            for tier in ('quick', 'deep')}


def validate_connections(request, user_id: str) -> None:
    from apps.api.integrations.helper_backend import helper_ready
    for provider in providers_for(request):
        if provider == 'chatgpt_helper':
            if not helper_ready(user_id):
                raise RuntimeError('Your ChatGPT helper is not connected')
        elif provider == 'google':
            if not user_keys.gemini_credential_available(user_id):
                raise RuntimeError('Add your Gemini API key in the model settings')
        else:
            provider_kwargs(user_id, provider)


def uses_server_credit(request) -> bool:
    return bool(providers_for(request) & {'openai', *ADMIN_PROVIDERS})


class PerTierGraph(TradingAgentsGraph):
    """Core workflow, using one credential resolver for each selected tier."""
    def __init__(self, selected_analysts=('market', 'social', 'news', 'fundamentals'),
                 debug=False, config=None, callbacks=None, *, user_id: str,
                 provider_overrides: dict | None = None):
        self.debug = debug
        self.config = config or DEFAULT_CONFIG
        self.callbacks = callbacks or []
        set_config(self.config)
        for key in ('data_cache_dir', 'results_dir'):
            Path(self.config[key]).mkdir(parents=True, exist_ok=True)
        overrides = provider_overrides or {}
        for tier in ('quick', 'deep'):
            provider = tier_provider(self.config, tier)
            private = overrides[provider] if provider in overrides else provider_kwargs(user_id, provider)
            kwargs = {**self._get_provider_kwargs(), **private}
            setattr(self, f'{tier}_thinking_llm', create_tier_client(self.config, tier, **kwargs).get_llm())
        self.memory_log = TradingMemoryLog(self.config)
        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=self.config['max_debate_rounds'],
            max_risk_discuss_rounds=self.config['max_risk_discuss_rounds'])
        rounds = self.config['max_tool_rounds']
        if 2 * rounds + 2 >= self.config['max_recur_limit']:
            raise ValueError('max_recur_limit must exceed the analyst tool-round bound')
        self.graph_setup = GraphSetup(self.quick_thinking_llm, self.deep_thinking_llm,
                                      self.conditional_logic, rounds)
        self.propagator = Propagator(max_recur_limit=self.config['max_recur_limit'])
        self.reflector = Reflector(self.quick_thinking_llm)
        self.selected_analysts = tuple(selected_analysts)
        self.workflow = self.graph_setup.setup_graph(selected_analysts, memory_node=self._memory_step)
        self.graph = self.workflow.compile()
        self._checkpointer_ctx = None
        self._resuming = False


def needs_product_graph(request) -> bool:
    return bool(providers_for(request) - {'openai', 'google', 'chatgpt_helper'}) or bool(
        getattr(request, 'quick_think_provider', None) or getattr(request, 'deep_think_provider', None))


def configure_tiers(request, config: dict, user_id: str) -> tuple[dict, str | None]:
    """Resolve endpoints on the server; keep helper credentials out of config."""
    from apps.api.integrations.helper_backend import (
        local_helper_reachable, helper_base_url, helper_credential, relay_available, relay_shim_url)
    overrides = {}
    token = None
    helper_url = None
    if 'chatgpt_helper' in providers_for(request):
        if local_helper_reachable():
            helper_url, key = helper_base_url(), helper_credential()
        elif relay_available(user_id):
            from apps.api.routes.relay import get_internal_tokens
            token = get_internal_tokens().mint(user_id)
            helper_url, key = relay_shim_url(), token
        else:
            raise RuntimeError('Your ChatGPT helper is not connected')
        overrides['openai_compatible'] = {'api_key': key}
    if config['llm_provider'] == 'chatgpt_helper':
        config['llm_provider'] = 'openai_compatible'
        config['backend_url'] = helper_url
    for tier in ('quick', 'deep'):
        value = getattr(request, f'{tier}_think_provider', None) or request.llm_provider
        config[f'{tier}_think_provider'] = 'openai_compatible' if value == 'chatgpt_helper' else value
        config[f'{tier}_think_backend_url'] = helper_url if value == 'chatgpt_helper' else None
    return {'user_id': user_id, 'provider_overrides': overrides}, token
