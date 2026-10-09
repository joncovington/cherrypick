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
