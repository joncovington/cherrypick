"""secretsops: the settings surface's keyring operations.

The load-bearing claims: no stored secret value ever appears in any returned structure (only
booleans, set/not-set strings, and masked accounts); writes land in the named service's own
namespace (never shared, never legacy); the service and key whitelists refuse anything not derived
from config.
"""

from __future__ import annotations

import json

import pytest

from cherrypick.orchestrator import secretsops

pytestmark = pytest.mark.unit

SECRET_VALUE = "sekret-oauth-token-xyz-123"
ACCOUNT = "5WU12349876"


class _FakeStore:
    """In-memory CredentialStore stand-in with the real read-through-legacy semantics."""

    _mem: dict = {}

    def __init__(self, service, legacy_service_names=(), prefix="production"):
        self.service_name = service
        self.legacy_service_names = tuple(legacy_service_names)
        _FakeStore._mem.setdefault(service, {})

    def get_secret(self, key):
        value = _FakeStore._mem[self.service_name].get(key)
        if value is not None:
            return value
        for legacy in self.legacy_service_names:
            value = _FakeStore._mem.get(legacy, {}).get(key)
            if value is not None:
                return value
        return None

    def set_secret(self, key, value):
        _FakeStore._mem[self.service_name][key] = value

    def delete_secret(self, key):
        _FakeStore._mem[self.service_name].pop(key, None)

    def secrets_status(self):
        from cherrypick.core.auth import ALL_SECRETS

        return {k: bool(self.get_secret(k)) for k in ALL_SECRETS}


@pytest.fixture
def env(monkeypatch):
    _FakeStore._mem = {}
    webhooks: dict[str, str] = {}
    # The double mirrors the real status()'s channel default (push channels + dedicated), which is
    # what the settings page renders -- not WEBHOOKS, which is only the URL-shaped set.
    monkeypatch.setattr(
        secretsops.notify_secrets,
        "status",
        lambda channels=None: {
            ch: ("set" if ch in webhooks else "not set")
            for ch in (
                channels
                if channels is not None
                else secretsops.notify_secrets.PUSH_CHANNELS + secretsops.notify_secrets.DEDICATED
            )
        },
    )
    monkeypatch.setattr(secretsops.notify_secrets, "set_webhook", webhooks.__setitem__)
    monkeypatch.setattr(secretsops.notify_secrets, "delete_webhook", lambda ch: webhooks.pop(ch, None))
    monkeypatch.setattr(
        secretsops.accounts, "onboarding_status", lambda cfg, store_factory=None: {"ok": True, "modules": []}
    )
    cfg = {
        "modules": {
            "flies": {"enabled": True, "keyring_service": "fliesagent-test"},
            "gex": {"enabled": True},  # no keyring service — not a writable target
        }
    }
    return cfg, webhooks


def test_status_contains_no_secret_values(env):
    cfg, _ = env
    _FakeStore._mem = {
        "cherrypick-broker": {"client_secret": SECRET_VALUE, "refresh_token": SECRET_VALUE},
        "fliesagent-test": {"account_number": ACCOUNT},
    }
    out = secretsops.status(cfg, store_factory=_FakeStore)
    blob = json.dumps(out)
    assert out["ok"] is True
    assert SECRET_VALUE not in blob and ACCOUNT not in blob
    assert out["services"]["cherrypick-broker"]["status"]["client_secret"] is True
    assert out["services"]["fliesagent-test"]["account"] == "****9876"


def test_set_secret_writes_own_service_not_shared(env):
    cfg, _ = env
    out = secretsops.set_secret(cfg, "fliesagent-test", "refresh_token", SECRET_VALUE, _FakeStore)
    assert out["ok"] is True
    assert _FakeStore._mem["fliesagent-test"]["refresh_token"] == SECRET_VALUE
    assert "refresh_token" not in _FakeStore._mem.get("cherrypick-broker", {})
    assert SECRET_VALUE not in json.dumps(out)


def test_unknown_service_and_key_rejected(env):
    cfg, _ = env
    assert secretsops.set_secret(cfg, "tastytrade-mcp", "refresh_token", "x", _FakeStore)["ok"] is False
    assert secretsops.set_secret(cfg, "made-up", "refresh_token", "x", _FakeStore)["ok"] is False
    assert secretsops.set_secret(cfg, "fliesagent-test", "password", "x", _FakeStore)["ok"] is False
    assert secretsops.set_secret(cfg, "fliesagent-test", "refresh_token", "  ", _FakeStore)["ok"] is False


def test_account_number_masked_in_set_response(env):
    cfg, _ = env
    out = secretsops.set_secret(cfg, "fliesagent-test", "account_number", ACCOUNT, _FakeStore)
    assert out["ok"] is True and out["account"] == "****9876"
    assert ACCOUNT not in json.dumps(out)
    assert "cherrypick account" in out["hint"]


def test_delete_secret(env):
    cfg, _ = env
    secretsops.set_secret(cfg, "fliesagent-test", "refresh_token", SECRET_VALUE, _FakeStore)
    out = secretsops.delete_secret(cfg, "fliesagent-test", "refresh_token", _FakeStore)
    assert out["ok"] is True and out["status"]["refresh_token"] is False


def test_webhooks_set_delete_and_url_floor(env):
    cfg, webhooks = env
    assert secretsops.set_webhook("slack", "http://not-https")["ok"] is False
    out = secretsops.set_webhook("slack", "https://hooks.slack.com/services/T/B/x")
    assert out["ok"] is True and out["webhooks"]["slack"] == "set"
    assert "hooks.slack.com" not in json.dumps(out["webhooks"])
    assert secretsops.delete_webhook("slack")["webhooks"]["slack"] == "not set"
    assert secretsops.set_webhook("teams", "https://x")["ok"] is False


def test_the_settings_page_never_offers_a_telegram_url_field(env):
    """Telegram takes a bot token and a chat ID, not a URL: `status()` lists it (so the page shows
    whether it is configured), but `set_webhook` must refuse to store a URL under its name."""
    out = secretsops.set_webhook("telegram", "https://example/tg")
    assert out["ok"] is False and "telegram" in out["error"]
    assert out["webhooks"]["telegram"] == "not set"
    # And the settings page still renders a row per listed channel, including telegram's status.
    assert set(secretsops.status(env[0])["webhooks"]) == {"slack", "discord", "telegram", "discord_reporting"}


def test_delete_accepts_every_channel_the_status_page_lists(env):
    """Delete must reach anything the settings page can render a Delete button for -- telegram
    included (its two entries; the secrets layer routes it)."""
    for ch in secretsops.status(env[0])["webhooks"]:
        out = secretsops.delete_webhook(ch)
        assert out["ok"] is True, (ch, out)


def test_the_reporting_webhook_is_settable_but_never_a_push_channel(env):
    from cherrypick.notify import secrets
    from cherrypick.notify.notifier import Notifier

    out = secretsops.set_webhook("discord_reporting", "https://discord.com/api/webhooks/1/x")
    assert out["ok"] is True and out["webhooks"]["discord_reporting"] == "set"
    assert "discord_reporting" in secrets.WEBHOOKS and "discord_reporting" not in secrets.SUPPORTED
    # Listed as an alert channel by mistake, it receives nothing: the notifier skips the name.
    res = Notifier({"channels": ["discord_reporting"]}).notify("INFO", "test", "t", "m")
    assert res["discord_reporting"]["ok"] is False
