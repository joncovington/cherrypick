"""Dual-read behavior during the schtasks→supervisor transition, and the watchdog/doctor checks
over the supervisor itself.

The contract: with NO supervisor heartbeat (a pre-cutover box) everything reads exactly as before —
scheduled tasks via `tasks.exists`, zero new findings. With a live supervisor, registration checks
read the job registry file (no schtasks spawns) and the watchdog gains supervisor.alive/anchor
findings.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from cherrypick.orchestrator import doctor, supersnap, supervisor, watchdog


def write_heartbeat(age_seconds: float = 0.0, pid: int | None = None):
    ts = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    supervisor.heartbeat_path().parent.mkdir(parents=True, exist_ok=True)
    supervisor.heartbeat_path().write_text(
        json.dumps({"ts": ts.isoformat(), "pid": pid if pid is not None else os.getpid()}),
        encoding="utf-8",
    )


def write_jobs(jobs: dict):
    supervisor.jobs_path().write_text(json.dumps({"schema": 1, "jobs": jobs}), encoding="utf-8")


MEIC_CFG = {"path": ".", "paper": {"kind": "self_healing", "task_name": "cherrypick-meic-paper-loop"}}


@pytest.fixture
def no_schtasks(monkeypatch):
    """Any schtasks query under a live supervisor is a bug — the spawn tax this migration removes."""

    def boom(name):
        raise AssertionError(f"tasks.exists({name!r}) called on a supervisor-driven box")

    monkeypatch.setattr(watchdog.tasks, "exists", boom)


# --------------------------------------------------------------------- pre-cutover: unchanged
def test_no_heartbeat_means_legacy_reads_and_no_new_findings(monkeypatch):
    monkeypatch.setattr(watchdog.tasks, "exists", lambda name: True)
    findings = watchdog._check_meic("meic", MEIC_CFG, in_session=False)
    task = next(f for f in findings if f.key == "meic.task")
    assert task.status == watchdog.OK and task.message == "registered"
    assert watchdog._check_supervisor({}) == []  # skips itself entirely


# --------------------------------------------------------------------- supervisor-driven reads
def test_meic_task_reads_job_registry_not_schtasks(no_schtasks):
    write_heartbeat()
    write_jobs({"meic-paper": {"enabled": True}})
    findings = watchdog._check_meic("meic", MEIC_CFG, in_session=False)
    task = next(f for f in findings if f.key == "meic.task")
    assert task.status == watchdog.OK and task.message == "supervised"


def test_meic_job_missing_or_disabled_is_critical(no_schtasks):
    write_heartbeat()
    write_jobs({})
    task = next(f for f in watchdog._check_meic("meic", MEIC_CFG, in_session=False) if f.key == "meic.task")
    assert task.status == watchdog.CRITICAL and "missing" in task.title

    write_jobs({"meic-paper": {"enabled": False, "enabled_reason": "derivation failed"}})
    task = next(f for f in watchdog._check_meic("meic", MEIC_CFG, in_session=False) if f.key == "meic.task")
    assert task.status == watchdog.CRITICAL and "derivation failed" in task.message


def test_earnings_tasks_read_job_registry(no_schtasks):
    write_heartbeat()
    write_jobs({"earnings-entry": {"enabled": True}, "earnings-exit": {"enabled": True}})
    mcfg = {
        "paper": {
            "kind": "cherrypick_scheduled",
            "entry_task_name": "cherrypick-earnings-paper-entry",
            "exit_task_name": "cherrypick-earnings-paper-exit",
        }
    }
    now = datetime(2026, 8, 10, 12, 0)
    findings = watchdog._check_earnings("earnings", mcfg, now, is_trading=False)
    for label in ("entry", "exit"):
        f = next(x for x in findings if x.key == f"earnings.task.{label}")
        assert f.status == watchdog.OK and f.message == "supervised"


# --------------------------------------------------------------------- the supervisor findings
def test_live_supervisor_and_anchor_are_ok(monkeypatch):
    write_heartbeat()
    monkeypatch.setattr(watchdog.tasks, "exists", lambda name: name == supersnap.ANCHOR_TASK)
    findings = watchdog._check_supervisor({})
    assert {f.key: f.status for f in findings} == {
        "supervisor.alive": watchdog.OK,
        "supervisor.anchor": watchdog.OK,
    }


def test_stale_heartbeat_is_critical(monkeypatch):
    write_heartbeat(age_seconds=600)
    monkeypatch.setattr(watchdog.tasks, "exists", lambda name: True)
    alive = next(f for f in watchdog._check_supervisor({}) if f.key == "supervisor.alive")
    assert alive.status == watchdog.CRITICAL and "600" in alive.message


def test_dead_pid_with_fresh_heartbeat_is_critical(monkeypatch):
    """A fresh file with a dead PID is a just-crashed daemon — freshness alone must not read OK."""
    write_heartbeat(pid=999999999)
    monkeypatch.setattr(watchdog.tasks, "exists", lambda name: True)
    alive = next(f for f in watchdog._check_supervisor({}) if f.key == "supervisor.alive")
    assert alive.status == watchdog.CRITICAL


def test_missing_anchor_is_critical_while_supervisor_runs(monkeypatch):
    write_heartbeat()
    monkeypatch.setattr(watchdog.tasks, "exists", lambda name: False)
    anchor = next(f for f in watchdog._check_supervisor({}) if f.key == "supervisor.anchor")
    assert anchor.status == watchdog.CRITICAL and "nothing will restart" in anchor.message


# --------------------------------------------------------------------- console (the read surface)
def test_no_heartbeat_means_console_check_skips_too(monkeypatch):
    """Pre-cutover box: the console job isn't derived/tracked here yet either."""
    assert watchdog._check_console({}) == []


