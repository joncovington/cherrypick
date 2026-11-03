"""`_check_live` on a supervisor-driven box — the re-keyed dead-man's switch.

Every safety property the schtasks-keyed checks pinned (test_watchdog_live.py, which still covers
the pre-cutover branch) has its supervisor equivalent here: the arm RECORD is the armed signal, the
job registry is the run record, and two new states are strictly stronger than before — a dead
supervisor while armed, and an armed record whose job never enabled.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cherrypick.orchestrator import liveops, supersnap, supervisor
from cherrypick.orchestrator import watchdog as wd
from cherrypick.orchestrator.watchdog import CRITICAL, OK

pytestmark = pytest.mark.unit

_TODAY = "2026-07-30"
_MIDDAY = datetime(2026, 7, 30, 11, 0)
_PAST_DISARM = datetime(2026, 7, 30, 17, 45)  # past 17:00 + 30m grace


def _mcfg(**live_over):
    live = {
        "task_name": "cherrypick-flies-live-loop",
        "status_argv": ["src/live_loop.py", "--status"],
        "log": "flies_live.log",
        "freshness_minutes": 5,
        "disarm_time": "17:00",
        "disarm_grace_minutes": 30,
        "settlement_grace_minutes": 30,
    }
    live.update(live_over)
    return {"live": live}


def _iso_ago(minutes: float, now=_MIDDAY) -> str:
    return (now.astimezone(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def _setup(monkeypatch, tmp_path, *, status_obj, heartbeat_age=0.0, hb_pid=None):
    monkeypatch.setattr(wd.cfgmod, "module_root", lambda *a, **k: Path("."))
    monkeypatch.setattr(wd.cfgmod, "module_logs_dir", lambda name: tmp_path / "logs")
    monkeypatch.setattr(liveops, "halt_flag_path", lambda: tmp_path / "halt-live.flag")
    # any schtasks read is only legal as the transition belt-and-braces when the record is absent
    monkeypatch.setattr(wd.tasks, "exists", lambda name: False)

    ts = datetime.now(timezone.utc) - timedelta(seconds=heartbeat_age)
    supervisor.heartbeat_path().parent.mkdir(parents=True, exist_ok=True)
    supervisor.heartbeat_path().write_text(
        json.dumps({"ts": ts.isoformat(), "pid": hb_pid if hb_pid is not None else os.getpid()}),
        encoding="utf-8",
    )

    class _R:
        returncode = 0
        stdout = json.dumps(status_obj) if status_obj is not None else ""

    monkeypatch.setattr(wd, "_run_module", lambda *a, **k: _R())


def _arm(date=_TODAY):
    supervisor.arm_record_path("flies").write_text(
        json.dumps({"date": date, "armed_by": "live-flies-start"}), encoding="utf-8"
    )


def _jobs(state: dict):
    supervisor.jobs_path().write_text(json.dumps({"schema": 1, "jobs": state}), encoding="utf-8")


def test_armed_with_running_tick_is_ok(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, status_obj={"armed_for": _TODAY})
    _arm()
    _jobs({"flies-live": {"enabled": True, "last_start": _iso_ago(0.5), "running_pid": os.getpid()}})
    out = wd._check_live("flies", _mcfg(), _MIDDAY, True)
    fresh = next(f for f in out if f.key == "flies.live_fresh")
    assert fresh.status == OK and "running" in fresh.message


def test_armed_fresh_last_tick_is_ok_even_with_no_log(monkeypatch, tmp_path):
    """The quiet-but-healthy tick: recent start, clean exit, nothing logged — never a CRITICAL
    (the false alarm `last_run_info` was introduced to kill, preserved under the registry)."""
    _setup(monkeypatch, tmp_path, status_obj={"armed_for": _TODAY})
    _arm()
    _jobs(
        {
            "flies-live": {
                "enabled": True,
                "last_start": _iso_ago(2),
                "running_pid": None,
                "last_exit_code": 0,
            }
        }
    )
    out = wd._check_live("flies", _mcfg(), _MIDDAY, True)
    assert next(f for f in out if f.key == "flies.live_fresh").status == OK


def test_stale_last_tick_is_critical(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, status_obj={"armed_for": _TODAY})
    _arm()
    _jobs({"flies-live": {"enabled": True, "last_start": _iso_ago(20), "last_exit_code": 0}})
    out = wd._check_live("flies", _mcfg(), _MIDDAY, True)
    fresh = next(f for f in out if f.key == "flies.live_fresh")
    assert fresh.status == CRITICAL and "unwatched" in fresh.message


def test_nonzero_exit_is_critical_even_if_recent(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, status_obj={"armed_for": _TODAY})
    _arm()
    _jobs({"flies-live": {"enabled": True, "last_start": _iso_ago(1), "last_exit_code": 1}})
    out = wd._check_live("flies", _mcfg(), _MIDDAY, True)
    fresh = next(f for f in out if f.key == "flies.live_fresh")
    assert fresh.status == CRITICAL and "exit=1" in fresh.message


def test_armed_but_job_missing_is_critical(monkeypatch, tmp_path):
    """Arming didn't take: the record exists but the supervisor derived no enabled job."""
    _setup(monkeypatch, tmp_path, status_obj={"armed_for": _TODAY})
    _arm()
    _jobs({})
    out = wd._check_live("flies", _mcfg(), _MIDDAY, True)
    fresh = next(f for f in out if f.key == "flies.live_fresh")
    assert fresh.status == CRITICAL and "did not take" in fresh.message


