"""`doctor`'s coverage of the orchestrator's OWN recurring tasks.

Before this, doctor verified module tasks, the dolt keep-alive and the watchdog — but not
trade-notify, log-archive, reconcile, the Follow Feed notifier, or the pre-open check. A green
`doctor` therefore meant "the paper pipeline is registered", not "the suite is".

The distinction these tests exist to protect: **off-by-choice is healthy, missing is not.** Several
of these tasks are opt-in, and a check that reports "not registered" as a warning for a feature
deliberately switched off is a check the operator learns to ignore.
"""

import pytest

from cherrypick.orchestrator import doctor

pytestmark = pytest.mark.unit


def _registered(*names):
    """Stub `tasks.exists` with an explicit set of registered task names."""
    return lambda name: name in set(names)


def _by_name(checks):
    return {c.name: c for c in checks}


def test_every_orchestrator_task_is_reported(monkeypatch):
    """All five, resolved through the same settings helpers install/uninstall use — a config-driven
    rename cannot desync the check from what was registered."""
    monkeypatch.setattr(doctor.tasks, "exists", _registered())
    cfg = {"trade_notify": {"task_name": "cherrypick-trade-notify"}}
    names = set(_by_name(doctor._suite_task_checks(cfg)))
    assert names == {
        "task.trade_notify",
        "task.log_archive",
        "task.reconcile",
        "task.preopen",
    }


def test_enabled_and_registered_is_ok(monkeypatch):
    monkeypatch.setattr(
        doctor.tasks,
        "exists",
        _registered("cherrypick-trade-notify", "cherrypick-log-archive", "cherrypick-preopen"),
    )
    checks = _by_name(doctor._suite_task_checks({"trade_notify": {"task_name": "cherrypick-trade-notify"}}))
    assert checks["task.trade_notify"].status == doctor.OK
    assert checks["task.log_archive"].status == doctor.OK
    assert checks["task.preopen"].status == doctor.OK


def test_enabled_but_missing_warns(monkeypatch):
    """The real-world shape of this failure is a task left against a stale checkout path after a
    move, or an `install` that partly failed — not one that was never created."""
    monkeypatch.setattr(doctor.tasks, "exists", _registered())
    checks = _by_name(doctor._suite_task_checks({"trade_notify": {"task_name": "cherrypick-trade-notify"}}))
    assert checks["task.trade_notify"].status == doctor.WARN
    assert "not registered" in checks["task.trade_notify"].detail
    assert "cherrypick-trade-notify" in checks["task.trade_notify"].detail  # names the task to fix


def test_opted_out_and_absent_is_healthy_not_a_warning(monkeypatch):
    """`reconcile` and the Follow Feed notifier are off by default. Reporting a deliberate choice as
    a warning is how a section becomes noise — the same way `holidays_loaded=0` sat in the docs as
    an accepted gap until it turned out to be a live break."""
    monkeypatch.setattr(doctor.tasks, "exists", _registered())
    checks = _by_name(doctor._suite_task_checks({}))
    assert checks["task.reconcile"].status == doctor.OK
    assert checks["task.reconcile"].detail == "disabled (not registered)"


def test_disabled_but_still_registered_is_drift(monkeypatch):
    """The original live example was follow_feed, switched off because a standalone repo ran
    it — since 2026-08-21 the whole feature lives out there, so reconcile plays the part. A
    leftover task is benign — the command re-reads config and no-ops — but it is drift, and
    drift nobody can see is what this whole check is for."""
    monkeypatch.setattr(doctor.tasks, "exists", _registered("cherrypick-reconcile"))
    checks = _by_name(doctor._suite_task_checks({}))
    assert checks["task.reconcile"].status == doctor.WARN
    assert "still registered" in checks["task.reconcile"].detail


def test_a_task_with_no_configured_name_is_skipped(monkeypatch):
    """trade_notify has no settings helper and no default name — an absent block means the feature
    was never configured, which is not the same as being switched off."""
    monkeypatch.setattr(doctor.tasks, "exists", _registered())
    assert "task.trade_notify" not in _by_name(doctor._suite_task_checks({}))


def test_checks_reach_the_real_doctor_report(monkeypatch):
    """Wired into `run`, not just callable in isolation."""
    monkeypatch.setattr(doctor.tasks, "exists", _registered())
    names = {c.name for c in doctor.run({"modules": {}}, fast=True)}
    assert "task.preopen" in names and "task.reconcile" in names
