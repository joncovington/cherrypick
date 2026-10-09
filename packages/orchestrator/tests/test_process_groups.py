"""A tree kill on POSIX must never reach the supervisor's own process group (2026-10-08 OS audit).

Without psutil, `_terminate_tree` falls back to `killpg`. Children used to inherit the supervisor's
group, so stopping one job signalled them all -- one console restart took down the supervisor, the
streamer, Dolt and any live tick mid-order. Now every spawn leads its own session, and the fallback
signals a group only when the child leads it.
"""

from __future__ import annotations

import sys

import pytest

from cherrypick import cli
from cherrypick.orchestrator import supervisor, util, watchdog


@pytest.fixture
def posix_without_psutil(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setitem(sys.modules, "psutil", None)  # `import psutil` raises ImportError
    monkeypatch.setattr(supervisor.os, "name", "posix")
    monkeypatch.setattr(supervisor.os, "getpgrp", lambda: 500, raising=False)
    monkeypatch.setattr(supervisor.os, "killpg", lambda g, s: calls.append(("killpg", g)), raising=False)
    monkeypatch.setattr(supervisor.os, "kill", lambda p, s: calls.append(("kill", p)))
    return calls


def test_a_child_leading_its_own_group_is_killed_as_a_group(posix_without_psutil, monkeypatch):
    monkeypatch.setattr(supervisor.os, "getpgid", lambda pid: pid, raising=False)
    assert supervisor._terminate_tree(4242) is True
    assert posix_without_psutil == [("killpg", 4242)]


def test_a_child_in_the_supervisors_group_never_signals_that_group(posix_without_psutil, monkeypatch):
    # An adopted orphan from an older supervisor still shares this group: kill the child alone.
    monkeypatch.setattr(supervisor.os, "getpgid", lambda pid: 500, raising=False)
    assert supervisor._terminate_tree(4242) is True
    assert posix_without_psutil == [("kill", 4242)]


def _capture_popen(monkeypatch, module):
    seen: list[dict] = []

    class P:
        pid = 1

        def __init__(self, argv, **kw):
            seen.append(kw)

    monkeypatch.setattr(module.subprocess, "Popen", P)
    return seen


def test_every_detached_start_asks_for_its_own_session(monkeypatch, tmp_path):
    monkeypatch.setattr(util, "NEW_SESSION", True)
    for mod in (supervisor, cli, watchdog):
        monkeypatch.setattr(mod, "NEW_SESSION", True)

    seen = _capture_popen(monkeypatch, cli)
    assert cli._start_dolt(tmp_path) and cli._spawn_supervisor_detached()
    seen_wd = _capture_popen(monkeypatch, watchdog)
    assert watchdog._start_streamer(tmp_path, ["-m", "x"])
    assert [kw.get("start_new_session") for kw in seen + seen_wd] == [True, True, True]


def test_a_supervised_job_spawns_in_its_own_session(monkeypatch, tmp_path):
    from cherrypick.orchestrator import jobspec

    monkeypatch.setattr(supervisor, "NEW_SESSION", True)
    seen = _capture_popen(monkeypatch, supervisor)
    monkeypatch.setattr(
        supervisor.Supervisor, "_open_stderr", lambda self, spec, st: supervisor.subprocess.DEVNULL
    )
    monkeypatch.setattr(supervisor.Supervisor, "_record_start", lambda *a, **k: None, raising=False)
    spec = jobspec.JobSpec(id="j", argv=("py", "x"), kind=jobspec.KIND_INTERVAL, interval_seconds=600)
    sup = supervisor.Supervisor({"modules": {}})
    try:
        sup._spawn(spec, {})
    except Exception:
        pass  # only the spawn's kwargs matter here
    assert seen and seen[0].get("start_new_session") is True


def test_new_session_is_off_on_windows_and_on_elsewhere():
    assert util.NEW_SESSION is (util.os.name != "nt")
