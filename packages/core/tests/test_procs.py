"""core.procs: child processes opted out of power throttling at their spawn, and the default pool size."""

from __future__ import annotations

import subprocess
import sys

import pytest

from cherrypick.core import procs


def test_default_workers_keeps_two_cores_free_and_never_drops_below_one(monkeypatch):
    monkeypatch.setattr(procs.os, "cpu_count", lambda: 16)
    assert procs.default_workers() == 14
    assert procs.default_workers(reserve=0) == 16
    monkeypatch.setattr(procs.os, "cpu_count", lambda: 2)
    assert procs.default_workers() == 1
    monkeypatch.setattr(procs.os, "cpu_count", lambda: None)
    assert procs.default_workers() == 1


def test_run_unthrottled_opts_the_child_out_and_returns_what_run_would(monkeypatch):
    seen = []
    monkeypatch.setattr(procs, "never_throttle_child", lambda proc: seen.append(proc.pid) or True)
    got = procs.run_unthrottled(
        [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert isinstance(got, subprocess.CompletedProcess)
    assert got.returncode == 3 and got.stdout.strip() == "out" and got.stderr.strip() == "err"
    assert len(seen) == 1 and seen[0] > 0


def test_run_unthrottled_kills_the_child_on_a_timeout(monkeypatch):
    monkeypatch.setattr(procs, "never_throttle_child", lambda proc: True)
    with pytest.raises(subprocess.TimeoutExpired):
        procs.run_unthrottled([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)


def test_a_child_without_a_handle_is_left_alone(monkeypatch):
    calls = []
    monkeypatch.setattr(procs, "never_throttle", lambda handle=None: calls.append(handle) or True)

    class _NoHandle:
        pid = 1

    assert procs.never_throttle_child(_NoHandle()) is False
    assert calls == []  # never `never_throttle(None)`, which would mean this process
