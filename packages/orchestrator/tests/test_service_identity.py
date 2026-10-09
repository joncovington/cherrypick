"""Fixes from the first real service start (2026-10-08): a running service supervisor read as dead,
a refused `sc start` read as "would not start", and a momentary file lock killed a supervisor."""

from __future__ import annotations

import sys

import pytest
from cherrypick.core import looplock

from cherrypick import cli
from cherrypick.orchestrator import util, winservice


class FakeKernel32:
    def __init__(self, handle):
        self.handle = handle

    def OpenProcess(self, *a):  # noqa: N802 -- the Win32 name
        return self.handle

    def CloseHandle(self, h):  # noqa: N802
        return 1


@pytest.mark.parametrize("handle,error,alive", [(7, 0, True), (0, 5, True), (0, 87, False)])
def test_access_denied_is_alive_and_only_no_such_process_is_dead(monkeypatch, handle, error, alive):
    import ctypes

    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr(looplock.os, "name", "nt")
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kw: FakeKernel32(handle), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error, raising=False)
    assert looplock.pid_alive(4242) is alive


def test_a_refused_start_of_a_running_service_is_not_a_failure(monkeypatch):
    spawned = []
    monkeypatch.setattr(cli, "_spawn_supervisor_detached", lambda: spawned.append(1) or True)
    monkeypatch.setattr(cli.os, "name", "nt")
    monkeypatch.setattr(winservice, "query", lambda sid: {"installed": True, "state": "RUNNING"})
    monkeypatch.setattr(winservice, "start", lambda sid: False)  # Access is denied
    assert cli._start_supervisor({"service": {"enabled": True}}) and spawned == []


def test_a_replace_held_for_a_moment_is_retried_and_one_held_for_good_still_raises(monkeypatch, tmp_path):
    calls = {"n": 0}
    real = util.os.replace

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(5, "Access is denied")
        real(src, dst)

    monkeypatch.setattr(util.os, "replace", flaky)
    monkeypatch.setattr(util, "_REPLACE_PAUSE_S", 0)
    util.atomic_write_json(tmp_path / "x.json", {"a": 1})
    assert calls["n"] == 3 and (tmp_path / "x.json").read_text(encoding="utf-8").strip().startswith("{")

    def held(src, dst):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(util.os, "replace", held)
    with pytest.raises(PermissionError):
        util.atomic_write_json(tmp_path / "y.json", {"a": 1})


def test_a_restart_request_exits_with_the_restart_code(monkeypatch):
    # 2026-10-09: the service restarts its program only after a FAILURE, so `--restart` must not be a
    # clean exit -- or the service stays stopped and only an administrator can start it again.
    from cherrypick.orchestrator import supervisor

    class Fake:
        def __init__(self, cfg):
            self._loop_seq = 0

        def adopt_prior_state(self):
            pass

        def pass_once(self):
            supervisor.request_stop(restart=True)

    monkeypatch.setenv("CHERRYPICK_SUPERVISOR_NO_LOCK", "1")
    monkeypatch.setattr(supervisor, "Supervisor", Fake)
    monkeypatch.setattr(supervisor.time, "sleep", lambda s: None)
    assert supervisor.run(max_passes=5).get("restart") is True
    monkeypatch.setattr(supervisor, "run", lambda: {"ok": True, "restart": True})
    with pytest.raises(SystemExit) as exc:
        cli.cmd_supervise({})
    assert exc.value.code == supervisor.RESTART_EXIT


def test_the_anchor_lets_a_stopped_service_restart_itself_before_falling_back(monkeypatch):
    spawned = []
    monkeypatch.setattr(cli, "_spawn_supervisor_detached", lambda: spawned.append(1) or True)
    monkeypatch.setattr(cli, "Notifier", lambda c: type("N", (), {"notify": lambda *a, **k: {}})())
    monkeypatch.setattr(cli.os, "name", "nt")
    monkeypatch.setattr(winservice, "query", lambda sid: {"installed": True, "state": "STOPPED"})
    monkeypatch.setattr(winservice, "start", lambda sid: False)
    cfg, state = {"service": {"enabled": True}}, {}
    assert cli._start_supervisor(cfg, state) is False and spawned == []  # inside the grace: wait
    state["service_down_since"] -= cli.SERVICE_RESTART_GRACE_S + 1
    assert cli._start_supervisor(cfg, state) is True and spawned == [1]  # past it: fall back
