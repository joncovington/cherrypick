"""Arming and disarming via the arm record (the supervisor re-key of the live loop's per-day arm).

The record in the shared state dir is the armed signal three readers share (this module's
self-disarm, the orchestrator supervisor's job enablement, the watchdog's dead-man backstop).
These pin: record-only arming under a live supervisor (no schtasks), the legacy-stamp migration
read, disarm removing every trace, and `should_disarm` semantics surviving the relocation.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import pytest
from cherrypick.core import home as _home

from cherrypick.flies import live_loop as ll


@pytest.fixture
def fresh_supervisor_heartbeat(managed_home):
    state = _home.state_dir()
    state.mkdir(parents=True, exist_ok=True)
    (state / "supervisor.last.json").write_text(
        json.dumps({"ts": datetime.now(UTC).isoformat(), "pid": os.getpid()}),
        encoding="utf-8",
    )
    return state


@pytest.fixture
def no_schtasks(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("schtasks/subprocess.run called while the supervisor is driving")

    monkeypatch.setattr(ll.subprocess, "run", boom)


def test_arm_record_lives_in_shared_state_dir(managed_home):
    assert ll.arm_stamp_path() == str(_home.state_dir() / "flies-live-arm.json")


def test_arm_under_supervisor_writes_record_and_never_touches_schtasks(
    fresh_supervisor_heartbeat, no_schtasks, monkeypatch
):
    spawned = []
    monkeypatch.setattr(ll.subprocess, "Popen", lambda argv, **kw: spawned.append(argv) or None)
    out = ll.install_task()
    assert out["ok"] and out["driver"] == "supervisor"
    rec = json.loads(open(ll.arm_stamp_path(), encoding="utf-8").read())
    assert rec["date"] == out["armed_for"]
    assert rec["armed_by"] == "live-flies-start" and rec["confirmation"] == "literal-YES"
    # the immediate first tick is preserved (detached spawn, not a schtasks /Run)
    assert spawned and spawned[0][-2:] == ["--once", "--live"]


def test_disarm_removes_record_and_legacy_stamp(fresh_supervisor_heartbeat, monkeypatch):
    monkeypatch.setattr(ll.subprocess, "Popen", lambda argv, **kw: None)
    monkeypatch.setattr(ll, "task_installed", lambda: False)
    ll.install_task()
    legacy = ll._legacy_arm_stamp_path()
    os.makedirs(os.path.dirname(legacy), exist_ok=True)
    with open(legacy, "w", encoding="utf-8") as f:
        json.dump({"date": "2026-01-01"}, f)
    out = ll.uninstall_task()
    assert out["ok"] and out["arm_record_removed"]
    assert not os.path.exists(ll.arm_stamp_path())
    assert not os.path.exists(legacy)
    assert ll.arm_stamp_date() is None


def test_disarm_with_nothing_armed_is_honest(managed_home, monkeypatch):
    monkeypatch.setattr(ll, "task_installed", lambda: False)
    out = ll.uninstall_task()
    assert not out["arm_record_removed"] and "nothing was armed" in out["detail"]


def test_arm_stamp_date_falls_back_to_legacy_path(managed_home):
    """A box armed pre-cutover (stamp in data/flies) reads correctly for one transition window."""
    legacy = ll._legacy_arm_stamp_path()
    os.makedirs(os.path.dirname(legacy), exist_ok=True)
    with open(legacy, "w", encoding="utf-8") as f:
        json.dump({"date": "2026-08-07"}, f)
    assert ll.arm_stamp_date() == "2026-08-07"


def test_should_disarm_reads_the_relocated_record(fresh_supervisor_heartbeat, no_schtasks, monkeypatch):
    monkeypatch.setattr(ll.subprocess, "Popen", lambda argv, **kw: None)
    ll.install_task()
    today = ll.provider.now_et().date().isoformat()
    config = {"live": {"disarm_time": "17:00"}}
    assert ll.should_disarm(config, now_min=11 * 60, today=today) is None
    assert "per-day" in ll.should_disarm(config, now_min=11 * 60, today="2099-01-01")
    assert "past disarm" in ll.should_disarm(config, now_min=17 * 60 + 1, today=today)


def test_no_supervisor_and_not_windows_refuses_cleanly(managed_home, monkeypatch):
    monkeypatch.setattr(ll, "_supervisor_heartbeat_fresh", lambda **kw: False)
    monkeypatch.setattr(ll.os, "name", "posix")
    out = ll.install_task()
    assert not out["ok"] and "no supervisor running" in out["error"]
