"""The per-day arm record: what it is, what it means, and the two ways a tick disarms."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import pytest

from cherrypick.core import home as _home
from cherrypick.core import live


@pytest.fixture(autouse=True)
def _home_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    return tmp_path


def _fresh_heartbeat():
    state = _home.state_dir()
    state.mkdir(parents=True, exist_ok=True)
    (state / live.SUPERVISOR_HEARTBEAT).write_text(
        json.dumps({"ts": datetime.now(UTC).isoformat(), "pid": os.getpid()}), encoding="utf-8"
    )


def test_arm_record_path_is_the_shared_convention():
    assert live.arm_record_name("bwb") == "bwb-live-arm.json"
    assert live.arm_record_path("flies") == _home.state_dir() / "flies-live-arm.json"


def test_arm_writes_the_record_fires_one_tick_and_refuses_without_a_supervisor():
    spawned = []
    kw = dict(
        date="2026-09-18", at="t", armed_by="live-bwb-start", spawn_first_tick=lambda: spawned.append(1)
    )
    out = live.arm("bwb", **kw)
    assert not out["ok"] and "no supervisor" in out["error"]
    assert not live.arm_record_path("bwb").exists() and not spawned

    _fresh_heartbeat()
    out = live.arm("bwb", **kw)
    assert out["ok"] and out["driver"] == "supervisor" and out["armed_for"] == "2026-09-18"
    rec = json.loads(live.arm_record_path("bwb").read_text(encoding="utf-8"))
    assert rec == {
        "date": "2026-09-18",
        "at": "t",
        "armed_by": "live-bwb-start",
        "confirmation": "literal-YES",
    }
    assert spawned == [1]
    assert live.arm_record_date("bwb") == "2026-09-18"


def test_disarm_removes_the_record_and_any_legacy_copy_and_is_honest_when_nothing_was_armed(tmp_path):
    _fresh_heartbeat()
    live.arm("bwb", date="2026-09-18", at="t", armed_by="x")
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"date": "2026-01-01"}), encoding="utf-8")
    out = live.disarm("bwb", legacy_paths=[legacy])
    assert out["ok"] and out["arm_record_removed"]
    assert not live.arm_record_path("bwb").exists() and not legacy.exists()
    assert live.arm_record_date("bwb", legacy_paths=[legacy]) is None
    again = live.disarm("bwb")
    assert not again["ok"] and "nothing was armed" in again["detail"]


def test_every_arm_and_removing_disarm_is_journaled_with_its_actor_and_reason():
    _fresh_heartbeat()
    live.arm("ladder", date="2026-11-23", at="t1", armed_by="live-ladder-start")
    live.disarm("ladder", actor="disarm-command")
    live.arm("ladder", date="2026-11-23", at="t2", armed_by="live-ladder-start")
    live.disarm("ladder", actor="ladder-tick", reason="past_disarm_at")
    live.disarm("ladder")  # nothing armed: not an event
    live.arm("meic", date="2026-11-24", at="t3", armed_by="live-meic-start")
    rows = live.arm_events("ladder")
    assert [(r["event"], r["actor"]) for r in rows] == [
        ("arm", "live-ladder-start"),
        ("disarm", "disarm-command"),
        ("arm", "live-ladder-start"),
        ("disarm", "ladder-tick"),
    ]
    assert rows[1]["record_date"] == "2026-11-23" and rows[3]["reason"] == "past_disarm_at"
    assert all(r["at"] and r["module"] == "ladder" for r in rows)
    assert [r["module"] for r in live.arm_events()] == ["ladder"] * 4 + ["meic"]
    assert len(live.arm_events("ladder", date="2026-11-23")) == 4 and not live.arm_events(date="1999-01-01")


def test_disarm_slug_names_both_should_disarm_reasons():
    stale = live.should_disarm(
        "2026-11-20", today="2026-11-23", now_min=600, disarm_min=1200, disarm_label="20:00"
    )
    late = live.should_disarm(
        "2026-11-23", today="2026-11-23", now_min=1200, disarm_min=1200, disarm_label="20:00"
    )
    assert live.disarm_slug(stale) == "record_not_today"
    assert live.disarm_slug(late) == "past_disarm_time"


def test_a_refused_arm_is_not_journaled_and_an_unwritable_journal_never_stops_an_arm():
    assert not live.arm("bwb", date="2026-09-18", at="t", armed_by="x")["ok"]  # no supervisor
    assert live.arm_events() == []
    _fresh_heartbeat()
    live.arm_events_path().mkdir(parents=True)  # a directory where the file belongs: the append fails
    assert live.arm_events() == []
    assert live.arm("bwb", date="2026-09-18", at="t", armed_by="x")["ok"]
    assert live.disarm("bwb")["ok"]


def test_record_event_says_whether_it_was_written():
    """`record_event` is the journal's public writer: a caller that must not proceed without the record
    (a change of the arm live runs) checks the answer, where an arm or a disarm ignores it."""
    assert live.record_event("meic", "live_arm_changed", actor="config", from_arm="a", to_arm="b") is True
    rows = live.arm_events("meic")
    assert (
        rows[-1]["event"] == "live_arm_changed"
        and rows[-1]["to_arm"] == "b"
        and rows[-1]["actor"] == "config"
    )
    live.arm_events_path().unlink()
    live.arm_events_path().mkdir()  # a directory where the file belongs: the append fails
    assert live.record_event("meic", "live_arm_changed") is False


def test_arm_record_date_reads_a_legacy_location_read_only(tmp_path):
    legacy = tmp_path / "live_armed.json"
    legacy.write_text(json.dumps({"date": "2026-08-07"}), encoding="utf-8")
    assert live.arm_record_date("flies", legacy_paths=[legacy]) == "2026-08-07"


def test_should_disarm_has_exactly_two_reasons():
    kw = dict(today="2026-09-18", disarm_min=17 * 60, disarm_label="17:00")
    assert live.should_disarm("2026-09-18", now_min=11 * 60, **kw) is None
    assert "per-day" in live.should_disarm("2026-09-17", now_min=11 * 60, **kw)
    assert "per-day" in live.should_disarm(None, now_min=11 * 60, **kw)
    assert "past disarm time (17:00)" in live.should_disarm("2026-09-18", now_min=17 * 60, **kw)


def test_supervisor_heartbeat_freshness():
    assert not live.supervisor_heartbeat_fresh()
    _fresh_heartbeat()
    assert live.supervisor_heartbeat_fresh()
    state = _home.state_dir()
    stale = json.dumps({"ts": "2020-01-01T00:00:00+00:00"})
    (state / live.SUPERVISOR_HEARTBEAT).write_text(stale, encoding="utf-8")
    assert not live.supervisor_heartbeat_fresh()
    (state / live.SUPERVISOR_HEARTBEAT).write_text("not json", encoding="utf-8")
    assert not live.supervisor_heartbeat_fresh()


def test_arming_on_a_quarter_end_session_carries_the_warning():
    """Arming still succeeds on the last trading day of a quarter; the result says no new entries."""
    _fresh_heartbeat()
    out = live.arm("bwb", date="2026-12-31", at="t", armed_by="live-bwb-start")
    assert out["ok"] and "NO new entries" in out["warning"]
    out = live.arm("bwb", date="2026-12-30", at="t", armed_by="live-bwb-start")
    assert out["ok"] and "warning" not in out


def test_quarter_end_warning_is_the_calendar_rule():
    assert live.quarter_end_warning("2026-09-30") is not None
    assert live.quarter_end_warning("2026-09-29") is None
    assert live.quarter_end_warning("not a date") is None
