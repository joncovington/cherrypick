"""Tests for the notification log floor — the walk-away guarantee's minimum.

The floor line must be written before any push channel is attempted, and a push-channel failure
must neither suppress the floor nor propagate to the caller.
"""

import json

import pytest

import notify.notifier as notifier_mod
import notify.secrets as secrets_mod
from notify.notifier import Notifier

pytestmark = pytest.mark.unit


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

    def fake_post(url, payload):
        captured["url"], captured["payload"] = url, payload
        return {"ok": True, "status": 204}

    monkeypatch.setattr(secrets_mod, "get_webhook",
                        lambda ch: "https://discord.example/webhook/abc" if ch == "discord" else None)
    monkeypatch.setattr(Notifier, "_post_json", staticmethod(fake_post))
    res = Notifier({"channels": ["discord"]}).notify("CRITICAL", "meic.task", "Task missing", "not registered")
    assert res["discord"]["ok"] is True
    assert captured["url"].endswith("/abc")
    assert "content" in captured["payload"]              # Discord uses `content`, not `text`
    assert "CRITICAL" in captured["payload"]["content"]
