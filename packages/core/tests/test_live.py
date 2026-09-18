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
