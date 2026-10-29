"""Kite connection: encrypted per-user credentials, state-bound login, daily
expiry, ticks → private bars, live/disconnect lifecycle, delayed fallback."""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet

from apps.api.desk import bars_source
from apps.api.desk.market.session import NSE
from apps.api.desk.store import reset_desk_store_for_tests
from apps.api.kite import auth, live, rest, ticker
from apps.api.kite.store import reset_kite_store_for_tests
from apps.api.routes import kite as kite_routes
from tests.desk_utils import desk_client
from tests.kite_fake import FakeKite, FakeSocket, frame, quote_packet
from tests.scanner_utils import make_store

DAY = date(2026, 10, 28)


def ist(h, m, s=0, d=DAY):
    return datetime(d.year, d.month, d.day, h, m, s, tzinfo=NSE.tz).astimezone(timezone.utc)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBAPP_KEY_ENCRYPTION_SECRET", Fernet.generate_key().decode())
    reset_kite_store_for_tests(tmp_path / "kite.sqlite")
    reset_desk_store_for_tests(tmp_path / "desk.sqlite")
    make_store(tmp_path, None)
    rest.clear_instruments_for_tests()
    live.clear_for_tests()
    fake = FakeKite()
    clock = {"now": ist(9, 0)}
    monkeypatch.setattr(kite_routes, "_now", lambda: clock["now"])
    monkeypatch.setattr(kite_routes, "_transport", fake)
    client, user = desk_client(kite_routes.router)
    yield client, user, fake, clock
    bars_source.clear_live_providers_for_tests()
    live.clear_for_tests()


def _connect(client, fake):
    assert client.put("/api/kite/app", json={"api_key": fake.api_key,
                                             "api_secret": fake.api_secret}).status_code == 200
    url = client.post("/api/kite/login").json()["login_url"]
    q = parse_qs(urlparse(url).query)
    assert q["api_key"] == [fake.api_key] and q["v"] == ["3"]
    state = parse_qs(q["redirect_params"][0])["state"][0]
    r = client.post("/api/kite/callback", json={"request_token": "reqtok123", "state": state,
                                                "status": "success"})
    assert r.status_code == 200, r.text
    return state


def _no_secret(text: str, fake: FakeKite):
    assert fake.api_secret not in text
    for tok in fake.tokens_issued:
        assert tok not in text


def test_saving_refused_without_encryption_secret(env, monkeypatch):
    client, _, fake, _ = env
    monkeypatch.delenv("WEBAPP_KEY_ENCRYPTION_SECRET")
    st = client.get("/api/kite/status").json()
    assert st["encryption_configured"] is False and st["app_configured"] is False
    r = client.put("/api/kite/app", json={"api_key": fake.api_key, "api_secret": fake.api_secret})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "encryption_unconfigured"


def test_credentials_encrypted_isolated_and_never_returned(env, tmp_path):
    client, user, fake, _ = env
    _connect(client, fake)
    st = client.get("/api/kite/status")
    _no_secret(st.text, fake)
    body = st.json()
    assert body["session"] == "active" and body["api_key_last4"] == fake.api_key[-4:]
    assert body["kite_user_id"] == "AB1234"
    raw = (tmp_path / "kite.sqlite").read_bytes()
    assert fake.api_secret.encode() not in raw and fake.tokens_issued[0].encode() not in raw
    user["id"] = "user_b"
    other = client.get("/api/kite/status").json()
    assert other["app_configured"] is False and other["session"] == "none"
    assert client.post("/api/kite/login").status_code == 409
    with pytest.raises(auth.KiteAuthError):
        auth.client_for("user_b", ist(10, 0))


def test_login_state_mismatch_replay_and_expiry_refused(env):
    client, user, fake, clock = env
    client.put("/api/kite/app", json={"api_key": fake.api_key, "api_secret": fake.api_secret})
    url = client.post("/api/kite/login").json()["login_url"]
    state = parse_qs(parse_qs(urlparse(url).query)["redirect_params"][0])["state"][0]
    # Another signed-in user can't redeem it (and burns nothing of theirs).
    user["id"] = "user_b"
    client.put("/api/kite/app", json={"api_key": fake.api_key, "api_secret": fake.api_secret})
    r = client.post("/api/kite/callback", json={"request_token": "reqtok123", "state": state})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "state_mismatch"
    user["id"] = "user_a"
    bogus = client.post("/api/kite/callback", json={"request_token": "reqtok123", "state": "nope"})
    assert bogus.json()["detail"]["code"] == "state_mismatch"
    # The state the user_b attempt consumed is now spent: replay refused.
    r = client.post("/api/kite/callback", json={"request_token": "reqtok123", "state": state})
    assert r.json()["detail"]["code"] == "state_replayed"
    # A fresh state that's older than 5 minutes is refused.
    url = client.post("/api/kite/login").json()["login_url"]
    state = parse_qs(parse_qs(urlparse(url).query)["redirect_params"][0])["state"][0]
    clock["now"] += timedelta(minutes=6)
    r = client.post("/api/kite/callback", json={"request_token": "reqtok123", "state": state})
    assert r.json()["detail"]["code"] == "state_expired"
    assert fake.tokens_issued == []          # no exchange ever happened
    # Kite reporting a failed login is not exchanged either.
    r = client.post("/api/kite/callback", json={"request_token": "x", "state": "y",
                                                "status": "cancelled"})
    assert r.status_code == 400


