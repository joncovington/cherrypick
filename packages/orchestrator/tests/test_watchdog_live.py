"""The live-loop watchdog check (`_check_live`): armed-window task presence, in-session log
freshness, live settle-overdue, and the disarm backstop that sets the suite halt flag when a
live task survives past its self-disarm window (the dead-man's switch's second layer).
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from cherrypick.orchestrator import liveops
from cherrypick.orchestrator import watchdog as wd
from cherrypick.orchestrator.watchdog import CRITICAL, OK, WARN  # noqa: F401

pytestmark = pytest.mark.unit

_TODAY = "2026-07-30"
_MIDDAY = datetime(2026, 7, 30, 11, 0)
_AFTER_CLOSE = datetime(2026, 7, 30, 16, 45)
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


def _setup(monkeypatch, tmp_path, *, status_obj, registered):
    monkeypatch.setattr(wd.cfgmod, "module_root", lambda *a, **k: Path("."))
    monkeypatch.setattr(wd.cfgmod, "module_logs_dir", lambda name: tmp_path / "logs")
    monkeypatch.setattr(wd.tasks, "exists", lambda name: registered)
    monkeypatch.setattr(liveops, "halt_flag_path", lambda: tmp_path / "halt-live.flag")

    class _R:
        returncode = 0
        stdout = json.dumps(status_obj) if status_obj is not None else ""

    monkeypatch.setattr(wd, "_run_module", lambda *a, **k: _R())


def _touch_log(tmp_path, age_seconds=0, now=_MIDDAY):
    # Ages are measured against the CHECK's `now_et`, which these tests fake — so the mtime
    # must anchor to that fake now, not the machine's real clock.
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    p = log_dir / "flies_live.log"
    p.write_text("tick")
    import os

    ts = now.timestamp() - age_seconds
    os.utime(p, (ts, ts))
    return p


def test_no_live_block_means_no_findings(monkeypatch):
    assert wd._check_live("flies", {"paper": {}}, _MIDDAY, True) == []


def test_fresh_armed_live_loop_is_ok(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, status_obj={"armed_for": _TODAY}, registered=True)
    _touch_log(tmp_path, age_seconds=30)
    out = wd._check_live("flies", _mcfg(), _MIDDAY, True)
    fresh = [f for f in out if f.key == "flies.live_fresh"]
    assert fresh and fresh[0].status == OK


def test_silent_armed_live_loop_is_critical(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, status_obj={"armed_for": _TODAY}, registered=True)
    _touch_log(tmp_path, age_seconds=15 * 60)  # 15 min old vs 5-min freshness
    out = wd._check_live("flies", _mcfg(), _MIDDAY, True)
    fresh = [f for f in out if f.key == "flies.live_fresh"]
    assert fresh and fresh[0].status == CRITICAL
    assert "resting unwatched" in fresh[0].message


def test_missing_task_mid_window_is_critical(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, status_obj={"armed_for": _TODAY}, registered=False)
    out = wd._check_live("flies", _mcfg(), _MIDDAY, True)
    task = [f for f in out if f.key == "flies.live_task"]
    assert task and task[0].status == CRITICAL


def test_disarm_backstop_sets_halt_flag_and_criticals(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, status_obj={"armed_for": _TODAY}, registered=True)
    out = wd._check_live("flies", _mcfg(), _PAST_DISARM, False)
    disarm = [f for f in out if f.key == "flies.live_disarm"]
    assert disarm and disarm[0].status == CRITICAL
    assert (tmp_path / "halt-live.flag").exists()  # the risk-reducing remediation actually happened


def test_disarm_backstop_fires_on_stale_arm_date_even_mid_morning(monkeypatch, tmp_path):
    # Yesterday's arm surviving into today (machine slept through 17:00): the backstop must not
    # wait for today's disarm time to pass.
    _setup(monkeypatch, tmp_path, status_obj={"armed_for": "2026-07-29"}, registered=True)
    out = wd._check_live("flies", _mcfg(), _MIDDAY, True)
    disarm = [f for f in out if f.key == "flies.live_disarm"]
    assert disarm and disarm[0].status == CRITICAL
    assert (tmp_path / "halt-live.flag").exists()


def test_no_backstop_when_task_already_disarmed(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, status_obj={"armed_for": _TODAY}, registered=False)
    out = wd._check_live("flies", _mcfg(), _PAST_DISARM, False)
    assert [f for f in out if f.key == "flies.live_disarm"] == []
    assert not (tmp_path / "halt-live.flag").exists()


def test_live_settle_overdue_warns(monkeypatch, tmp_path):
    _setup(
        monkeypatch,
        tmp_path,
        status_obj={"armed_for": _TODAY, "session_settled": False, "open_positions": 2},
        registered=False,
    )
    out = wd._check_live("flies", _mcfg(), _AFTER_CLOSE, False)
    settle = [f for f in out if f.key == "flies.live_settle_overdue"]
    assert settle and settle[0].status == WARN
    assert "2 open live position" in settle[0].message


def test_orphaned_orders_are_critical_any_time(monkeypatch, tmp_path):
    _setup(
        monkeypatch,
        tmp_path,
        status_obj={"armed_for": _TODAY, "orphaned_orders": 2},
        registered=False,
    )
    out = wd._check_live("flies", _mcfg(), _AFTER_CLOSE, False)
    orphans = [f for f in out if f.key == "flies.live_orphans"]
    assert orphans and orphans[0].status == CRITICAL
    assert "2 working order(s)" in orphans[0].message


def test_zero_orphans_reports_ok_so_a_prior_alert_recovers(monkeypatch, tmp_path):
    _setup(
        monkeypatch,
        tmp_path,
        status_obj={"armed_for": _TODAY, "orphaned_orders": 0},
        registered=False,
    )
    out = wd._check_live("flies", _mcfg(), _MIDDAY, True)
    orphans = [f for f in out if f.key == "flies.live_orphans"]
    assert orphans and orphans[0].status == OK


def test_live_settled_reports_ok(monkeypatch, tmp_path):
    _setup(
        monkeypatch,
        tmp_path,
        status_obj={"armed_for": _TODAY, "session_settled": True, "open_positions": 0},
        registered=False,
    )
    out = wd._check_live("flies", _mcfg(), _AFTER_CLOSE, False)
    settle = [f for f in out if f.key == "flies.live_settle_overdue"]
    assert settle and settle[0].status == OK
