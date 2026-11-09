"""Windows Update's automatic-restart window against the suite's day (2026-10-09). Read-only advice for
`doctor` and `install`; it never changes Windows settings."""

from __future__ import annotations

from cherrypick.orchestrator import winupdate as w

DAY = ["05:30", "07:00", "09:30", "16:30", "19:30"]  # the suite's ET day: 05:30 .. 20:00


def test_the_registry_schedule_is_read_and_overnight_maintenance_does_not_count():
    jobs = {
        "report-edition": {"schedule": "daily 07:00 ET, trading days", "enabled": True},
        "log-archive": {"schedule": "monthly day 1 03:30 ET", "enabled": True},
        "suite-backup": {"schedule": "daily 01:30 ET", "enabled": True},
        "x-monthly": {"schedule": "monthly day 2 04:15 ET", "enabled": True},
        "off": {"schedule": "daily 02:00 ET", "enabled": False},
        "ticker": {"schedule": "every 60s", "enabled": True},
    }
    assert sorted(w.job_times(jobs)) == ["04:15", "07:00"]
    assert w.protected_span_et(DAY) == (330, 1200)


def test_hours_that_cover_the_day_are_ok_and_the_windows_default_is_not():
    # This PC on Mountain time: ET is 2 hours ahead.
    ok = w.check(DAY, active={"start": 3, "end": 21, "smart": False}, et_minus_local_h=2)
    assert ok["status"] == "ok"
    default = w.check(DAY, active={"start": 7, "end": 0, "smart": False}, et_minus_local_h=2)
    assert default["status"] == "warn" and default["exposed_et"][:2] == ["05:00", "06:00"]
    assert "Manually, 03:00 to 19:00" in default["detail"]  # the minimum: 05:00-21:00 ET


def test_wrapping_hours_and_smart_hours():
    assert w.exposed_minutes((330, 1200), {"start": 22, "end": 21}, 0) == []  # 22:00 -> 21:00 next day
    assert w.exposed_minutes((330, 1200), {"start": 22, "end": 20}, 0) == [1200]  # the 20:00 hour is out
    assert w.exposed_minutes((330, 1200), {"start": 9, "end": 9}, 0) == []  # equal: all day
    smart = w.check(DAY, active={"start": 3, "end": 21, "smart": True}, et_minus_local_h=2)
    assert smart["status"] == "warn" and "adjust automatically" in smart["detail"]
    assert w.check(DAY, active=None)["status"] in ("unknown", "ok", "warn")  # this machine, whatever it says


def test_a_job_with_several_times_counts_every_one():
    jobs = {
        "manage-eval": {"schedule": "daily 10:00, 13:00, 16:35 ET, trading days", "enabled": True},
        "report-edition": {"schedule": "daily 07:00 ET, trading days", "enabled": True},
    }
    assert sorted(w.job_times(jobs)) == ["07:00", "10:00", "13:00", "16:35"]
    assert w.protected_span_et(w.job_times(jobs)) == (420, 16 * 60 + 35 + 30)


def test_times_from_specs_reads_every_time_of_a_slotted_job():
    from cherrypick.orchestrator import jobspec

    slotted = jobspec.JobSpec(
        id="m", argv=("py",), kind="daily", times_et=("07:45", "18:45"), catchup_minutes=10
    )
    single = jobspec.JobSpec(id="s", argv=("py",), kind="daily", at_et="09:00", catchup_minutes=10)
    assert w.times_from_specs([slotted, single]) == ["07:45", "18:45", "09:00"]
