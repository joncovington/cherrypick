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
    monkeypatch.setattr(credentials.keyring, "get_password", fake.get_password)
    monkeypatch.setattr(credentials.keyring, "set_password", fake.set_password)
    monkeypatch.setattr(credentials.keyring, "delete_password", fake.delete_password)
    return fake


def test_set_and_get_secret_roundtrip():
    credentials.set_secret(credentials.CLIENT_SECRET, "sekrit")
    assert credentials.get_secret(credentials.CLIENT_SECRET) == "sekrit"


def test_get_secret_missing_returns_none():
    assert credentials.get_secret(credentials.CLIENT_SECRET) is None


def test_secrets_present_false_when_required_missing():
    assert credentials.secrets_present() is False


def test_secrets_present_true_when_all_required_set():
    for key in credentials.REQUIRED_SECRETS:
        credentials.set_secret(key, "x")
    assert credentials.secrets_present() is True


def test_missing_secrets_lists_only_unset_required():
    credentials.set_secret(credentials.CLIENT_SECRET, "x")
    missing = credentials.missing_secrets()
    assert credentials.CLIENT_SECRET not in missing
    assert credentials.REFRESH_TOKEN in missing


def test_secrets_status_reports_each_key():
    credentials.set_secret(credentials.CLIENT_SECRET, "x")
    status = credentials.secrets_status()
    assert status[credentials.CLIENT_SECRET] is True
    assert status[credentials.REFRESH_TOKEN] is False


def test_delete_secret_removes_value():
    credentials.set_secret(credentials.CLIENT_SECRET, "x")
    credentials.delete_secret(credentials.CLIENT_SECRET)
    assert credentials.get_secret(credentials.CLIENT_SECRET) is None


def test_get_secret_translates_no_keyring_error(monkeypatch):
    def _raise(service, entry):
        raise keyring.errors.NoKeyringError("no backend")
    monkeypatch.setattr(credentials.keyring, "get_password", _raise)
    with pytest.raises(credentials.CredentialError):
        credentials.get_secret(credentials.CLIENT_SECRET)
