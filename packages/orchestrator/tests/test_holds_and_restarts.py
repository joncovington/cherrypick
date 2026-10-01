"""Stops and restarts someone ASKED for (2026-10-01): never two running, never an alarm.

The supervisor acts on a restart request itself -- stop the child, confirm it gone by PID and
creation time, only then relaunch -- and a held job is stopped and not relaunched. The watchdog
neither restarts nor alarms on what was stopped on purpose, and a requested exit's code is not a
failure to the live-loop check.
"""

from __future__ import annotations

import time

import pytest
from test_supervisor import MONDAY_NOON, FakeProc, base_cfg, flies_cfg, spawned  # noqa: F401

from cherrypick.orchestrator import holds, supersnap, supervisor
from cherrypick.orchestrator import watchdog as wd


def _live(spawned, needle):  # noqa: F811
    return [p for p in spawned if needle in " ".join(p.argv) and p.poll() is None]


# --------------------------------------------------------------------------- restart: never two


def test_a_restart_stops_the_old_child_before_launching_one(spawned, tmp_path):  # noqa: F811
    sup = supervisor.Supervisor(flies_cfg(tmp_path))
    sup.pass_once(now=MONDAY_NOON)
    old = _live(spawned, "--interval")[0]
    supervisor.request_restart("flies-paper", by="test", request_id="r1")
    sup.pass_once(now=MONDAY_NOON)

    assert old.poll() is not None  # stopped
    assert len(_live(spawned, "--interval")) == 1  # and exactly one in its place
    st = sup._state["flies-paper"]
    assert st["last_restart"]["id"] == "r1" and st["last_exit_requested"] is True
    assert not st.get("consecutive_failures")


def test_a_child_that_will_not_die_is_not_replaced(spawned, tmp_path, monkeypatch):  # noqa: F811
    """The whole guarantee: nothing new starts beside a process that is still running."""
    monkeypatch.setattr(supervisor, "_STOP_CHILD_TIMEOUT_S", 0.3)
    sup = supervisor.Supervisor(flies_cfg(tmp_path))
    sup.pass_once(now=MONDAY_NOON)
    stubborn = _live(spawned, "--interval")[0]
    stubborn.terminate = lambda: None  # ignores every attempt
    monkeypatch.setattr(supervisor, "_terminate_tree", lambda pid: True)
    supervisor.request_restart("flies-paper", by="test", request_id="r2")
    sup.pass_once(now=MONDAY_NOON)

    assert stubborn.poll() is None
    assert len(_live(spawned, "--interval")) == 1, "a second child was launched beside a live one"
    assert sup._state["flies-paper"]["last_restart"]["result"] == "old process did not exit"


def test_asking_twice_restarts_once(spawned, tmp_path):  # noqa: F811
    sup = supervisor.Supervisor(flies_cfg(tmp_path))
    sup.pass_once(now=MONDAY_NOON)
    supervisor.request_restart("flies-paper", by="a", request_id="1")
    supervisor.request_restart("flies-paper", by="b", request_id="2")  # overwrites, not queues
    n = len(spawned)
    sup.pass_once(now=MONDAY_NOON)
    sup.pass_once(now=MONDAY_NOON)
    assert len(spawned) == n + 1 and len(_live(spawned, "--interval")) == 1


def test_a_reused_pid_is_not_mistaken_for_the_adopted_child(spawned, monkeypatch):  # noqa: F811
    """A registry PID now owned by a different (newer) process: the job is not still running."""
    supervisor.Supervisor(base_cfg()).pass_once(now=MONDAY_NOON)
    sup = supervisor.Supervisor(base_cfg())
    sup.adopt_prior_state()
    import os

    st = sup._state["watchdog"]
    st["running_pid"], st["pid_started_at"] = os.getpid(), 1_000.0  # alive, but born long after
    monkeypatch.setattr(supervisor._looplock, "process_start_time", lambda pid: 2_000_000_000.0)
    assert supervisor.same_process(st["running_pid"], st["pid_started_at"]) is False


# --------------------------------------------------------------------------- holds


def test_a_held_job_is_stopped_and_not_relaunched(spawned, tmp_path):  # noqa: F811
    sup = supervisor.Supervisor(flies_cfg(tmp_path))
    sup.pass_once(now=MONDAY_NOON)
    holds.hold("flies-paper", by="test")
    for _ in range(5):
        sup.pass_once(now=MONDAY_NOON)
    assert _live(spawned, "--interval") == []
    st = sup._state["flies-paper"]
    assert st["resident_state"] == "held" and not st.get("consecutive_failures")
    assert not st.get("starts_in_window") or st["starts_in_window"] == 1  # no churn from the stop

    assert holds.release("flies-paper") is True
    sup.pass_once(now=MONDAY_NOON)
    assert len(_live(spawned, "--interval")) == 1  # started again, once