def test_session_expires_at_six_ist_and_token_errors_revoke(env):
    client, _, fake, clock = env
    _connect(client, fake)
    st = client.get("/api/kite/status").json()
    assert datetime.fromisoformat(st["expires_at"]) == datetime(2026, 10, 29, 6, 0, tzinfo=NSE.tz)
    assert auth.session_expiry(datetime(2026, 10, 29, 5, 59, tzinfo=NSE.tz)) == \
        datetime(2026, 10, 29, 6, 0, tzinfo=NSE.tz)
    assert client.get("/api/kite/quote?symbols=SBIN").json()["quotes"]["SBIN"]["last_price"] == 801.5
    clock["now"] = datetime(2026, 10, 29, 6, 0, tzinfo=NSE.tz).astimezone(timezone.utc)
    assert client.get("/api/kite/status").json()["session"] == "expired"
    r = client.get("/api/kite/quote?symbols=SBIN")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_connected"
    # A token Kite revoked early (TokenException) is marked and never reused.
    clock["now"] = ist(10, 0)
    _connect(client, fake)
    fake.revoked.add(fake.tokens_issued[-1])
    r = client.get("/api/kite/quote?symbols=SBIN")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "session_expired"
    assert client.get("/api/kite/status").json()["session"] == "revoked"


def test_disconnect_logs_out_and_forget_removes_app(env):
    client, _, fake, _ = env
    _connect(client, fake)
    tok = fake.tokens_issued[-1]
    out = client.post("/api/kite/disconnect").json()
    assert tok in fake.revoked and out["session"] == "none" and out["app_configured"] is True
    _no_secret(json.dumps(out), fake)
    out = client.delete("/api/kite/app").json()
    assert out["app_configured"] is False


# ---------------------------------------------------------------- ticks

def test_binary_framing_quote_full_ltp_and_heartbeat():
    full = quote_packet(779521, 80150, 5000) + bytes(16) + \
        (int(ist(9, 20).timestamp())).to_bytes(4, "big") + bytes(120)
    ltp = (738561).to_bytes(4, "big") + (250010).to_bytes(4, "big")
    ticks = ticker.parse_binary(frame(quote_packet(779521, 80025, 1200), full, ltp))
    assert [(t.token, t.ltp, t.volume) for t in ticks] == [
        (779521, 800.25, 1200), (779521, 801.5, 5000), (738561, 2500.1, None)]
    assert ticks[1].exchange_ts == ist(9, 20) and ticks[0].exchange_ts is None
    assert ticker.parse_binary(b"\x00") == []
    with pytest.raises(ValueError):
        ticker.parse_binary(frame(quote_packet(1, 1, 1))[:-3])
    assert ticker.parse_text('{"type":"order","data":{"tag":"Dx"}}')["type"] == "order"


def test_ticks_aggregate_into_session_aligned_1m_and_5m_bars():
    b = ticker.BarBuilder()
    assert not b.on_tick("SBIN", 800.0, 100, ist(9, 14, 59))     # pre-open ignored
    seq = [(ist(9, 15, 1), 800.0, 1000), (ist(9, 15, 30), 802.0, 1300),
           (ist(9, 16, 5), 799.0, 1400), (ist(9, 19, 59), 801.0, 1600),
           (ist(9, 20, 0), 803.0, 2000)]
    for at, px, vol in seq:
        assert b.on_tick("SBIN", px, vol, at)
    one = b.rows("SBIN", "1m")
    assert [(r[0], r[1:]) for r in one] == [
        (ist(9, 15), (800.0, 802.0, 800.0, 802.0, 300.0)),
        (ist(9, 16), (799.0, 799.0, 799.0, 799.0, 100.0)),
        (ist(9, 19), (801.0, 801.0, 801.0, 801.0, 200.0)),
        (ist(9, 20), (803.0, 803.0, 803.0, 803.0, 400.0))]
    five = b.rows("SBIN", "5m")
    assert [(r[0], r[1:]) for r in five] == [
        (ist(9, 15), (800.0, 802.0, 799.0, 801.0, 600.0)),
        (ist(9, 20), (803.0, 803.0, 803.0, 803.0, 400.0))]
    # The 09:20 bar is final only after 09:25:02.
    assert len(b.final(five, "5m", ist(9, 25, 1))) == 1
    assert len(b.final(five, "5m", ist(9, 25, 2))) == 2
    assert not b.on_tick("SBIN", 800.0, 2100, ist(15, 30))       # close ignored


