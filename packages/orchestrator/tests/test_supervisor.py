"""The supervisor daemon's state machine (supervisor.py), driven one pass at a time with fake
clocks and fake child processes — no real spawns, no real scheduler.

The properties pinned here are the ones the OS scheduler used to provide: no double-fire (overlap
guard), crash containment (per-job backoff), an authoritative run record (the registry), and — new
obligations the scheduler never had — adopt-don't-kill on restart and resident-child silence
supervision.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import supersnap, supervisor

ET = ZoneInfo("America/New_York")
MONDAY_NOON = datetime(2026, 8, 10, 12, 0, tzinfo=ET)


class FakeProc:
    _next_pid = 91000

    def __init__(self, argv, **kw):
        self.argv = list(argv)
        self.kw = kw
        FakeProc._next_pid += 1
        self.pid = FakeProc._next_pid
        self._code = None
        self.terminated = False

    def poll(self):
        return self._code

    def exit(self, code):
        self._code = code

    def terminate(self):
        self.terminated = True
        self._code = -15

    def kill(self):
        self._code = -9

    def wait(self, timeout=None):
        return self._code


@pytest.fixture
def spawned(monkeypatch, tmp_path):
    """Patch Popen inside supervisor.py, isolate logs, and pretend every fake PID is alive."""
    procs: list[FakeProc] = []

    def fake_popen(argv, **kw):
        p = FakeProc(argv, **kw)
        procs.append(p)
        return p

    monkeypatch.setattr(supervisor.subprocess, "Popen", fake_popen)
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(cfgmod, "LOGS_DIR", logs, raising=False)
    monkeypatch.setattr(cfgmod, "log_file", lambda name: logs / name)
    # fake children read as alive while unexited
    real_alive = supervisor.pid_alive
    monkeypatch.setattr(
        supervisor,
        "pid_alive",
        lambda pid: any(p.pid == pid and p.poll() is None for p in procs) or real_alive(pid),
    )
    return procs


def base_cfg(**overrides):
    cfg = {
        "timezone": "America/New_York",
        "modules": {},
        "watchdog": {"interval_minutes": 10},
        "trade_notify": {"task_name": "cherrypick-trade-notify", "interval_seconds": 30},
        "follow_feed": {"enabled": False},
    }
    cfg.update(overrides)
    return cfg


def flies_cfg(tmp_path):
    return base_cfg(
        modules={
            "flies": {
                "enabled": True,
                "path": str(tmp_path),
                "paper": {
                    "kind": "self_healing",
                    "once_argv": ["-m", "cherrypick.flies.paper_loop", "--once"],
                    "tick_interval_seconds": 15,
                    "log": "flies_paper.log",
                },
            }
        }
    )


def test_launcher_path_resolves_to_a_real_run_py():
    """The daemon spawns `pythonw <launcher> <verb>` — a wrong parents[] depth makes every run.py
    verb exit 2 silently (caught live at the 2026-08-09 cutover; the -m module jobs masked it)."""
    assert supervisor._LAUNCHER.name == "run.py" and supervisor._LAUNCHER.exists()


def test_pass_spawns_due_jobs_and_writes_state(spawned):
    sup = supervisor.Supervisor(base_cfg())
    res = sup.pass_once(now=MONDAY_NOON)
    assert "watchdog" in res["started"] and "trade-notify" in res["started"]
    # streamer-health fires too: Monday noon is inside its 09:00-16:00 window
    assert "streamer-health" in res["started"]
    # registry + heartbeat on disk, atomically (no .tmp remnants)
    reg = json.loads(supervisor.jobs_path().read_text(encoding="utf-8"))
    assert reg["jobs"]["watchdog"]["running_pid"] == next(p.pid for p in spawned if "watchdog" in p.argv)
    hb = json.loads(supervisor.heartbeat_path().read_text(encoding="utf-8"))
    assert hb["pid"] == os.getpid() and hb["jobs"] == res["jobs"]
    assert not list(supervisor.jobs_path().parent.glob("*.tmp"))


def test_overlap_guard_never_double_fires(spawned):
    sup = supervisor.Supervisor(base_cfg())
    sup.pass_once(now=MONDAY_NOON)
    n = len(spawned)
    # force every interval due again while children are still running
    for st in sup._state.values():
        st["next_run_epoch"] = 0
    sup.pass_once(now=MONDAY_NOON)
    assert len(spawned) == n  # nothing re-spawned


def test_finished_child_respawns_when_due(spawned):
    sup = supervisor.Supervisor(base_cfg())
    sup.pass_once(now=MONDAY_NOON)
    for p in spawned:
        p.exit(0)
    for st in sup._state.values():
        st["next_run_epoch"] = 0
    res = sup.pass_once(now=MONDAY_NOON)
    assert "watchdog" in res["started"]
    st = sup._state["watchdog"]
    assert st["last_exit_code"] == 0 and st["consecutive_failures"] == 0


def test_nonzero_exit_backs_off_exponentially(spawned):
    sup = supervisor.Supervisor(base_cfg())
    sup.pass_once(now=MONDAY_NOON)
    watchdog_proc = next(p for p in spawned if "watchdog" in p.argv)
    watchdog_proc.exit(1)
    sup._state["watchdog"]["next_run_epoch"] = 0
    res = sup.pass_once(now=MONDAY_NOON)
    st = sup._state["watchdog"]
    assert st["last_exit_code"] == 1 and st["consecutive_failures"] == 1
    assert st["backoff_until"] > time.time()
    assert "watchdog" not in res["started"]  # backoff holds even though due
    # backoff expiry allows the next spawn
    st["backoff_until"] = time.time() - 1
    st["next_run_epoch"] = 0
    res = sup.pass_once(now=MONDAY_NOON)
    assert "watchdog" in res["started"]


def test_job_backoff_cap_override_shortens_the_wait(spawned, monkeypatch):
    """A job's own backoff_cap_seconds (e.g. the console dev knob) wins over the supervisor's normal
    10-minute cap, without needing enough consecutive failures to reach it exponentially."""
    real_derive_jobs = supervisor.jobspec.derive_jobs

    def capped_derive_jobs(*a, **kw):
        jobs, errors = real_derive_jobs(*a, **kw)
        jobs = [
            supervisor.jobspec.replace(j, backoff_cap_seconds=5) if j.id == "watchdog" else j
            for j in jobs
        ]
        return jobs, errors

    monkeypatch.setattr(supervisor.jobspec, "derive_jobs", capped_derive_jobs)

    sup = supervisor.Supervisor(base_cfg())
    sup.pass_once(now=MONDAY_NOON)
    watchdog_proc = next(p for p in spawned if "watchdog" in p.argv)
    watchdog_proc.exit(1)
    sup._state["watchdog"]["next_run_epoch"] = 0
    sup.pass_once(now=MONDAY_NOON)
    st = sup._state["watchdog"]
    delay = st["backoff_until"] - time.time()
    assert 0 < delay <= 5  # not the normal 30s first-failure delay, let alone the 600s cap


def test_adopts_alive_orphan_and_clears_dead_one(spawned):
    """A restarted supervisor never kills or duplicates a prior child (the MEIC lock lesson)."""
    supervisor.Supervisor(base_cfg()).pass_once(now=MONDAY_NOON)  # leaves a registry behind
    alive_pid = os.getpid()  # a PID that is definitely alive
    reg = json.loads(supervisor.jobs_path().read_text(encoding="utf-8"))
    reg["jobs"]["watchdog"]["running_pid"] = alive_pid
    reg["jobs"]["trade-notify"]["running_pid"] = 999999999  # definitely dead
    supervisor.jobs_path().write_text(json.dumps(reg), encoding="utf-8")

    sup = supervisor.Supervisor(base_cfg())
    sup.adopt_prior_state()
    assert sup._state["watchdog"]["orphaned"] is True
    assert sup._state["trade-notify"]["running_pid"] is None
    n = len(spawned)
    sup._state["watchdog"]["next_run_epoch"] = 0
    sup.pass_once(now=MONDAY_NOON)
    # the alive orphan still counts for the overlap guard — watchdog was NOT re-spawned
    assert not any("watchdog" in p.argv for p in spawned[n:])


def test_resident_child_starts_in_window_only(spawned, tmp_path):
    sup = supervisor.Supervisor(flies_cfg(tmp_path))
    before_open = datetime(2026, 8, 10, 9, 0, tzinfo=ET)
    sup.pass_once(now=before_open)
    assert not any("--interval" in p.argv for p in spawned)  # resident waits for its window
    n = len(spawned)
    res = sup.pass_once(now=MONDAY_NOON)
    assert "flies-paper" in res["started"]
    child = next(p for p in spawned if "--interval" in p.argv)
    assert child.argv[-2:] == ["--interval", "15"]
    # the off-session --once twin does NOT fire inside the window
    assert not any(p.argv[-1] == "--once" for p in spawned[n:])


def test_offsession_tick_fires_outside_window(spawned, tmp_path):
    sup = supervisor.Supervisor(flies_cfg(tmp_path))
    evening = datetime(2026, 8, 10, 16, 20, tzinfo=ET)  # settlement time
    res = sup.pass_once(now=evening)
    assert "flies-paper-offsession" in res["started"]
    assert not any("--interval" in p.argv for p in spawned)


def test_silent_resident_child_is_restarted_with_backoff(spawned, tmp_path, monkeypatch):
    cfg = flies_cfg(tmp_path)
    # The real tree kill shells out to taskkill, which would run against a fake pid here. What the
    # tree kill does is pinned separately below; this test is about the restart/backoff semantics.
    killed: list[int] = []
    monkeypatch.setattr(supervisor, "_terminate_tree", lambda pid: killed.append(pid) or True)
    sup = supervisor.Supervisor(cfg)
    sup.pass_once(now=MONDAY_NOON)
    child = next(p for p in spawned if "--interval" in p.argv)
    st = sup._state["flies-paper"]
    # age the child past the settle grace and its log past the silence window
    log = cfgmod.module_logs_dir("flies") / "flies_paper.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("x")
    old = time.time() - 1000
    os.utime(log, (old, old))
    from datetime import timedelta, timezone

    st["last_start"] = (datetime.now(timezone.utc) - timedelta(seconds=1000)).isoformat()
    sup.pass_once(now=MONDAY_NOON)
    assert child.terminated
    assert killed == [child.pid], "the child's whole tree must be taken down, not just the handle"
    assert st["running_pid"] is None and st["backoff_until"] > time.time()
    # after backoff expires the child is restarted
    st["backoff_until"] = time.time() - 1
    res = sup.pass_once(now=MONDAY_NOON)
    assert "flies-paper" in res["started"]


def test_fresh_resident_child_is_never_judged_silent(spawned, tmp_path):
    """The settling grace: a just-started child hasn't logged yet and must not be restart-looped
    (the streamer's settling lesson)."""
    sup = supervisor.Supervisor(flies_cfg(tmp_path))
    sup.pass_once(now=MONDAY_NOON)
    child = next(p for p in spawned if "--interval" in p.argv)
    sup.pass_once(now=MONDAY_NOON)  # log file doesn't even exist yet
    assert not child.terminated


def test_run_honors_stop_file_and_lock(spawned, monkeypatch):
    monkeypatch.setattr(supervisor.time, "sleep", lambda s: None)
    # a stop requested BEFORE startup is stale (it targeted a previous daemon) and is cleared —
    # uninstall avoids the race by deleting the anchor before requesting stop
    supervisor.request_stop()
    res = supervisor.run(base_cfg(), max_passes=2)
    assert res["ok"] and res["passes"] == 2
    assert not supervisor.stop_path().exists()

    # a stop arriving mid-run ends the loop on the next pass boundary
    orig = supervisor.Supervisor.pass_once

    def stop_after_first(self, now=None):
        out = orig(self, now)
        supervisor.request_stop()
        return out

    monkeypatch.setattr(supervisor.Supervisor, "pass_once", stop_after_first)
    res = supervisor.run(base_cfg(), max_passes=10)
    assert res["ok"] and res["passes"] == 1
    assert not supervisor.stop_path().exists()  # consumed on shutdown

    # a live holder blocks a second instance
    supervisor.lock_path().write_text(str(os.getpid()))
    res = supervisor.run(base_cfg(), max_passes=1)
    assert not res["ok"] and "already running" in res["detail"]


def test_snapshot_reflects_daemon_state(spawned):
    sup = supervisor.Supervisor(base_cfg())
    sup.pass_once(now=MONDAY_NOON)
    snap = supersnap.supervisor_snapshot(base_cfg(), query_anchor=False)
    assert snap["supervisor"]["running"] is True  # our own live PID + fresh heartbeat
    assert snap["jobs"]["watchdog"]["running_pid"]
    info = supersnap.job_run_info("watchdog", snap)
    assert info and info["last_run_time"]
    # the disabled opt-in stays visible with its reason
    assert snap["jobs"]["follow-notify"]["enabled"] is False


def test_derive_error_disables_one_job_and_is_reported(spawned):
    cfg = base_cfg()
    cfg["watchdog"]["interval_minutes"] = 0  # invalid → watchdog job fails derivation
    sup = supervisor.Supervisor(cfg)
    res = sup.pass_once(now=MONDAY_NOON)
    assert "watchdog" in res["errors"]
    assert "trade-notify" in res["started"]  # everything else unaffected
    reg = json.loads(supervisor.jobs_path().read_text(encoding="utf-8"))
    assert "watchdog" in reg["derive_errors"]


@pytest.mark.unit
def test_registry_drops_jobs_config_no_longer_derives(tmp_path, monkeypatch):
    """A retired job's row used to sit at `enabled: true` forever, frozen at its last fire.

    Because it is no longer evaluated it is never marked missed either, so it looks exactly like a
    scheduled job that has silently stopped firing — the earnings entry/exit rows after the 2026-08-12
    lifecycle cutover, which cost a real diagnosis to tell apart.
    """
    from cherrypick.orchestrator import supervisor as sup

    s = sup.Supervisor.__new__(sup.Supervisor)
    s._state = {
        "watchdog": {"enabled": True},
        "earnings-entry": {"enabled": True, "last_fire_day": "2026-08-11"},
        "flies-paper": {"enabled": True, "running_pid": 4242},
        "broken-job": {"enabled": False},
    }

    class _Spec:
        def __init__(self, jid):
            self.id = jid

    s._prune_retired([_Spec("watchdog")], {"broken-job": "config error"})

    assert "watchdog" in s._state, "a derived job stays"
    assert "earnings-entry" not in s._state, "a retired job's row goes"
    # A live child must keep its row or the overlap guard loses the process it would reap.
    assert "flies-paper" in s._state, "a row with a running child is never dropped"
    # Missing because it FAILED to derive, not because it was retired — its history is evidence.
    assert "broken-job" in s._state, "a derivation failure keeps its row"


@pytest.mark.unit
def test_daemon_reloads_config_when_the_file_mtime_moves(tmp_path, monkeypatch):
    """The production daemon (constructed with cfg=None) must pick up a config edit on the next
    pass -- the whole promise of "a cadence change is a config edit, no install step"."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"marker": "old"}), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "effective_config_path", lambda: cfg_file)
    monkeypatch.setattr(cfgmod, "load_config", lambda: json.loads(cfg_file.read_text(encoding="utf-8")))

    s = supervisor.Supervisor(None)
    assert s._load_cfg()["marker"] == "old"

    cfg_file.write_text(json.dumps({"marker": "new"}), encoding="utf-8")
    os.utime(cfg_file, (time.time() + 5, time.time() + 5))  # force a distinct mtime
    assert s._load_cfg()["marker"] == "new"


@pytest.mark.unit
def test_cli_supervise_does_not_pin_the_daemon_to_a_config_snapshot(monkeypatch):
    """Passing the CLI's pre-loaded cfg into supervisor.run() pins the daemon to that snapshot and
    silently disables the mtime reload -- config edits then need a restart nobody knows to perform.
    That is exactly how an enabled notifier sat derived-as-disabled for a day (2026-08-13)."""
    import cherrypick.cli as cli

    seen = {}

    def fake_run(cfg=None, **kw):
        seen["cfg"] = cfg
        return {"ok": True, "passes": 0}

    monkeypatch.setattr(supervisor, "run", fake_run)
    cli.cmd_supervise({"anything": True}, stop=False)
    assert seen["cfg"] is None, "supervise must let the daemon load (and reload) its own config"
