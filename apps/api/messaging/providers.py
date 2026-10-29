"""Bounded provider requests. Never propagate credential-bearing HTTP errors."""
import os
import re
from urllib.parse import urlsplit
import requests


class ProviderError(RuntimeError):
    def __init__(self, message='Messaging provider is unavailable; try again.', status=None):
        super().__init__(message)
        self.status = status


def origin(value):
    p = urlsplit(value.strip())
    if (p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password
            or p.query or p.fragment or p.path not in ('', '/')):
        raise ValueError('Enter a server origin without credentials, path, query or fragment')
    try:
        port = p.port
    except ValueError:
        raise ValueError('Invalid server port') from None
    host = p.hostname.lower()
    if ':' in host:
        host = '[' + host + ']'
    return f'{p.scheme}://{host}' + (f':{port}' if port and port != (443 if p.scheme == 'https' else 80) else '')


def trusted_base(value=''):
    value = value.strip() or os.getenv('EVOLUTION_API_URL', '').strip()
    if not value:
        raise ValueError('Ask the administrator to configure EVOLUTION_API_URL')
    base = origin(value)
    allowed = [os.getenv('EVOLUTION_API_URL', '')] + os.getenv('EVOLUTION_ALLOWED_ORIGINS', '').split(',')
    if base not in {origin(v) for v in allowed if v.strip()}:
        raise ValueError('This Evolution server is not approved; ask the administrator to allow its origin')
    return base


def public_base():
    base = origin(os.getenv('MESSAGING_PUBLIC_BASE_URL', ''))
    if not base.startswith('https://'):
        raise ValueError('Administrator must set MESSAGING_PUBLIC_BASE_URL to the public HTTPS app origin')
    return base


def _request(method, url, **kw):
    try:
        r = requests.request(method, url, timeout=(3, 8), allow_redirects=False, **kw)
        if not 200 <= r.status_code < 300:
            raise ProviderError('Messaging provider rejected the request. Check its connection and credentials.', r.status_code)
        doc = r.json()
        if not isinstance(doc, (dict, list, bool)):
            raise ProviderError('Messaging provider returned an unsupported response')
        return doc
    except ProviderError:
        raise
    except Exception:
        raise ProviderError() from None


def evolution(base, key, path, method='GET', body=None, params=None):
    base = trusted_base(base)
    if not key:
        raise ValueError('Evolution API key is not configured')
    if not re.fullmatch(r'/(?:instance|webhook|message)/(?:[A-Za-z]+)(?:/drishti-[a-f0-9]+)?', path) and path != '/':
        raise ValueError('Unsupported Evolution endpoint')
    return _request(method, base + path, headers={'apikey': key}, json=body, params=params)


def telegram(token, method, body=None):
    if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token):
        raise ValueError('Enter a valid Telegram bot token')
    if method not in {'getMe','getWebhookInfo','setWebhook','deleteWebhook','sendMessage','editMessageReplyMarkup','answerCallbackQuery'}:
        raise ValueError('Unsupported Telegram operation')
    doc = _request('POST', f'https://api.telegram.org/bot{token}/{method}', json=body or {})
    if not isinstance(doc, dict) or not doc.get('ok'):
        raise ProviderError('Telegram rejected the request; check the bot configuration')
    return doc['result']
