import pytest

import session
import credentials


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    session.reset_session()
    yield
    session.reset_session()


def test_get_session_raises_when_credentials_missing(monkeypatch):
    monkeypatch.setattr(session, "missing_secrets", lambda: [credentials.CLIENT_SECRET])
    with pytest.raises(credentials.CredentialError):
        session.get_session()


def test_get_session_constructs_and_caches_session(monkeypatch):
    created = []

    class _FakeSession:
        def __init__(self, client_secret, refresh_token, is_test):
            created.append((client_secret, refresh_token, is_test))

    monkeypatch.setattr(session, "missing_secrets", lambda: [])
    monkeypatch.setattr(session, "get_secret", lambda key: f"value-{key}")
    monkeypatch.setattr(session, "Session", _FakeSession)

    result1 = session.get_session()
    result2 = session.get_session()

    assert result1 is result2  # cached, not reconstructed
    assert len(created) == 1
    assert created[0] == (f"value-{credentials.CLIENT_SECRET}", f"value-{credentials.REFRESH_TOKEN}", False)


def test_reset_session_clears_cache(monkeypatch):
    class _FakeSession:
        def __init__(self, *a, **k):
            pass

    monkeypatch.setattr(session, "missing_secrets", lambda: [])
    monkeypatch.setattr(session, "get_secret", lambda key: "x")
    monkeypatch.setattr(session, "Session", _FakeSession)

    first = session.get_session()
    session.reset_session()
    second = session.get_session()
    assert first is not second
