"""Per-user Kite Connect app credentials and the daily session.

* **App credentials** (``api_key`` + ``api_secret``) are pasted once on the
  connection page and stored Fernet-encrypted with the same
  ``WEBAPP_KEY_ENCRYPTION_SECRET`` idiom as user_keys.py. Saving is refused
  when the secret isn't configured — never plaintext.
* **Login**: ``begin_login`` mints a single-use state nonce bound to the
  signed-in user, valid 5 minutes, carried through Kite's ``redirect_params``.
  Kite redirects the browser to the app's connection page with
  ``request_token`` + ``state``; that page (still signed in) posts both to
  ``/api/kite/callback``. The nonce must exist, be unused, unexpired, AND
  belong to the caller — a replayed or cross-user callback is refused.
* **Session**: the ``access_token`` is encrypted at rest and expires at the
  next 06:00 IST after login (Kite's regulatory daily expiry).

Nothing here ever returns a secret to a caller outside the server: statuses
carry the api_key's last 4 characters at most.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

from apps.api import user_keys
from apps.api.kite import rest
from apps.api.kite.store import get_kite_store
from apps.api.scanner.calendar import IST

logger = logging.getLogger(__name__)

APP, SESSION = "app", "session"
STATE_TTL = timedelta(minutes=5)
_KEY_RE = re.compile(r"^[A-Za-z0-9]{6,64}$")


class KiteAuthError(RuntimeError):
    """Plain-language refusal; ``code`` lets the UI pick a next step."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _encrypt(obj: Dict[str, Any]) -> str:
    f = user_keys._fernet()
    if f is None:
        raise KiteAuthError("encryption_unconfigured",
                            f"{user_keys.ENCRYPTION_KEY_ENV} is not set on the server, so Kite "
                            "credentials cannot be stored safely. Ask the admin to set it.")
    return f.encrypt(json.dumps(obj).encode()).decode()


def _decrypt(user_id: str, kind: str) -> Optional[Dict[str, Any]]:
    ciphertext = get_kite_store().get_secret(user_id, kind)
    f = user_keys._fernet()
    if not ciphertext or f is None:
        return None
    from cryptography.fernet import InvalidToken

    try:
        return json.loads(f.decrypt(ciphertext.encode()).decode())
    except InvalidToken:
        logger.warning("stored Kite %s for %s undecryptable (secret rotated?)", kind, user_id)
        return None


# ---------------------------------------------------------------- app credentials

def save_app(user_id: str, api_key: str, api_secret: str) -> None:
    api_key, api_secret = api_key.strip(), api_secret.strip()
    if not _KEY_RE.match(api_key) or not _KEY_RE.match(api_secret):
        raise KiteAuthError("invalid_credentials",
                            "api_key and api_secret are the letters-and-digits values from your "
                            "app on developers.kite.trade")
    get_kite_store().put_secret(user_id, APP, _encrypt({"api_key": api_key,
                                                        "api_secret": api_secret}))
    # A new app invalidates any session minted with the old one.
    get_kite_store().delete_secret(user_id, SESSION)


def load_app(user_id: str) -> Optional[Tuple[str, str]]:
    doc = _decrypt(user_id, APP)
    return (doc["api_key"], doc["api_secret"]) if doc else None


# ---------------------------------------------------------------- login

def login_url(api_key: str, nonce: str) -> str:
    return f"{rest.LOGIN_URL}?" + urlencode({"v": 3, "api_key": api_key,
                                             "redirect_params": urlencode({"state": nonce})})


def begin_login(user_id: str, now: datetime) -> str:
    app = load_app(user_id)
    if app is None:
        raise KiteAuthError("app_missing", "save your Kite Connect api_key and api_secret first")
    nonce = secrets.token_urlsafe(24)
    get_kite_store().add_state(nonce, user_id, (now + STATE_TTL).isoformat())
    return login_url(app[0], nonce)


def session_expiry(login_at: datetime) -> datetime:
    """The next 06:00 IST strictly after ``login_at``."""
    local = login_at.astimezone(IST)
    six = datetime.combine(local.date(), time(6, 0), IST)
    return (six if local < six else six + timedelta(days=1)).astimezone(timezone.utc)


