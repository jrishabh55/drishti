import pytest
from cryptography.fernet import Fernet
from apps.api.jobs.store import reset_store_for_tests
from apps.api.desk.store import reset_desk_store_for_tests
from apps.api.desk import notifications


@pytest.fixture
def env(tmp_path, monkeypatch):
    reset_store_for_tests(tmp_path/'jobs.sqlite')
    reset_desk_store_for_tests(tmp_path/'desk.sqlite')
    monkeypatch.setenv('WEBAPP_KEY_ENCRYPTION_SECRET', Fernet.generate_key().decode())


def test_external_delivery_requires_opt_in_and_is_per_user(env, monkeypatch):
    sent=[]
    monkeypatch.setattr(notifications,'_send',lambda token,chat,text: sent.append((chat,text)))
    notifications.configure('a','123:private-token','42',True)
    assert notifications.status('a')['enabled']
    assert 'private-token' not in str(notifications.status('a'))
    event={'id':'evt1','message':'RELIANCE target reached'}
    assert not notifications.deliver('b',event)
    assert notifications.deliver('a',event)
    assert not notifications.deliver('a',event)
    assert sent==[('42','RELIANCE target reached')]


def test_delivery_failure_can_retry_without_losing_in_app_event(env,monkeypatch):
    notifications.configure('a','123:private-token','42',True)
    def fail(*a):
        raise OSError('offline')
    monkeypatch.setattr(notifications,'_send',fail)
    event={'id':'evt1','message':'Target reached'}
    assert not notifications.deliver('a',event)
    assert notifications.status('a')['last_error']=='Telegram delivery failed; in-app alerts remain available'
    sent=[]
    monkeypatch.setattr(notifications,'_send',lambda *a: sent.append(a))
    assert notifications.deliver('a',event)
    assert len(sent)==1
