"""Wiring tests for EarningsAgent's session shim over cherrypick-core's SessionManager.

Session-caching behavior is covered exhaustively in cherrypick-core's own test suite. Here we verify
EarningsAgent wires a *process-global* manager (thread_local=False — no persistent daemon, short-lived
subprocesses) bound to its credential store, and preserves the get_session / reset_session API.
tastytrade is never constructed — an injected fake factory stands in.
"""

import threading

import pytest
from cherrypick.core.auth import SessionManager

import session


@pytest.fixture(autouse=True)
def _reset():
    session.reset_session()
    yield
    session.reset_session()


def test_manager_is_process_global_and_bound_to_earnings_store():
    assert isinstance(session._manager, SessionManager)
    assert session._manager._thread_local is False
    assert session._manager._creds.service_name == "earningsagent"


def test_public_api_preserved():
    assert callable(session.get_session)
    assert callable(session.reset_session)


def _stub_creds(monkeypatch):
    monkeypatch.setattr(session._manager._creds, "missing_secrets", lambda: [])
    monkeypatch.setattr(session._manager._creds, "get_secret", lambda key: "x")


def test_get_session_constructs_once_and_caches(monkeypatch):
    calls = {"n": 0}

    def factory(cs, rt, is_test):
        calls["n"] += 1
        return object()

    _stub_creds(monkeypatch)
    monkeypatch.setattr(session._manager, "_factory", factory)
    first = session.get_session()
    second = session.get_session()
    assert first is second and calls["n"] == 1


def test_reset_session_clears_cache(monkeypatch):
    def factory(cs, rt, is_test):
        return object()

    _stub_creds(monkeypatch)
    monkeypatch.setattr(session._manager, "_factory", factory)
    first = session.get_session()
    session.reset_session()
    second = session.get_session()
    assert first is not second


def test_process_global_session_shared_across_threads(monkeypatch):
    calls = {"n": 0}

    def factory(cs, rt, is_test):
        calls["n"] += 1
        return object()

    _stub_creds(monkeypatch)
    monkeypatch.setattr(session._manager, "_factory", factory)

    main_session = session.get_session()
    other = []

    def _in_other_thread():
        other.append(session.get_session())

    t = threading.Thread(target=_in_other_thread)
    t.start()
    t.join()

    # One process-wide session, reused across threads.
    assert other[0] is main_session
    assert calls["n"] == 1