def test_holds_survive_a_missing_or_corrupt_file_as_nothing_held():
    holds.path().write_text("not json", encoding="utf-8")
    assert holds.all_holds() == {}


# --------------------------------------------------------------------------- the watchdog stays quiet


def test_the_watchdog_neither_restarts_nor_alarms_on_a_held_streamer(monkeypatch, tmp_path):
    holds.hold("streamer", by="test")
    started = []
    monkeypatch.setattr(wd, "_start_streamer", lambda *a, **k: started.append(1) or True)
    findings = wd._check_streamer_health("streamer", tmp_path, {"auto_restart": True})
    assert started == [] and [f.status for f in findings] == [wd.OK]


def test_a_requested_exit_code_is_not_a_failed_live_tick():
    assert wd._tick_failed({"last_exit_code": -3, "last_exit_requested": True}) is False
    assert wd._tick_failed({"last_exit_code": 1, "last_exit_requested": False}) is True  # a real crash
    assert wd._tick_failed({"last_exit_code": 0}) is False


def test_holds_are_listed_quietly_then_warned_when_forgotten(monkeypatch):
    holds.hold("console", by="test")
    [f] = wd._check_holds()
    assert f.status == wd.OK and "console" in f.message
    old = holds.all_holds()
    old["console"]["at"] = time.time() - (wd._HOLD_FORGOTTEN_HOURS + 1) * 3600
    holds.atomic_write_json(holds.path(), old)
    [f] = wd._check_holds()
    assert f.status == wd.WARN


# --------------------------------------------------------------------------- the commands (proc)


def test_restart_refuses_when_nothing_would_start_it_again(monkeypatch):
    from cherrypick.orchestrator import proc

    monkeypatch.setattr(proc.supersnap, "supervisor_alive", lambda: False)
    asked = []
    monkeypatch.setattr(proc.supervisor, "request_restart", lambda *a, **k: asked.append(a))
    rec = proc.restart_job("console")
    assert rec["ok"] is False and asked == []


def test_restart_waits_for_the_supervisors_own_answer(monkeypatch):
    """It reports success only for ITS request (the id), and only once a new PID is in place."""
    from cherrypick.orchestrator import proc

    monkeypatch.setattr(proc, "POLL_S", 0)
    monkeypatch.setattr(proc.supersnap, "supervisor_alive", lambda: True)
    sent = {}
    monkeypatch.setattr(
        proc.supervisor, "request_restart", lambda name, by, request_id: sent.update(id=request_id)
    )
    answers = iter(
        [
            {"kind": "resident", "running_pid": 10},  # before
            {"kind": "resident", "running_pid": 10, "last_restart": {"id": "someone-else"}},
            {"kind": "resident", "running_pid": None, "last_restart": {"id": None}},
        ]
    )

    def state(name):
        try:
            return next(answers)
        except StopIteration:
            return {
                "kind": "resident",
                "running_pid": 11,
                "last_restart": {"id": sent["id"], "result": "restarted"},
            }

    monkeypatch.setattr(proc, "_job", state)
    rec = proc.restart_job("console", timeout=5)
    assert rec == {"ok": True, "name": "console", "old_pid": 10, "new_pid": 11, "result": "restarted"}


def test_stop_with_the_supervisor_down_kills_only_a_matching_process(monkeypatch):
    from cherrypick.orchestrator import proc

    monkeypatch.setattr(proc.supersnap, "supervisor_alive", lambda: False)
    monkeypatch.setattr(proc, "_job", lambda name: {"running_pid": 4242, "pid_started_at": 100.0})
    killed = []
    monkeypatch.setattr(proc.supervisor, "_terminate_tree", lambda pid: killed.append(pid) or True)
    # The PID is alive but is a different, newer process: not ours to kill.
    monkeypatch.setattr(proc.supervisor, "same_process", lambda pid, started: False)
    rec = proc.stop_job("console")
    assert killed == [] and rec["ok"] and rec["detail"] == "not running" and holds.is_held("console")


def test_stop_all_refuses_while_anything_could_start_it_again(monkeypatch):
    from cherrypick.orchestrator import proc

    monkeypatch.setattr(proc.supersnap, "supervisor_alive", lambda: False)
    assert proc.stop_all({}, anchor_registered=True)["ok"] is False
    monkeypatch.setattr(proc.supersnap, "supervisor_alive", lambda: True)
    assert proc.stop_all({}, anchor_registered=False)["ok"] is False


@pytest.mark.parametrize("job", ["watchdog"])
def test_run_info_carries_the_requested_flag(spawned, job):  # noqa: F811
    sup = supervisor.Supervisor(base_cfg())
    sup.pass_once(now=MONDAY_NOON)
    supervisor.request_restart(job, by="test")
    sup._state[job]["next_run_epoch"] = 0
    sup.pass_once(now=MONDAY_NOON)
    info = supersnap.job_run_info(job)
    assert info["last_exit_requested"] is True