def test_console_disabled_in_config_is_not_alarmed_on(no_schtasks):
    write_heartbeat()
    write_jobs({})
    assert watchdog._check_console({"console": {"enabled": False}}) == []


def test_console_running_is_ok(no_schtasks):
    write_heartbeat()
    write_jobs({"console": {"enabled": True, "resident_state": "running"}})
    task = next(f for f in watchdog._check_console({}) if f.key == "console.task")
    assert task.status == watchdog.OK and task.message == "running"


def test_console_job_missing_or_disabled_is_critical(no_schtasks):
    write_heartbeat()
    write_jobs({})
    task = next(f for f in watchdog._check_console({}) if f.key == "console.task")
    assert task.status == watchdog.CRITICAL and "missing" in task.title

    write_jobs({"console": {"enabled": False, "enabled_reason": "disabled in config (console)"}})
    task = next(f for f in watchdog._check_console({}) if f.key == "console.task")
    assert task.status == watchdog.CRITICAL and "disabled in config (console)" in task.message


def test_console_stuck_in_backoff_warns(no_schtasks):
    """The gap found live on 2026-08-14: the console has no paper writes or log whose staleness
    would out a stuck restart loop, so this resident_state read is the only thing that can."""
    write_heartbeat()
    write_jobs({"console": {"enabled": True, "resident_state": "backoff"}})
    task = next(f for f in watchdog._check_console({}) if f.key == "console.task")
    assert task.status == watchdog.WARN and "not running" in task.title.lower()

    write_jobs({"console": {"enabled": True, "resident_state": "start failed"}})
    task = next(f for f in watchdog._check_console({}) if f.key == "console.task")
    assert task.status == watchdog.WARN


# --------------------------------------------------------------------- resident job health
def _resident(**over):
    base = {"enabled": True, "kind": "resident", "resident_state": "running", "heartbeat_seen": True}
    return {**base, **over}


def test_a_thrashing_resident_job_is_reported(no_schtasks):
    """The whole reason this check exists. On 2026-08-17 calendars-paper was killed and restarted
    107 times while the watchdog reported it OK / 0 min old on every tick — its restarts kept its own
    paper DB fresh, so the freshness check read the thrash as health."""
    write_heartbeat()
    write_jobs({"calendars-paper": _resident(starts_in_window=107)})
    churn = next(f for f in watchdog._check_resident_health({}) if f.key == "calendars-paper.churn")
    assert churn.status == watchdog.WARN and "107 starts" in churn.message


