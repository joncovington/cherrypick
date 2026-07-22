"""SLA heartbeats must be attributed to the module they belong to.

Regression cover for a bug that was invisible while Earnings was the only `cherrypick_scheduled`
module: both the dashboard and the watchdog read `earnings_entry.last.json` / `earnings_exit.last.json`
literally, so the moment a second scheduled module existed (flies), it would display Earnings' SLA
state as its own and raise a CRITICAL titled "Earnings paper entry did not run" for a missed flies run.
"""

import json

import pytest

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import watchdog

pytestmark = pytest.mark.unit


def _scheduled(name, **paper):
    return {"paper": {"kind": "cherrypick_scheduled", **paper}}


def test_state_files_derive_from_the_module_name():
    entry, exit_ = cfgmod.sla_state_files("flies", _scheduled("flies"))
    assert entry.name == "flies_entry.last.json"
    assert exit_.name == "flies_exit.last.json"


def test_earnings_paths_are_unchanged():
    """The existing module must keep the exact filenames it already writes — this fix generalizes the
    derivation, it does not migrate anyone."""
    entry, exit_ = cfgmod.sla_state_files("earnings", _scheduled("earnings"))
    assert entry.name == "earnings_entry.last.json"
    assert exit_.name == "earnings_exit.last.json"


def test_two_scheduled_modules_never_share_a_heartbeat():
    """The bug, stated directly."""
    earnings, _ = cfgmod.sla_state_files("earnings", _scheduled("earnings"))
    flies, _ = cfgmod.sla_state_files("flies", _scheduled("flies"))
    assert earnings != flies


def test_prefix_override_is_honored():
    entry, _ = cfgmod.sla_state_files("flies", _scheduled("flies", sla_state_prefix="butterflies"))
    assert entry.name == "butterflies_entry.last.json"


def test_missed_run_is_reported_against_the_right_module(monkeypatch, tmp_path):
    """A missed flies entry must not surface as an Earnings alert."""
    monkeypatch.setattr(cfgmod, "STATE_DIR", tmp_path)
    (tmp_path / "earnings_entry.last.json").write_text(
        json.dumps({"date": "2026-07-20", "ok": True}), encoding="utf-8")

    from datetime import datetime
    now = datetime(2026, 7, 20, 15, 0)
    findings = watchdog._check_earnings(
        "flies", {"paper": {"kind": "cherrypick_scheduled", "entry_time": "12:00"}}, now, True)

    sla = [f for f in findings if f.key == "flies.entry_sla"]
    assert sla, "the flies module should have produced its own SLA finding"
    assert "Earnings" not in sla[0].title, "flies must not be reported under Earnings' name"
    assert sla[0].title.startswith("Flies")
    # Earnings' healthy heartbeat must not make flies look healthy.
    assert sla[0].status == watchdog.CRITICAL


def test_healthy_heartbeat_is_named_for_its_own_module(monkeypatch, tmp_path):
    monkeypatch.setattr(cfgmod, "STATE_DIR", tmp_path)
    (tmp_path / "flies_entry.last.json").write_text(
        json.dumps({"date": "2026-07-20", "ok": True}), encoding="utf-8")

    from datetime import datetime
    findings = watchdog._check_earnings(
        "flies", {"paper": {"kind": "cherrypick_scheduled", "entry_time": "12:00"}},
        datetime(2026, 7, 20, 15, 0), True)

    sla = [f for f in findings if f.key == "flies.entry_sla"]
    assert sla and sla[0].status == watchdog.OK
    assert sla[0].title == "Flies paper entry"


# --------------------------------------------------------------------------- self_healing alert names
def test_self_healing_alerts_name_their_own_module(monkeypatch):
    """A missing flies task must not raise a CRITICAL titled for MEIC.

    Same fault as the SLA heartbeat naming, in a different function: `_check_meic` hardcoded "MEIC"
    in every alert title, so once a second self_healing module existed the operator was pointed at
    the wrong module by name.
    """
    monkeypatch.setattr(watchdog.tasks, "exists", lambda _n: False)
    mcfg = {"paper": {"kind": "self_healing", "task_name": "cherrypick-flies-paper-loop"},
            "path": "."}
    findings = watchdog._check_meic("flies", mcfg, in_session=False)

    task = next(f for f in findings if f.key == "flies.task")
    assert task.status == watchdog.CRITICAL
    assert "MEIC" not in task.title, "flies must not be reported under MEIC's name"
    assert task.title.startswith("Flies")
    assert "cherrypick-flies-paper-loop" in task.message


def test_meic_keeps_its_own_name(monkeypatch):
    """The existing module's alert text must be unchanged — this generalizes, it does not rename."""
    monkeypatch.setattr(watchdog.tasks, "exists", lambda _n: False)
    mcfg = {"paper": {"kind": "self_healing", "task_name": "cherrypick-meic-paper-loop"}, "path": "."}
    task = next(f for f in watchdog._check_meic("meic", mcfg, in_session=False)
                if f.key == "meic.task")
    assert task.title == "MEIC paper task missing"


def test_registered_task_reports_ok_under_its_own_name(monkeypatch):
    monkeypatch.setattr(watchdog.tasks, "exists", lambda _n: True)
    mcfg = {"paper": {"kind": "self_healing", "task_name": "cherrypick-flies-paper-loop"},
            "path": "."}
    task = next(f for f in watchdog._check_meic("flies", mcfg, in_session=False)
                if f.key == "flies.task")
    assert task.status == watchdog.OK and task.title == "Flies paper task"
