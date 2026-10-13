"""cherrypick.core.broker.cli -- the read-only CLI verbs the modules' seams share."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from cherrypick.core.broker import cli


def test_error_result_marks_a_5xx_retryable_and_nothing_else():
    assert cli.error_result(RuntimeError("upstream said 503 Service Unavailable")) == {
        "ok": False,
        "error": "RuntimeError: upstream said 503 Service Unavailable",
        "retryable": True,
    }
    assert "retryable" not in cli.error_result(ValueError("bad input"))


class _Store:
    def __init__(self, present):
        self._present = present
        self.saved = {}

    def secrets_present(self):
        return self._present

    def secrets_status(self):
        return {"client_secret": self._present}

    def set_secret(self, key, value):
        self.saved[key] = value


def _creds(present=True, session=None, designated="5WX12345678"):
    def get_session():
        if isinstance(session, Exception):
            raise session
        return session

    return SimpleNamespace(
        SERVICE_NAME="testagent",
        store=_Store(present),
        get_session=get_session,
        designated_account=lambda: designated,
    )


def test_connection_status_masks_the_account_and_never_opens_a_session_without_credentials():
    out = asyncio.run(
        cli.connection_status(_creds(present=False), live_trading_enabled=False, hint="connect")
    )
    assert out["designated"] == "****5678" and out["hint"] == "connect" and "account_count" not in out
    failed = asyncio.run(
        cli.connection_status(_creds(session=RuntimeError("401")), live_trading_enabled=True, hint="x")
    )
    assert failed["ok"] is False and failed["error"] == "RuntimeError: 401"


def test_connection_check_is_not_ok_without_credentials():
    out = asyncio.run(
        cli.connection_check(lambda: None, credentials_present=False, live_trading_enabled=False, hint="h")
    )
    assert out == {"ok": False, "live_trading_enabled": False, "credentials_present": False, "hint": "h"}


def test_secrets_set_keeps_a_stored_value_on_blank_input(monkeypatch):
    import getpass

    answers = iter(["new-secret", "  "])
    monkeypatch.setattr(getpass, "getpass", lambda prompt: next(answers))
    creds = _creds()
    out = cli.secrets_set(creds, ["client_secret", "refresh_token"])
    assert creds.store.saved == {"client_secret": "new-secret"} and out["service"] == "testagent"