def test_a_few_restarts_are_not_churn(no_schtasks):
    """A couple of genuine crashes in a session is bad luck, not a finding — this must fire on the
    shape those sessions had, never on noise."""
    write_heartbeat()
    write_jobs({"calendars-paper": _resident(starts_in_window=2)})
    assert not [f for f in watchdog._check_resident_health({}) if f.key.endswith(".churn")]


def test_consecutive_failures_could_not_have_caught_it(no_schtasks):
    """Pins why a new counter was needed. A clean exit resets consecutive_failures, and a clean exit
    is the storm's own signature — the live registry showed 0 failures beside 161 spawns."""
    write_heartbeat()
    write_jobs({"calendars-paper": _resident(starts_in_window=53, consecutive_failures=0)})
    assert [f for f in watchdog._check_resident_health({}) if f.key.endswith(".churn")]


def test_a_module_that_stopped_itself_mid_window_is_reported(no_schtasks):
    """The trade the supervisor now makes: a session-scoped loop that exits 0 is believed and not
    restarted. If it said so wrongly nothing brings it back until the window reopens, so this is the
    only thing that would surface it."""
    write_heartbeat()
    write_jobs(
        {
            "calendars-paper": _resident(
                module_stopped=True, resident_state="module reports session complete"
            )
        }
    )
    stopped = next(f for f in watchdog._check_resident_health({}) if f.key == "calendars-paper.stopped")
    assert stopped.status == watchdog.WARN


def test_a_resident_publishing_no_heartbeat_is_reported(no_schtasks):
    """A module with no heartbeat is deliberately NOT silence-supervised — restarting a process
    nobody can judge is the bug this area is recovering from — so the cost of that safe degrade is
    reported here rather than paid silently."""
    write_heartbeat()
    write_jobs(
        {
            "flies-paper": _resident(
                heartbeat_seen=False, silence_file="/tmp/flies.heartbeat", running_pid=4242
            )
        }
    )
    beat = next(f for f in watchdog._check_resident_health({}) if f.key == "flies-paper.heartbeat")
    assert beat.status == watchdog.WARN and "no heartbeat" in beat.title


def test_interval_jobs_are_not_resident_and_are_left_alone(no_schtasks):
    """Only residents have this failure mode; an interval job's repeated starts are its schedule."""
    write_heartbeat()
    write_jobs({"meic-paper": {"enabled": True, "kind": "interval", "starts_in_window": 900}})
    assert watchdog._check_resident_health({}) == []


# --------------------------------------------------------------------- doctor's dual-read
def test_doctor_suite_checks_read_jobs_under_supervisor(monkeypatch):
    write_heartbeat()
    write_jobs(
        {
            "trade-notify": {"enabled": True},
            "log-archive": {"enabled": True},
            "streamer-health": {"enabled": True},
            "console": {"enabled": True},
            "reconcile": {"enabled": False, "enabled_reason": "disabled in config (reconcile.schedule)"},
        }
    )
    monkeypatch.setattr(
        doctor.tasks, "exists", lambda name: (_ for _ in ()).throw(AssertionError("schtasks spawned"))
    )
    checks = {c.name: c for c in doctor._suite_task_checks({})}
    assert checks["task.trade_notify"].status == doctor.OK
    assert checks["task.streamer_health"].status == doctor.OK  # preopen's replacement
    assert checks["task.console"].status == doctor.OK
    assert "preopen" not in {c.name for c in doctor._suite_task_checks({})}
    # opted-out features stay healthy-disabled, not warnings
    assert checks["task.reconcile"].status == doctor.OK