def test_live_hub_serves_private_bars_reconnects_and_falls_back_on_disconnect(env):
    client, user, fake, clock = env
    _connect(client, fake)
    clock["now"] = ist(9, 20, 3)
    sockets = []
    attempts = {"n": 0}

    async def connect(url):
        attempts["n"] += 1
        assert "access_token=" in url
        if attempts["n"] == 1:
            raise OSError("network blip")
        sock = FakeSocket([frame(quote_packet(779521, 80000, 1000)),
                           b"\x00",
                           frame(quote_packet(779521, 80300, 1500)),
                           '{"type":"order","data":{"tag":"Dabc","status":"COMPLETE"}}'])
        sockets.append(sock)
        return sock

    seen = []
    live.order_listeners.append(lambda uid, d: seen.append((uid, d["status"])))
    times = iter([ist(9, 19, 1), ist(9, 19, 30)] + [ist(9, 20, 3)] * 50)

    async def scenario():
        hub = await live.start("user_a", ["SBIN"], transport=fake, connect=connect,
                               now=clock["now"])
        await hub.backfill_task   # started at 09:20: today's session so far comes first
        assert hub.backfilled == {"SBIN"}
        hub.task.cancel()  # restart with a deterministic clock and fast backoff
        hub.task = asyncio.create_task(ticker.run_ticker(
            api_key=fake.api_key, access_token=fake.tokens_issued[-1], tokens={779521: "SBIN"},
            on_tick=lambda s, t, at: hub.builder.on_tick(s, t.ltp, t.volume, next(times)),
            on_text=lambda m: [fn("user_a", m["data"]) for fn in live.order_listeners],
            on_state=lambda st, d: setattr(hub, "state", st) or (
                st == "reconnecting" and setattr(hub, "reconnects", hub.reconnects + 1)),
            stop_at=ist(15, 35), connect=connect, clock=lambda: ist(9, 20, 3), backoff=(0,)))
        for _ in range(100):
            await asyncio.sleep(0)
            if seen:
                break
        assert hub.state == "live" and hub.reconnects >= 1
        subscribe = [json.loads(m) for m in sockets[-1].sent]
        assert subscribe[0] == {"a": "subscribe", "v": [779521]}
        assert subscribe[1] == {"a": "mode", "v": ["quote", [779521]]}
        got = live.provider("user_a", "SBIN", "1m", "session", now=ist(9, 20, 3))
        assert got.source == "live:kite" and got.rows[0][4] == 803.0 and got.rows[0][5] == 500.0
        # Another user never sees this user's licensed bars.
        assert live.provider("user_b", "SBIN", "1m", "session", now=ist(9, 20, 3)) is None
        # Stale (no tick for > 90 s) → explicit fallback.
        assert live.provider("user_a", "SBIN", "1m", "session", now=ist(9, 21, 31)) is None
        await live.stop("user_a")
        assert sockets[-1].closed

    asyncio.run(scenario())
    assert seen == [("user_a", "COMPLETE")]
    assert live.provider("user_a", "SBIN", "1m", "session", now=ist(9, 20, 3)) is None
    st = client.get("/api/kite/status").json()
    assert st["live"]["state"] == "off"


def test_bars_source_uses_live_then_delayed_and_never_writes_shared_cache(env, monkeypatch, tmp_path):
    client, _, fake, clock = env
    _connect(client, fake)
    live.install()
    hub = live.Hub("user_a", ["SBIN"], state="live", backfilled={"SBIN"})
    hub.builder.on_tick("SBIN", 800.0, 10, ist(9, 15, 5))
    live._hubs["user_a"] = hub
    monkeypatch.setattr(live, "_now", lambda: ist(9, 15, 30))
    from apps.api.scanner.store import get_scanner_store

    got = bars_source.get_session_rows("user_a", "SBIN", "1m", now=ist(9, 15, 30))
    assert got.source == "live:kite"
    assert get_scanner_store().load_symbol_bars("1m", "SBIN", limit=10).empty
    # Other users and stale hubs fall through to the delayed path, labelled so.
    from apps.api.desk import bars as bars_mod

    monkeypatch.setattr(bars_mod, "fetch_history", lambda *a, **k: [])
    other = bars_source.get_session_rows("user_b", "SBIN", "1m", now=ist(9, 15, 30))
    assert other.source == "delayed:yahoo" and other.notes
    hub.state = "reconnecting"
    assert bars_source.get_session_rows("user_a", "SBIN", "1m",
                                        now=ist(9, 15, 30)).source == "delayed:yahoo"


def test_history_from_kite_is_per_user(env):
    client, _, fake, clock = env
    _connect(client, fake)
    fake.candles = [["2026-10-27T09:15:00+0530", 800, 801, 799, 800.5, 1200]]
    got = live.provider("user_a", "SBIN", "5m", "history", now=ist(10, 0), transport=fake)
    assert got.source == "live:kite" and got.rows[0][0] == ist(9, 15, d=date(2026, 10, 27))
    assert live.provider("user_b", "SBIN", "5m", "history", now=ist(10, 0), transport=fake) is None