def complete_login(user_id: str, request_token: str, state: str, now: datetime,
                   transport: Optional[rest.Transport] = None) -> Dict[str, Any]:
    row = get_kite_store().consume_state(state or "")
    if row is None or row["user_id"] != user_id:
        raise KiteAuthError("state_mismatch", "this Kite login didn't start from your session; "
                                              "start the login again")
    if row["used"]:
        raise KiteAuthError("state_replayed", "this Kite login link was already used; start the "
                                              "login again")
    if datetime.fromisoformat(row["expires_at"]) < now:
        raise KiteAuthError("state_expired", "the Kite login took longer than 5 minutes; start "
                                             "the login again")
    app = load_app(user_id)
    if app is None:
        raise KiteAuthError("app_missing", "save your Kite Connect api_key and api_secret first")
    if not request_token or not _KEY_RE.match(request_token):
        raise KiteAuthError("bad_request_token", "Kite didn't send a usable request_token")
    try:
        data = rest.KiteClient(app[0], transport=transport).generate_session(request_token, app[1])
    except rest.KiteError as exc:
        raise KiteAuthError("exchange_failed", f"Kite refused the login: {exc}") from exc
    doc = {"access_token": data["access_token"], "kite_user_id": data.get("user_id"),
           "user_name": data.get("user_name"), "login_at": now.isoformat(),
           "expires_at": session_expiry(now).isoformat()}
    get_kite_store().put_secret(user_id, SESSION, _encrypt(doc))
    return session_status(user_id, now)


# ---------------------------------------------------------------- session

def load_session(user_id: str, now: datetime) -> Optional[Dict[str, Any]]:
    """The live session (with its token, server-side only), or None."""
    doc = _decrypt(user_id, SESSION)
    if not doc or doc.get("revoked") or datetime.fromisoformat(doc["expires_at"]) <= now:
        return None
    return doc


def client_for(user_id: str, now: datetime, transport: Optional[rest.Transport] = None
               ) -> rest.KiteClient:
    app, sess = load_app(user_id), load_session(user_id, now)
    if app is None or sess is None:
        raise KiteAuthError("not_connected", "Kite is not connected for today; log in to Zerodha")
    return rest.KiteClient(app[0], sess["access_token"], transport)


def mark_revoked(user_id: str) -> None:
    """Kite answered TokenException: keep the row for status, never reuse it."""
    doc = _decrypt(user_id, SESSION)
    if doc:
        doc["revoked"] = True
        get_kite_store().put_secret(user_id, SESSION, _encrypt(doc))


def disconnect(user_id: str, now: datetime, *, forget_app: bool = False,
               transport: Optional[rest.Transport] = None) -> Dict[str, Any]:
    """Log out at Kite (best effort), drop the session, optionally the app keys."""
    note = None
    app, sess = load_app(user_id), load_session(user_id, now)
    if app and sess:
        try:
            rest.KiteClient(app[0], sess["access_token"], transport).invalidate_session()
        except rest.KiteError as exc:
            note = f"Kite logout failed ({exc}); the local session was removed anyway"
    store = get_kite_store()
    store.delete_secret(user_id, SESSION)
    if forget_app:
        store.delete_secret(user_id, APP)
    out = session_status(user_id, now)
    out["note"] = note
    return out


def session_status(user_id: str, now: datetime) -> Dict[str, Any]:
    stored = get_kite_store().get_secret(user_id, APP) is not None
    app = load_app(user_id)
    raw = _decrypt(user_id, SESSION)
    if raw is None:
        state = "none"
    elif raw.get("revoked"):
        state = "revoked"
    elif datetime.fromisoformat(raw["expires_at"]) <= now:
        state = "expired"
    else:
        state = "active"
    return {
        "encryption_configured": user_keys.encryption_enabled(),
        "app_configured": app is not None,
        # Stored but undecryptable → the server's secret was rotated.
        "app_unreadable": stored and app is None,
        "api_key_last4": app[0][-4:] if app else None,
        "session": state,
        "kite_user_id": raw.get("kite_user_id") if raw else None,
        "user_name": raw.get("user_name") if raw else None,
        "login_at": raw.get("login_at") if raw else None,
        "expires_at": raw.get("expires_at") if raw else None,
    }