def test_supervisor_down_while_armed_is_critical(monkeypatch, tmp_path):
    """A dead supervisor while live is armed = a silent live loop. Strictly stronger than any
    freshness read — this state had no detector at all under schtasks."""
    _setup(monkeypatch, tmp_path, status_obj={"armed_for": _TODAY}, heartbeat_age=600)
    _arm()
    _jobs({"flies-live": {"enabled": True, "last_start": _iso_ago(1), "last_exit_code": 0}})
    out = wd._check_live("flies", _mcfg(), _MIDDAY, True)
    down = next(f for f in out if f.key == "flies.live_supervisor")
    assert down.status == CRITICAL and "no live ticks are being fired" in down.message


def test_a_surviving_arm_record_nothing_obeys_is_removed_not_halted(monkeypatch, tmp_path):
    """2026-10-09: the supervisor has already turned the live job off past disarm, so a record that
    outlives the window (the machine was off when the tick would have deleted it) drives nothing.
    Halting on it re-set the suite halt every pass and blocked the next day's live entries."""
    _setup(monkeypatch, tmp_path, status_obj={"armed_for": _TODAY})
    _arm()
    _jobs({"flies-live": {"enabled": False, "enabled_reason": "past disarm 17:00 (+30m grace)"}})
    out = wd._check_live("flies", _mcfg(), _PAST_DISARM, False)
    disarm = next(f for f in out if f.key == "flies.live_disarm")
    assert disarm.status == OK and "removed" in disarm.message
    assert not (tmp_path / "halt-live.flag").exists()


def test_a_previous_days_arm_record_is_removed_even_mid_morning(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, status_obj={"armed_for": "2026-07-29"})
    _arm(date="2026-07-29")
    _jobs({})
    out = wd._check_live("flies", _mcfg(), _MIDDAY, True)
    assert any(f.key == "flies.live_disarm" and f.status == OK for f in out)
    assert not (tmp_path / "halt-live.flag").exists()


def test_no_backstop_when_record_already_removed(monkeypatch, tmp_path):
    """Self-disarm worked: record gone → no halt flag, no CRITICAL."""
    _setup(monkeypatch, tmp_path, status_obj={"armed_for": None})
    _jobs({})
    out = wd._check_live("flies", _mcfg(), _PAST_DISARM, False)
    assert not any(f.status == CRITICAL for f in out)
    assert not (tmp_path / "halt-live.flag").exists()


def test_missing_armed_signal_mid_window_is_critical(monkeypatch, tmp_path):
    """Module status says armed-for-today but no record and no legacy task exists."""
    _setup(monkeypatch, tmp_path, status_obj={"armed_for": _TODAY})
    _jobs({})
    out = wd._check_live("flies", _mcfg(), _MIDDAY, True)
    task = next(f for f in out if f.key == "flies.live_task")
    assert task.status == CRITICAL and "no arm record" in task.message


def test_stale_legacy_task_still_trips_backstop_during_transition(monkeypatch, tmp_path):
    """Belt-and-braces: no arm record, but a leftover schtasks live task past disarm still sets
    the halt flag — deleting the record must not blind the backstop to a stale registration."""
    _setup(monkeypatch, tmp_path, status_obj={"armed_for": _TODAY})
    monkeypatch.setattr(wd.tasks, "exists", lambda name: True)
    _jobs({})
    out = wd._check_live("flies", _mcfg(), _PAST_DISARM, False)
    assert any(f.key == "flies.live_disarm" and f.status == CRITICAL for f in out)
    assert (tmp_path / "halt-live.flag").exists()


def test_supersnap_never_queried_when_heartbeat_absent(monkeypatch, tmp_path):
    """Pre-cutover boxes stay on the legacy branch (covered by test_watchdog_live.py) — the
    supervisor state files are not even consulted."""
    monkeypatch.setattr(wd.cfgmod, "module_root", lambda *a, **k: Path("."))
    monkeypatch.setattr(wd.cfgmod, "module_logs_dir", lambda name: tmp_path / "logs")
    monkeypatch.setattr(liveops, "halt_flag_path", lambda: tmp_path / "halt-live.flag")
    monkeypatch.setattr(wd.tasks, "exists", lambda name: False)
    monkeypatch.setattr(wd, "_run_module", lambda *a, **k: type("R", (), {"returncode": 1, "stdout": ""})())
    monkeypatch.setattr(
        supersnap, "job_state", lambda *a, **k: (_ for _ in ()).throw(AssertionError("registry read"))
    )
    out = wd._check_live("flies", _mcfg(), _MIDDAY, True)
    assert not any(f.key == "flies.live_supervisor" for f in out)