def test_doctor_enabled_but_missing_job_warns():
    write_heartbeat()
    write_jobs({})
    checks = {c.name: c for c in doctor._suite_task_checks({})}
    assert checks["task.trade_notify"].status == doctor.WARN
    assert "missing" in checks["task.trade_notify"].detail
    assert checks["task.console"].status == doctor.WARN
    assert "missing" in checks["task.console"].detail


def test_doctor_supervisor_checks_fast_mode_skips_spawny_parts(monkeypatch):
    write_heartbeat()
    monkeypatch.setattr(
        doctor.tasks, "exists", lambda name: (_ for _ in ()).throw(AssertionError("schtasks spawned"))
    )
    checks = doctor._supervisor_checks({}, fast=True)
    assert [c.name for c in checks] == ["supervisor"]
    assert checks[0].status == doctor.OK


def test_doctor_legacy_leftover_task_warns(monkeypatch):
    write_heartbeat()
    cfg = {"watchdog": {"task_name": "cherrypick-watchdog"}, "modules": {}}
    monkeypatch.setattr(doctor.tasks, "exists", lambda name: name == "cherrypick-watchdog")
    checks = {c.name: c for c in doctor._supervisor_checks(cfg, fast=False)}
    legacy = checks["supervisor.legacy_tasks"]
    assert legacy.status == doctor.WARN and "cherrypick-watchdog" in legacy.detail
    # anchor missing also fails loudly in full mode
    assert checks["supervisor.anchor"].status == doctor.FAIL


def test_doctor_pre_cutover_box_has_no_supervisor_checks():
    assert doctor._supervisor_checks({}, fast=False) == []


def test_a_self_healing_earnings_module_is_not_asked_for_entry_exit_jobs(no_schtasks):
    """The lifecycle cutover replaced two daily jobs with one continuous loop that does entry and
    exit from its own clock. Those jobs are now correctly ABSENT, so looking them up would raise a
    CRITICAL every tick for a shape that is working as designed. entry_task_name survives in config
    for deleting the pre-cutover scheduled tasks by name, so the kind is the discriminator."""
    write_heartbeat()
    write_jobs({"earnings-paper": {"enabled": True}})
    mcfg = {
        "paper": {
            "kind": "self_healing",
            "entry_task_name": "cherrypick-earnings-paper-entry",
            "exit_task_name": "cherrypick-earnings-paper-exit",
        }
    }
    findings = watchdog._check_earnings("earnings", mcfg, datetime(2026, 8, 10, 12, 0), is_trading=False)
    assert not [f for f in findings if f.key.startswith("earnings.task.")]


def test_the_entry_sla_survives_the_cutover(no_schtasks, monkeypatch):
    """The loop writes the same heartbeat files the scheduled verb used to — the file and its shape
    are the contract, not who writes it. So a missed scan is still CRITICAL under the new kind."""
    write_heartbeat()
    write_jobs({"earnings-paper": {"enabled": True}})
    monkeypatch.setattr(watchdog, "_read_heartbeat", lambda path: {})
    mcfg = {"paper": {"kind": "self_healing", "entry_time": "15:45", "entry_sla_grace_minutes": 35}}

    findings = watchdog._check_earnings("earnings", mcfg, datetime(2026, 8, 10, 16, 30), is_trading=True)
    sla = next(f for f in findings if f.key == "earnings.entry_sla")
    assert sla.status == watchdog.CRITICAL


# ------------------------------------------------- the stale job table (added 2026-08-25)
def _stub_derive(monkeypatch, ids):
    from types import SimpleNamespace

    monkeypatch.setattr(
        watchdog.jobspec,
        "derive_jobs",
        lambda cfg, **kw: ([SimpleNamespace(id=i) for i in ids], {}),
    )


