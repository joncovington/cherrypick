"""Single-writer guard for the spot-trail recorder.

The 2026-07-23 incident: two writers filled the trail with a frozen value — the standalone `run.py
record` daemon AND the dashboard's own record loop, which bypassed the pid guard entirely. These tests
pin the shared lock (exactly one holder records) and that stop_recorder confirms the process is dead
before clearing the pid file (so a wedged process can't let the next start spawn a duplicate).
"""

import os

import service


def _cfg(tmp_path):
    return {"history_db_path": str(tmp_path / "gex_history.db")}


def test_lock_is_single_writer(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    assert service.acquire_recorder_lock(cfg) is True          # nobody holds it -> we do
    assert service.acquire_recorder_lock(cfg) is True          # re-entrant for the same process
    assert service._recorder_pid_file(cfg).read_text().strip() == str(os.getpid())

    # A different, LIVE process holds it -> we must not acquire.
    other = 987654
    monkeypatch.setattr(service, "_pid_alive", lambda p: p in (other, os.getpid()))
    service._recorder_pid_file(cfg).write_text(str(other))
    assert service.acquire_recorder_lock(cfg) is False

    # Release never deletes another process's file.
    service.release_recorder_lock(cfg)
    assert service._recorder_pid_file(cfg).read_text().strip() == str(other)


def test_release_only_drops_our_own_lock(tmp_path):
    cfg = _cfg(tmp_path)
    service.acquire_recorder_lock(cfg)
    assert service._recorder_pid_file(cfg).exists()
    service.release_recorder_lock(cfg)
    assert not service._recorder_pid_file(cfg).exists()


def test_stop_recorder_confirms_death_before_clearing_pidfile(tmp_path, monkeypatch):
    """A process that ignores SIGTERM (common on Windows) must be force-killed, and the pid file cleared
    only after — never unlinked while the process still lives (which enabled the duplicate)."""
    cfg = _cfg(tmp_path)
    pid = 424242
    service._recorder_pid_file(cfg).write_text(str(pid))
    monkeypatch.setattr(service, "_pid_alive", lambda p: True)   # never dies on SIGTERM
    monkeypatch.setattr("os.kill", lambda p, s: None)           # SIGTERM is a no-op here
    monkeypatch.setattr("time.sleep", lambda s: None)           # don't actually wait the ~5s
    forced = {"pid": None}
    monkeypatch.setattr(service, "_force_kill", lambda p: forced.__setitem__("pid", p))

    res = service.stop_recorder(cfg)
    assert forced["pid"] == pid                                  # escalated to a force-kill
    assert res["signal"] == "SIGKILL"
    assert not service._recorder_pid_file(cfg).exists()          # cleared only after the kill


def test_stop_recorder_when_not_running(tmp_path):
    cfg = _cfg(tmp_path)
    assert service.stop_recorder(cfg) == {"ok": True, "running": False, "detail": "not running"}