# --------------------------------------------------------------------------- unarmed and holding
_HOLDING = {
    "armed_for": None,
    "session_settled": False,
    "open_positions": 1,
    "pending_orders": [],
    "unmet_gates": [],
}


def test_an_unarmed_meic_loop_holding_positions_is_watched_for_freshness(monkeypatch, tmp_path):
    """Shown to fail with freshness gated on the arm alone: MEIC manages what it holds without an
    arm (2026-11-02), and a silent tick then leaves those positions unwatched."""
    _setup(monkeypatch, tmp_path, status_obj=_HOLDING)
    _jobs({"meic-live": {"enabled": True, "last_start": _iso_ago(20), "last_exit_code": 0}})
    out = wd._check_live("meic", _mcfg(), _MIDDAY, True)
    fresh = next(f for f in out if f.key == "meic.live_fresh")
    assert fresh.status == CRITICAL and "the live book holds positions" in fresh.message


def test_an_unarmed_flat_book_is_not_watched(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, status_obj={**_HOLDING, "session_settled": True, "open_positions": 0})
    _jobs({"meic-live": {"enabled": True, "last_start": _iso_ago(20), "last_exit_code": 0}})
    out = wd._check_live("meic", _mcfg(), _MIDDAY, True)
    assert not any(f.key == "meic.live_fresh" for f in out)


def test_holding_with_a_live_gate_unmet_is_critical(monkeypatch, tmp_path):
    """Shown to fail without (c7): the master switch off left positions unmanaged, and nothing said so."""
    status = {**_HOLDING, "pending_orders": ["P1"], "unmet_gates": ["enable_live_trading is false"]}
    _setup(monkeypatch, tmp_path, status_obj=status)
    out = wd._check_live("meic", _mcfg(), _MIDDAY, False)
    unmanaged = next(f for f in out if f.key == "meic.live_unmanaged")
    assert unmanaged.status == CRITICAL
    assert "1 open live position(s) and 1 pending order(s)" in unmanaged.message
    assert "enable_live_trading is false" in unmanaged.message


def test_an_alert_names_the_held_ic(monkeypatch, tmp_path):
    """Shown to fail without `_held`: the alert said how many, never which or what was left."""
    status = {**_HOLDING, "unmet_gates": ["x"], "open_ics": ["LIVE-SPX-14 SPX 7565p/7615c 10w, holds call"]}
    _setup(monkeypatch, tmp_path, status_obj=status)
    out = wd._check_live("meic", _mcfg(), _MIDDAY, False)
    message = next(f for f in out if f.key == "meic.live_unmanaged").message
    assert message.endswith("Held: LIVE-SPX-14 SPX 7565p/7615c 10w, holds call.")


def test_the_loops_critical_lines_are_announced_one_incident_each(monkeypatch, tmp_path):
    """Shown to fail without (c8): the loop's CRITICALs reached only its own log."""
    criticals = [
        {"at": "2026-07-30T14:22:05-04:00", "message": "CRITICAL: held legs unpriced"},
        {"at": "2026-07-30T14:22:05-04:00", "message": "CRITICAL: held legs unpriced"},  # logged twice
        {"at": "2026-07-30T14:23:05-04:00", "message": "CRITICAL: close order failed"},
    ]
    _setup(monkeypatch, tmp_path, status_obj={**_HOLDING, "criticals_today": criticals})
    found = next(f for f in wd._check_live("meic", _mcfg(), _MIDDAY, False) if f.key == "meic.live_critical")
    assert found.status == CRITICAL and len(found.members) == 2
    assert "14:23 CRITICAL: close order failed" in found.message
    _setup(monkeypatch, tmp_path, status_obj={**_HOLDING, "criticals_today": []})
    found = next(f for f in wd._check_live("meic", _mcfg(), _MIDDAY, False) if f.key == "meic.live_critical")
    assert found.status == OK


def test_gates_met_or_a_flat_book_is_not_unmanaged(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, status_obj=_HOLDING)
    out = wd._check_live("meic", _mcfg(), _MIDDAY, False)
    assert next(f for f in out if f.key == "meic.live_unmanaged").status == OK
    flat = {**_HOLDING, "session_settled": True, "open_positions": 0, "unmet_gates": ["x"]}
    _setup(monkeypatch, tmp_path, status_obj=flat)
    out = wd._check_live("meic", _mcfg(), _MIDDAY, False)
    assert next(f for f in out if f.key == "meic.live_unmanaged").status == OK
