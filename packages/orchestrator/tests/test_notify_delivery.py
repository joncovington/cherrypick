"""Notification delivery told the truth only to the log (item 6 of the 2026-10-08 audit): a broken
log file silenced every push, every caller stamped "notified" whatever happened, and nothing watched
delivery itself."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from cherrypick.notify import delivered, notifier
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
        f.key for f in wd._delivery_findings(["log", "discord", "slack"], failing, lambda ch: ch == "discord")
    }
    assert keys == {"notify.slack_webhook", "notify.discord_failing"}
    mixed = failing + [{"channel": "discord", "ok": True}]
    assert wd._delivery_findings(["discord"], mixed, lambda ch: True) == []
    assert wd._delivery_findings(["discord"], failing[:2], lambda ch: True) == []
