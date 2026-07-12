"""Wiring tests for EarningsAgent's credentials shim over cherrypick-core.

Exhaustive keyring behavior lives in cherrypick-core's own test suite. Here we verify EarningsAgent
wires the shared CredentialStore with its service name (no legacy fallback) and preserves the
module-level API. keyring's password functions are monkeypatched to an in-memory fake (patched
globally, since the keyring calls now happen inside cherrypick-core).
"""

import keyring
import keyring.errors
import pytest

import credentials


class _FakeKeyring:
    def __init__(self):
        self.store = {}

    def get_password(self, service, entry):
        return self.store.get((service, entry))

    def set_password(self, service, entry, value):
        self.store[(service, entry)] = value

    def delete_password(self, service, entry):
        key = (service, entry)
        if key not in self.store:
            raise keyring.errors.PasswordDeleteError("not found")
        del self.store[key]


@pytest.fixture(autouse=True)
def fake_keyring(monkeypatch):
    fake = _FakeKeyring()
    monkeypatch.setattr(keyring, "get_password", fake.get_password)
    monkeypatch.setattr(keyring, "set_password", fake.set_password)
    monkeypatch.setattr(keyring, "delete_password", fake.delete_password)
    return fake


def test_store_is_configured_for_earnings():
    assert credentials.store.service_name == "earningsagent"
    assert credentials.store.legacy_service_names == ()  # no legacy fallback


def test_public_api_preserved():
    for name in ("get_secret", "set_secret", "delete_secret",
                 "secrets_present", "missing_secrets", "secrets_status"):
        assert callable(getattr(credentials, name)), name
    for const in ("CLIENT_SECRET", "REFRESH_TOKEN", "ACCOUNT_NUMBER",
                  "REQUIRED_SECRETS", "ALL_SECRETS", "CredentialError"):
        assert hasattr(credentials, const), const


def test_set_and_get_secret_roundtrip(fake_keyring):
    credentials.set_secret(credentials.CLIENT_SECRET, "sekrit")
    assert credentials.get_secret(credentials.CLIENT_SECRET) == "sekrit"
    assert fake_keyring.store[("earningsagent", "production:client_secret")] == "sekrit"


def test_present_missing_and_status(fake_keyring):
    assert credentials.secrets_present() is False
    credentials.set_secret(credentials.CLIENT_SECRET, "x")
    assert credentials.REFRESH_TOKEN in credentials.missing_secrets()
    status = credentials.secrets_status()
    assert status[credentials.CLIENT_SECRET] is True
    assert status[credentials.REFRESH_TOKEN] is False


def test_delete_secret_absent_is_a_noop(fake_keyring):
    credentials.delete_secret(credentials.CLIENT_SECRET)  # must not raise


def test_get_secret_translates_no_keyring_error(monkeypatch):
    def _raise(*_a, **_k):
        raise keyring.errors.NoKeyringError("no backend")

    monkeypatch.setattr(keyring, "get_password", _raise)
    with pytest.raises(credentials.CredentialError):
        credentials.get_secret(credentials.CLIENT_SECRET)
