"""Dated reminders (orchestrator/reminders.py): what a row may read, when it pushes, and when it closes.

The allow-list is the safety property, as the research queue's: the supervisor runs this as a service.
The clock, the module status and the notifier are injected; no process runs and nothing is sent.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from cherrypick.orchestrator import reminders as rm

ET = rm.ET
OFF = {"kind": "module-disabled", "module": "curve"}
FLAT = {"kind": "module-status", "module": "curve", "field": "open_positions", "equals": 0}


def _at(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=ET)


class FakeNotifier:
    def __init__(self, lands: bool = True):
        self.lands, self.pushed, self.recorded = lands, [], []

    def notify(self, level, key, title, message, embed=None, **_):
        self.pushed.append((title, message))
        return {"log": {"ok": True}, "discord": {"ok": self.lands}}

    def record(self, level, key, title, message):
        self.recorded.append((title, message))
        return {"log": {"ok": True}}


@pytest.fixture
def db(tmp_path):
    return tmp_path / "reminders.db"


def _cfg(curve_on=True):
    return {"modules": {"curve": {"enabled": curve_on}}, "notify": {"channels": ["log", "discord"]}}


def _add(db, **kw):
    kw.setdefault("done_when", [OFF])
    return rm.add("switch curve off", "curve's spreads have settled", "2026-11-21T09:00", path=db, **kw)["id"]


def _tick(db, when, cfg=None, status=None, notifier=None):
    n = notifier or FakeNotifier()
    out = rm.run(cfg or _cfg(), now=_at(when), notifier=n, status=status or (lambda c, m: None), path=db)
    return out, n


def _row(db, rid):
    return next(r for r in rm.rows(db) if r["id"] == rid)


# ── the allow-list ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "conds",
    [
        [{"kind": "shell", "cmd": "del *"}],
        [{"kind": "module-disabled", "module": "../curve"}],
        [{"kind": "module-status", "module": "curve", "field": "open positions", "equals": 0}],
        [{"kind": "module-status", "module": "curve", "field": "open_positions"}],
        [{"kind": "module-status", "module": "curve", "field": "open_positions", "equals": [0]}],
        {"kind": "module-disabled", "module": "curve"},
    ],
)
def test_a_condition_outside_the_allow_list_is_refused(db, conds):
    with pytest.raises(rm.Refused):
        rm.add("t", "m", "2026-11-21T09:00", done_when=conds, path=db)
    with pytest.raises(rm.Refused):
        rm.add("t", "m", "2026-11-21T09:00", checks=conds, path=db)
    assert rm.rows(db) == []


@pytest.mark.parametrize(
    "title,message,due,hours",
    [
        ("", "m", "2026-11-21T09:00", None),
        ("t", "", "2026-11-21T09:00", None),
        ("t", "m", "2026-11-21 09:00", None),
        ("t", "m", "21/11/2026", None),
        ("t", "m", "2026-11-21T09:00", 0),
        ("x" * 121, "m", "2026-11-21T09:00", None),
    ],
)
def test_a_malformed_reminder_is_refused(db, title, message, due, hours):
    with pytest.raises(rm.Refused):
        rm.add(title, message, due, renotify_hours=hours, path=db)


# ── the conditions ───────────────────────────────────────────────────────────


def test_module_disabled_reads_config_and_counts_an_absent_module_as_off():
    assert rm.evaluate(OFF, _cfg(curve_on=True))[0] is False
    assert rm.evaluate(OFF, _cfg(curve_on=False))[0] is True
    assert rm.evaluate(OFF, {"modules": {}})[0] is True


def test_module_status_compares_the_field_and_an_unreadable_status_is_unknown():
    assert rm.evaluate(FLAT, {}, lambda c, m: {"open_positions": 4})[0] is False
    assert rm.evaluate(FLAT, {}, lambda c, m: {"open_positions": 0})[0] is True
    assert rm.evaluate(FLAT, {}, lambda c, m: None)[0] is None
    assert rm.evaluate(FLAT, {}, lambda c, m: {"other": 0})[0] is None


# ── the tick ─────────────────────────────────────────────────────────────────


def test_nothing_happens_before_the_due_time(db):
    rid = _add(db)
    out, n = _tick(db, "2026-11-21T08:59")
    assert n.pushed == [] and _row(db, rid)["status"] == "pending"


def test_an_unmet_reminder_pushes_once_then_again_only_after_renotify_hours(db):
    rid = _add(db, renotify_hours=24)
    _, n = _tick(db, "2026-11-21T09:00")
    assert len(n.pushed) == 1 and _row(db, rid)["status"] == "open"
    _, n = _tick(db, "2026-11-22T08:59")
    assert n.pushed == []
    _, n = _tick(db, "2026-11-22T09:00")
    assert len(n.pushed) == 1 and _row(db, rid)["notify_count"] == 2


def test_an_open_reminder_closes_with_one_done_push_when_its_condition_holds(db):
    rid = _add(db)
    _tick(db, "2026-11-21T09:00")
    _, n = _tick(db, "2026-11-21T09:15", cfg=_cfg(curve_on=False))
    assert [t for t, _ in n.pushed] == ["Done: switch curve off"]
    assert _row(db, rid)["status"] == "done"
    _, n = _tick(db, "2026-11-23T09:15", cfg=_cfg(curve_on=False))
    assert n.pushed == []


def test_work_done_before_the_due_time_closes_it_without_a_push(db):
    rid = _add(db)
    _, n = _tick(db, "2026-11-21T09:00", cfg=_cfg(curve_on=False))
    assert n.pushed == [] and len(n.recorded) == 1 and _row(db, rid)["status"] == "done"


def test_every_condition_must_hold_and_unknown_is_not_met(db):
    rid = _add(db, done_when=[OFF, FLAT])
    _, n = _tick(db, "2026-11-21T09:00", cfg=_cfg(curve_on=False), status=lambda c, m: None)
    assert len(n.pushed) == 1 and _row(db, rid)["status"] == "open"
    _, n = _tick(db, "2026-11-21T09:15", cfg=_cfg(curve_on=False), status=lambda c, m: {"open_positions": 0})
    assert _row(db, rid)["status"] == "done"


def test_a_plain_reminder_is_done_once_its_push_lands(db):
    rid = _add(db, done_when=[])
    _, n = _tick(db, "2026-11-21T09:00")
    assert len(n.pushed) == 1 and _row(db, rid)["status"] == "done"


def test_a_push_that_reaches_no_channel_changes_nothing(db):
    rid = _add(db)
    _, n = _tick(db, "2026-11-21T09:00", notifier=FakeNotifier(lands=False))
    assert len(n.pushed) == 1
    r = _row(db, rid)
    assert r["status"] == "pending" and r["notify_count"] == 0 and r["notified_at"] is None
    _, n = _tick(db, "2026-11-21T09:15")
    assert _row(db, rid)["status"] == "open"


def test_the_push_names_each_condition_as_read_and_how_to_close_it(db):
    rid = _add(db, checks=[FLAT])
    _, n = _tick(db, "2026-11-21T09:00", status=lambda c, m: {"open_positions": 4})
    title, message = n.pushed[0]
    assert title == "Reminder: switch curve off"
    assert "[not yet] curve is enabled in config" in message
    assert "[not yet] curve status open_positions = 4" in message
    assert f"remind-done --reminder-id {rid}" in message


# ── closing by hand ──────────────────────────────────────────────────────────


def test_done_and_cancel_close_a_live_row_once(db):
    a, b = _add(db), _add(db)
    assert rm.mark_done(a, "did it", path=db)["ok"] and _row(db, a)["note"] == "did it"
    assert rm.cancel(b, path=db)["ok"] and _row(db, b)["status"] == "cancelled"
    assert not rm.mark_done(a, path=db)["ok"] and not rm.cancel(b, path=db)["ok"]
    assert not rm.cancel(999, path=db)["ok"]
    _, n = _tick(db, "2026-11-25T09:00")
    assert n.pushed == []


def test_channels_default_to_the_suites_notify_channels():
    assert rm.settings({"notify": {"channels": ["log", "discord"]}})["channels"] == ["log", "discord"]
    assert rm.settings({"reminders": {"channels": ["log"]}, "notify": {"channels": ["discord"]}})[
        "channels"
    ] == ["log"]
    assert rm.settings({})["channels"] == ["log"]
