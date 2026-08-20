"""Tests for cherrypick.core.auth.credentials.CredentialStore."""

import keyring
import keyring.errors
import pytest

from cherrypick.core.auth import (
    prompt_and_store,
    ALL_SECRETS,
    CLIENT_SECRET,
    REFRESH_TOKEN,
    CredentialError,
    CredentialStore,
)


def test_set_get_roundtrip_uses_prefixed_entry(mem_keyring):
    store = CredentialStore("meicagent")
    store.set_secret(CLIENT_SECRET, "sekret")
    assert store.get_secret(CLIENT_SECRET) == "sekret"
    # Stored under the "production:" prefixed entry, scoped to the service name.
    assert mem_keyring[("meicagent", "production:client_secret")] == "sekret"


def test_missing_and_present(mem_keyring):
    store = CredentialStore("meicagent")
    assert store.secrets_present() is False
    assert set(store.missing_secrets()) == {CLIENT_SECRET, REFRESH_TOKEN}
    store.set_secret(CLIENT_SECRET, "a")
    store.set_secret(REFRESH_TOKEN, "b")
    assert store.secrets_present() is True
    assert store.missing_secrets() == []


def test_status_covers_all_secrets(mem_keyring):
    store = CredentialStore("earningsagent")
    status = store.secrets_status()
    assert set(status.keys()) == set(ALL_SECRETS)
    assert all(v is False for v in status.values())


def test_legacy_service_fallback_is_read_only(mem_keyring):
    # Secret exists only under the legacy service name.
    mem_keyring[("tastytrade-mcp", "production:refresh_token")] = "legacy-token"
    store = CredentialStore("meicagent", legacy_service_names=("tastytrade-mcp",))
    assert store.get_secret(REFRESH_TOKEN) == "legacy-token"

    # Writing goes to the primary service only; the legacy entry is never modified.
    store.set_secret(REFRESH_TOKEN, "new-token")
    assert mem_keyring[("meicagent", "production:refresh_token")] == "new-token"
    assert mem_keyring[("tastytrade-mcp", "production:refresh_token")] == "legacy-token"


def test_primary_takes_precedence_over_legacy(mem_keyring):
    mem_keyring[("meicagent", "production:client_secret")] = "primary"
    mem_keyring[("tastytrade-mcp", "production:client_secret")] = "legacy"
    store = CredentialStore("meicagent", legacy_service_names=("tastytrade-mcp",))
    assert store.get_secret(CLIENT_SECRET) == "primary"


def test_no_legacy_configured_returns_none(mem_keyring):
    store = CredentialStore("earningsagent")
    assert store.get_secret(CLIENT_SECRET) is None


def test_delete_is_idempotent(mem_keyring):
    store = CredentialStore("meicagent")
    store.set_secret(CLIENT_SECRET, "x")
    store.delete_secret(CLIENT_SECRET)
    store.delete_secret(CLIENT_SECRET)  # already absent -> no raise
    assert store.get_secret(CLIENT_SECRET) is None


def test_no_keyring_backend_raises_credential_error(monkeypatch):
    def boom(*_a, **_k):
        raise keyring.errors.NoKeyringError("no backend")

    monkeypatch.setattr(keyring, "get_password", boom)
    store = CredentialStore("meicagent")
    with pytest.raises(CredentialError):
        store.get_secret(CLIENT_SECRET)


# --------------------------------------------------------------------------- prompt_and_store
class _FakeStore:
    """Stands in for a CredentialStore. Nothing here touches a real keyring."""

    def __init__(self, existing=None):
        self.values = dict(existing or {})
        self.writes = []

    def set_secret(self, key, value):
        self.values[key] = value
        self.writes.append((key, value))


def test_prompt_and_store_writes_what_was_entered():
    store = _FakeStore()
    written = prompt_and_store(
        store, ["client_secret", "refresh_token"], prompt_fn=lambda _p: "abc"
    )
    assert written == ["client_secret", "refresh_token"]
    assert store.values == {"client_secret": "abc", "refresh_token": "abc"}


def test_blank_input_leaves_the_existing_value():
    """The rule this exists for. The prompt is hidden, so a stray Enter is indistinguishable from a
    typo — treating it as "" would erase a working credential and take a module offline at its next
    broker call."""
    store = _FakeStore({"client_secret": "already-set"})
    written = prompt_and_store(store, ["client_secret"], prompt_fn=lambda _p: "")
    assert written == []
    assert store.writes == [], "a blank entry must not reach the keyring at all"
    assert store.values["client_secret"] == "already-set"


def test_whitespace_only_input_is_also_blank():
    store = _FakeStore({"refresh_token": "keep"})
    assert prompt_and_store(store, ["refresh_token"], prompt_fn=lambda _p: "   \n") == []
    assert store.values["refresh_token"] == "keep"


def test_values_are_stripped():
    """A token pasted with a trailing newline is not a different token — but it fails at the broker
    with no clue why."""
    store = _FakeStore()
    prompt_and_store(store, ["client_secret"], prompt_fn=lambda _p: "  tok\n")
    assert store.values["client_secret"] == "tok"


def test_each_key_is_prompted_once_and_named():
    seen = []

    def prompt(text):
        seen.append(text)
        return ""

    prompt_and_store(_FakeStore(), ["client_secret", "refresh_token"], prompt_fn=prompt)
    assert len(seen) == 2
    assert "client_secret" in seen[0] and "refresh_token" in seen[1]
    assert "blank to keep current" in seen[0], "the prompt must state what a blank entry does"