def test_a_job_the_running_supervisor_never_derived_is_reported(monkeypatch, no_schtasks):
    """The fault nothing else in the suite can see.

    The supervisor imports jobspec once, at startup, so a job added to that module does not exist
    until the daemon restarts. The registry is a picture of what it is CURRENTLY driving -- so the
    new job is not a row that looks wrong, it is no row at all, and status/doctor/watchdog all read
    healthy because they enumerate that registry. `earnings-dolt-pull` sat in the source for a day
    like this while the earnings calendar it exists to refresh went on ageing out; it had never run.
    """
    write_heartbeat()
    write_jobs({"meic-paper": {"enabled": True}})
    _stub_derive(monkeypatch, ["meic-paper", "earnings-dolt-pull", "futures-contracts"])

    f = next(f for f in watchdog._check_job_registry_drift({}) if f.key == "supervisor.jobs_stale")

    assert f.status == watchdog.WARN
    assert "earnings-dolt-pull" in f.message and "futures-contracts" in f.message
    assert "supervise --stop" in f.message, "the finding has to say how to fix it"


def test_a_matching_job_table_is_ok(monkeypatch, no_schtasks):
    write_heartbeat()
    write_jobs({"meic-paper": {"enabled": True}, "watchdog": {"enabled": True}})
    _stub_derive(monkeypatch, ["meic-paper", "watchdog"])

    f = next(iter(watchdog._check_job_registry_drift({})))
    assert f.status == watchdog.OK and f.key == "supervisor.jobs_current"


def test_a_retired_job_still_in_the_registry_is_not_drift(monkeypatch, no_schtasks):
    """Only the derived-but-absent direction. A row for a job config no longer derives is
    `_prune_retired`'s business and is expected mid-cutover -- flagging it would make every
    retirement look like a fault."""
    write_heartbeat()
    write_jobs({"meic-paper": {"enabled": True}, "symbol-watch": {"enabled": False}})
    _stub_derive(monkeypatch, ["meic-paper"])

    assert all(f.status == watchdog.OK for f in watchdog._check_job_registry_drift({}))


def test_a_dead_supervisor_is_not_double_reported(monkeypatch, no_schtasks):
    """`_check_supervisor` already CRITICALs a dead daemon, and its registry describes a world that
    is no longer running -- judging it would add a second alarm for one fault."""
    write_heartbeat(age_seconds=99999)
    write_jobs({})
    _stub_derive(monkeypatch, ["meic-paper"])

    assert watchdog._check_job_registry_drift({}) == []


def test_a_just_restarted_supervisor_is_given_a_moment(monkeypatch, no_schtasks):
    """The restart IS the remedy here, so a finding that always fires for two minutes afterwards
    would train the reader to ignore it."""
    import json as _json

    ts = datetime.now(timezone.utc)
    supervisor.heartbeat_path().write_text(
        _json.dumps({"ts": ts.isoformat(), "pid": os.getpid(), "started_at": ts.isoformat()}),
        encoding="utf-8",
    )
    write_jobs({})
    _stub_derive(monkeypatch, ["meic-paper"])

    assert watchdog._check_job_registry_drift({}) == []


def test_a_job_that_failed_to_derive_is_reported(monkeypatch, no_schtasks):
    """The third state, and the only one invisible from both directions.

    A job whose derivation raised is omitted from the derived table, so the drift check never sees
    it missing; and `_prune_retired` deliberately KEEPS its registry row, because it is absent
    through breakage rather than retirement. Both checks read healthy. The supervisor has always
    recorded these and nothing read them: on 2026-08-20 `advisor-open` failed with a ValueError and
    the sole trace anywhere was one line in supervisor.log.
    """
    write_heartbeat()
    supervisor.jobs_path().write_text(
        json.dumps(
            {
                "schema": 1,
                "jobs": {"meic-paper": {"enabled": True}},
                "derive_errors": {"advisor-open": "ValueError: not enough values to unpack"},
            }
        ),
        encoding="utf-8",
    )
    _stub_derive(monkeypatch, ["meic-paper"])

    findings = watchdog._check_job_registry_drift({})

    f = next(f for f in findings if f.key == "supervisor.job_derive_failed")
    assert f.status == watchdog.WARN
    assert "advisor-open" in f.title and "not enough values" in f.message
    # The drift half still answers independently -- a derivation failure is not table drift.
    assert any(f.key == "supervisor.jobs_current" for f in findings)
