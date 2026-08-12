"""The watchdog-tick cost fixes, pinned: one schtasks spawn per task query, and rotation for the
orchestrator's own logs. (The no-git-subprocess and bounded-log-tail cases covered the static
dashboard render, which the tick no longer does — those helpers went with dashboard.py.)"""

import json
import subprocess

import pytest

from cherrypick.orchestrator import tasks, util

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------- query_verbose
def test_query_verbose_spawns_exactly_once(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)

        class _R:
            returncode = 0
            stdout = "Status: Ready\nLast Result: 0\n"

        return _R()

    monkeypatch.setattr(tasks, "_IS_WINDOWS", True)
    monkeypatch.setattr(subprocess, "run", fake_run)
    out = tasks.query_verbose("cherrypick-watchdog")
    assert out["exists"] is True and out["Status"] == "Ready"
    assert len(calls) == 1  # the old exists() pre-check made this 2


def test_query_verbose_missing_task_is_one_spawn_too(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)

        class _R:
            returncode = 1
            stdout = ""

        return _R()

    monkeypatch.setattr(tasks, "_IS_WINDOWS", True)
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert tasks.query_verbose("nope") == {"exists": False}
    assert len(calls) == 1


# --------------------------------------------------------------------- last_run_info
def test_last_run_info_parses_powershell_json(monkeypatch):
    def fake_run(cmd, **kw):
        class _R:
            returncode = 0
            stdout = json.dumps({"LastRunTime": "2026-07-31T09:52:01.0000000-06:00", "LastTaskResult": 0})

        return _R()

    monkeypatch.setattr(tasks, "_IS_WINDOWS", True)
    monkeypatch.setattr(subprocess, "run", fake_run)
    out = tasks.last_run_info("cherrypick-flies-live-loop")
    assert out == {"last_run_time": "2026-07-31T09:52:01.0000000-06:00", "last_task_result": 0}


def test_last_run_info_missing_task_returns_none(monkeypatch):
    def fake_run(cmd, **kw):
        class _R:
            returncode = 1
            stdout = ""

        return _R()

    monkeypatch.setattr(tasks, "_IS_WINDOWS", True)
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert tasks.last_run_info("nope") is None


def test_last_run_info_bad_json_returns_none(monkeypatch):
    def fake_run(cmd, **kw):
        class _R:
            returncode = 0
            stdout = "not json"

        return _R()

    monkeypatch.setattr(tasks, "_IS_WINDOWS", True)
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert tasks.last_run_info("cherrypick-flies-live-loop") is None


def test_last_run_info_none_on_posix(monkeypatch):
    monkeypatch.setattr(tasks, "_IS_WINDOWS", False)
    assert tasks.last_run_info("anything") is None


# --------------------------------------------------------------------- rotation
def test_rotate_if_large_shifts_backups_and_caps_them(tmp_path):
    log = tmp_path / "watchdog.log"
    log.write_text("x" * 100, encoding="utf-8")
    assert util.rotate_if_large(log, max_bytes=50, keep=2) is True
    assert not log.exists()
    assert (tmp_path / "watchdog.log.1").exists()
    # Second rotation: .1 -> .2, live -> .1; a third pushes the oldest off the end.
    log.write_text("y" * 100, encoding="utf-8")
    assert util.rotate_if_large(log, max_bytes=50, keep=2)
    assert (tmp_path / "watchdog.log.1").read_text() == "y" * 100
    assert (tmp_path / "watchdog.log.2").read_text() == "x" * 100
    log.write_text("z" * 100, encoding="utf-8")
    assert util.rotate_if_large(log, max_bytes=50, keep=2)
    assert (tmp_path / "watchdog.log.2").read_text() == "y" * 100
    assert not (tmp_path / "watchdog.log.3").exists()


def test_rotate_if_large_leaves_small_files_alone(tmp_path):
    log = tmp_path / "watchdog.log"
    log.write_text("tiny", encoding="utf-8")
    assert util.rotate_if_large(log, max_bytes=50) is False
    assert log.read_text() == "tiny"
