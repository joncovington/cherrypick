"""The watchdog-tick cost fixes, pinned: one schtasks spawn per task query, no git
subprocess per render, bounded log tails, and rotation for the orchestrator's own logs."""

import json
import subprocess

import pytest

from cherrypick.orchestrator import dashboard, tasks, util

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


# --------------------------------------------------------------------- _git_ref
def _fake_repo(tmp_path, sha="0123456789abcdef", packed=False):
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    if packed:
        (git / "packed-refs").write_text(
            f"# pack-refs with: peeled fully-peeled sorted\n{sha} refs/heads/main\n", encoding="utf-8"
        )
    else:
        (git / "refs" / "heads" / "main").write_text(sha + "\n", encoding="utf-8")
    return tmp_path


def test_git_ref_reads_loose_ref_without_subprocess(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("git subprocess spawned")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert dashboard._git_ref(_fake_repo(tmp_path)) == "0123456"


def test_git_ref_reads_packed_refs(tmp_path):
    assert dashboard._git_ref(_fake_repo(tmp_path, packed=True)) == "0123456"


def test_git_ref_none_for_non_repo(tmp_path):
    assert dashboard._git_ref(tmp_path) is None


# --------------------------------------------------------------------- _tail
def test_tail_reads_only_the_end_of_a_large_file(tmp_path):
    log = tmp_path / "big.log"
    with log.open("w", encoding="utf-8") as fh:
        for i in range(20_000):
            fh.write(json.dumps({"ts": i, "level": "INFO", "message": f"line {i}"}) + "\n")
    assert log.stat().st_size > dashboard._TAIL_READ_BYTES
    lines = dashboard._tail(log, 50)
    assert len(lines) == 50
    assert json.loads(lines[-1])["message"] == "line 19999"
    assert json.loads(lines[0])["message"] == "line 19950"


def test_tail_small_file_unchanged(tmp_path):
    log = tmp_path / "small.log"
    log.write_text("a\n\nb\nc\n", encoding="utf-8")
    assert dashboard._tail(log, 10) == ["a", "b", "c"]
    assert dashboard._tail(tmp_path / "missing.log", 10) == []


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
