"""Notification delivery told the truth only to the log (item 6 of the 2026-10-08 audit): a broken
log file silenced every push, every caller stamped "notified" whatever happened, and nothing watched
delivery itself."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from cherrypick.notify import delivered, notifier
from cherrypick.notify.secrets import NOT_SET, SET, UNAVAILABLE
from cherrypick.orchestrator import watchdog as wd


def test_delivered_reads_the_pushes_and_falls_back_to_the_floor_when_there_are_none():
    assert delivered({"log": {"ok": True}, "discord": {"ok": False, "error": "503"}, "desktop": {"ok": True}})
    assert not delivered({"log": {"ok": True}, "discord": {"ok": False, "skipped": "webhook not set"}})
    assert delivered({"log": {"ok": True}})
    assert not delivered({"log": {"ok": False, "error": "disk full"}})


def test_a_broken_log_floor_no_longer_blocks_the_pushes(monkeypatch):
    def full(*a, **k):
        raise OSError(28, "No space left on device")

    pushed = []
    monkeypatch.setattr(notifier.Notifier, "_write_log", full)
    monkeypatch.setattr(notifier.Notifier, "_push_desktop", lambda self, *a: pushed.append(a) or {"ok": True})
    out = notifier.Notifier({"channels": ["log", "desktop"]}).notify("WARN", "k", "t", "m")
    assert pushed and out["log"]["ok"] is False and "No space" in out["log"]["error"] and delivered(out)


class Fake:
    def __init__(self, ok):
        self.ok, self.sent = ok, []

    def notify(self, level, key, title, message, **kw):
        self.sent.append((level, key, title))
        return {"log": {"ok": True}, "discord": {"ok": self.ok}}


@pytest.fixture
def state(monkeypatch):
    box = {"s": {}}
    monkeypatch.setattr(wd, "_load_state", lambda: dict(box["s"]))
    monkeypatch.setattr(wd, "_save_state", lambda s: box.__setitem__("s", dict(s)))
    return box


def test_an_undelivered_alert_is_tried_again_next_tick_and_a_delivered_one_waits(state):
    f = wd.Finding("x", wd.CRITICAL, "t", "m")
    now = datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)
    down = Fake(ok=False)
    wd._process_notifications([f], down, 60, now)
    wd._process_notifications([f], down, 60, now)
    assert len(down.sent) == 2 and state["s"]["x"]["last_notified"] is None
    up = Fake(ok=True)
    wd._process_notifications([f], up, 60, now)
    wd._process_notifications([f], up, 60, now)
    assert len(up.sent) == 1 and state["s"]["x"]["last_notified"]


def test_an_undelivered_recovery_is_announced_again(state):
    state["s"] = {"x": {"status": wd.WARN, "last_notified": "2026-10-08T14:00:00+00:00"}}
    ok = wd.Finding("x", wd.OK, "t", "m")
    down = Fake(ok=False)
    wd._process_notifications([ok], down, 60)
    assert "x" in state["s"]
    wd._process_notifications([ok], Fake(ok=True), 60)
    assert "x" not in state["s"]


def test_delivery_findings_name_a_missing_webhook_and_a_channel_that_keeps_failing():
    failing = [{"channel": "discord", "ok": False, "status": 403, "error": "Forbidden"} for _ in range(3)]
    keys = {
        f.key
        for f in wd._delivery_findings(
            ["log", "discord", "slack"], failing, lambda ch: SET if ch == "discord" else NOT_SET
        )
    }
    assert keys == {"notify.slack_webhook", "notify.discord_failing"}
    mixed = failing + [{"channel": "discord", "ok": True}]
    assert wd._delivery_findings(["discord"], mixed, lambda ch: SET) == []
    assert wd._delivery_findings(["discord"], failing[:2], lambda ch: SET) == []


def test_delivery_findings_cover_telegram_too():
    """Telegram rides the same delivery health as the webhook channels: configured-but-unset is
    named (every push skipped), and a window of failures is named (a dead bot must not read as a
    quiet suite)."""
    failing = [{"channel": "telegram", "ok": False, "status": 401, "error": "Unauthorized"} for _ in range(3)]
    keys = {f.key for f in wd._delivery_findings(["log", "telegram"], failing, lambda ch: NOT_SET)}
    assert keys == {"notify.telegram_unconfigured", "notify.telegram_failing"}
    # Configured: the not-configured finding drops, the failing one stays.
    keys = {f.key for f in wd._delivery_findings(["log", "telegram"], failing, lambda ch: SET)}
    assert keys == {"notify.telegram_failing"}
    # One success in the window: the failing one drops too.
    mixed = failing + [{"channel": "telegram", "ok": True}]
    assert wd._delivery_findings(["telegram"], mixed, lambda ch: SET) == []


def test_a_keyring_outage_is_not_reported_as_a_missing_secret():
    """Re-entering a secret that is fine is the wrong fix for an unavailable keyring."""
    (f,) = wd._delivery_findings(["telegram"], [], lambda ch: UNAVAILABLE)
    assert f.key == "notify.telegram_unconfigured"
    assert "keyring is unavailable" in f.title and "Nothing needs re-entering" in f.message
    assert "secrets-set" not in f.message


def test_a_channel_named_only_in_trade_channels_is_still_checked(monkeypatch):
    """Fill pushes go to `notify.trade_channels`; a telegram there with no token skipped every fill
    with no finding while only `notify.channels` was read (verified: reading only `channels` makes
    this fail with no findings)."""
    from cherrypick.notify import secrets

    monkeypatch.setattr(notifier, "read_outbound", lambda since=None: [])
    monkeypatch.setattr(secrets, "state", lambda ch: NOT_SET)
    cfg = {"notify": {"channels": ["log"], "trade_channels": ["log", "telegram"]}}
    assert [f.key for f in wd._check_notify_delivery(cfg)] == ["notify.telegram_unconfigured"]


def test_init_knows_every_push_channel():
    """A correct telegram config must not be warned about as an unknown (typo'd) channel."""
    from cherrypick.notify.secrets import PUSH_CHANNELS
    from cherrypick.orchestrator import init

    assert set(PUSH_CHANNELS) <= init.KNOWN_CHANNELS
    issues = init.validate_config({"notify": {"channels": ["log", "telegram"]}, "modules": {}})
    assert not any("unknown notify channel" in msg for _lvl, msg in issues)


def test_every_push_channel_is_checked_from_the_one_tuple(monkeypatch):
    """The watchdog reads PUSH_CHANNELS, so a channel added there is covered with no edit here."""
    from cherrypick.notify import secrets

    monkeypatch.setattr(secrets, "PUSH_CHANNELS", secrets.PUSH_CHANNELS + ("pager",))
    (f,) = wd._delivery_findings(["pager"], [], lambda ch: NOT_SET)
    assert f.key == "notify.pager_webhook"
