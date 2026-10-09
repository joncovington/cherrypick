"""Tests for the notification log floor — the walk-away guarantee's minimum.

The floor line must be written before any push channel is attempted, and a push-channel failure
must neither suppress the floor nor propagate to the caller.
"""

import json

import keyring.errors
import pytest

import cherrypick.notify.notifier as notifier_mod
import cherrypick.notify.secrets as secrets_mod
from cherrypick.notify.notifier import Notifier

pytestmark = pytest.mark.unit


class _FakeKeyring:
    """Dict-backed stand-in for the keyring calls the secrets module makes."""

    def __init__(self):
        self.store = {}

    def get(self, _service, entry):
        return self.store.get(entry)

    def set(self, _service, entry, value):
        self.store[entry] = value

    def delete(self, _service, entry):
        if entry not in self.store:
            raise keyring.errors.PasswordDeleteError(entry)
        del self.store[entry]


@pytest.fixture
def fake_keyring(monkeypatch):
    fake = _FakeKeyring()
    monkeypatch.setattr(secrets_mod.keyring, "get_password", fake.get)
    monkeypatch.setattr(secrets_mod.keyring, "set_password", fake.set)
    monkeypatch.setattr(secrets_mod.keyring, "delete_password", fake.delete)
    return fake


@pytest.fixture
def temp_floor(tmp_path, monkeypatch):
    log_path = tmp_path / "notify.log"
    monkeypatch.setattr(notifier_mod, "_LOG", log_path)
    return log_path


def test_floor_written_for_log_channel(temp_floor):
    Notifier({"channels": ["log"]}).notify("WARN", "k", "Title", "Body")
    lines = temp_floor.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["kind"] == "NOTIFY" and rec["level"] == "WARN" and rec["key"] == "k"


def test_push_channel_failure_does_not_break_or_suppress_floor(temp_floor, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("slack exploded")

    monkeypatch.setattr(Notifier, "_push_slack", boom)
    # Should not raise despite the channel blowing up.
    res = Notifier({"channels": ["log", "slack"]}).notify("CRITICAL", "k", "T", "B")
    assert res["slack"]["ok"] is False
    # Floor was still written.
    rec = json.loads(temp_floor.read_text(encoding="utf-8").strip())
    assert rec["level"] == "CRITICAL"


def test_unknown_channel_is_reported_not_fatal(temp_floor):
    res = Notifier({"channels": ["log", "carrier-pigeon"]}).notify("INFO", "k", "T", "B")
    assert res["carrier-pigeon"]["ok"] is False
    assert temp_floor.exists()


def test_discord_skips_when_webhook_unset(temp_floor, monkeypatch):
    monkeypatch.setattr(secrets_mod, "get_webhook", lambda ch: None)
    res = Notifier({"channels": ["log", "discord"]}).notify("WARN", "k", "T", "B")
    assert res["discord"]["ok"] is False and "skipped" in res["discord"]
    assert temp_floor.exists()  # floor still written


def test_discord_posts_content_payload_from_keyring(temp_floor, monkeypatch):
    captured = {}

    def fake_post(url, payload, **record):
        captured["url"], captured["payload"] = url, payload
        return {"ok": True, "status": 204}

    monkeypatch.setattr(
        secrets_mod,
        "get_webhook",
        lambda ch: "https://discord.example/webhook/abc" if ch == "discord" else None,
    )
    monkeypatch.setattr(Notifier, "_post_json", staticmethod(fake_post))
    res = Notifier({"channels": ["discord"]}).notify(
        "CRITICAL", "meic.task", "Task missing", "not registered"
    )
    assert res["discord"]["ok"] is True
    assert captured["url"].endswith("/abc")
    assert "content" in captured["payload"]  # Discord uses `content`, not `text`
    assert "CRITICAL" in captured["payload"]["content"]


def test_discord_embed_needs_no_content_beside_it(temp_floor, monkeypatch):
    """An embed carries its own author/title/fields; the [LEVEL] prefix a plain message gets is
    dropped, and nothing else rides the payload — the exact bytes every caller relies on."""
    captured = {}
    monkeypatch.setattr(secrets_mod, "get_webhook", lambda ch: "https://discord.example/webhook/abc")
    monkeypatch.setattr(
        Notifier,
        "_post_json",
        staticmethod(lambda url, payload, **record: captured.update(payload) or {"ok": True, "status": 204}),
    )
    n = Notifier({"channels": ["discord"]})
    n.notify("INFO", "k", "T", "B", embed={"title": "card"})
    assert captured == {"embeds": [{"title": "card"}]}


def test_telegram_set_means_both_entries_stored(fake_keyring):
    secrets_mod.set_telegram("123:ABC", "456")
    assert fake_keyring.store == {"telegram_token": "123:ABC", "telegram_chat_id": "456"}
    assert secrets_mod.is_set("telegram") is True
    assert secrets_mod.status(["telegram"]) == {"telegram": "set"}


def test_telegram_is_set_needs_both_entries(fake_keyring):
    secrets_mod.set_telegram("123:ABC", "456")
    del fake_keyring.store["telegram_chat_id"]
    assert secrets_mod.is_set("telegram") is False


def test_deleting_telegram_removes_both_entries(fake_keyring):
    secrets_mod.set_telegram("123:ABC", "456")
    assert secrets_mod.delete_webhook("telegram") is True
    assert fake_keyring.store == {}
    assert secrets_mod.is_set("telegram") is False


def test_a_url_is_never_stored_as_a_telegram_webhook(fake_keyring):
    with pytest.raises(ValueError):
        secrets_mod.set_webhook("telegram", "https://example/tg")
    assert "telegram_webhook" not in fake_keyring.store


def test_telegram_skips_when_token_or_chat_id_unset(temp_floor, monkeypatch):
    monkeypatch.setattr(secrets_mod, "get_telegram_token", lambda: None)
    monkeypatch.setattr(secrets_mod, "get_telegram_chat_id", lambda: None)
    res = Notifier({"channels": ["log", "telegram"]}).notify("WARN", "k", "T", "B")
    assert res["telegram"]["ok"] is False and "skipped" in res["telegram"]
    assert temp_floor.exists()


def test_telegram_posts_html_payload_to_bot_api(temp_floor, monkeypatch):
    captured = {}

    def fake_post(url, payload, **record):
        captured["url"], captured["payload"] = url, payload
        return {"ok": True, "status": 200}

    monkeypatch.setattr(secrets_mod, "get_telegram_token", lambda: "123:abc")
    monkeypatch.setattr(secrets_mod, "get_telegram_chat_id", lambda: "456")
    monkeypatch.setattr(Notifier, "_post_json", staticmethod(fake_post))
    res = Notifier({"channels": ["telegram"]}).notify(
        "CRITICAL", "meic.task", "Task missing", "not registered"
    )
    assert res["telegram"]["ok"] is True
    assert "bot123:abc" in captured["url"]
    assert "sendMessage" in captured["url"]
    assert captured["payload"]["chat_id"] == "456"
    assert "CRITICAL" in captured["payload"]["text"]
    assert "parse_mode" in captured["payload"]
