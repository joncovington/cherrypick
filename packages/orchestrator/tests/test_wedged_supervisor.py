"""A supervisor alive but not beating (item 9 of the 2026-10-08 audit) holds its lock, so every
restart the anchor tried refused and the suite stayed down. It is ended -- only when the PID is
provably the process that wrote the heartbeat."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cherrypick.orchestrator import supersnap

STARTED = datetime(2026, 10, 8, 9, 0, tzinfo=timezone.utc)


def hb(age_s):
    return {
        "pid": 4242,
        "ts": (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat(),
        "started_at": STARTED.isoformat(),
    }


def created(offset_s):
    return lambda pid: STARTED.timestamp() + offset_s


def alive(pid):
    return True


def test_a_long_silent_supervisor_that_wrote_the_heartbeat_is_named():
    assert supersnap.wedged_supervisor_pid(hb(900), start_time_fn=created(-3), alive_fn=alive) == 4242


def test_never_a_slow_one_a_dead_one_a_reused_pid_or_an_unknown_one():
    assert supersnap.wedged_supervisor_pid(hb(120), start_time_fn=created(-3), alive_fn=alive) is None
    assert (
        supersnap.wedged_supervisor_pid(hb(900), start_time_fn=created(-3), alive_fn=lambda p: False) is None
    )
    # created after the heartbeat's supervisor started: the number was reused by something else
    assert supersnap.wedged_supervisor_pid(hb(900), start_time_fn=created(3600), alive_fn=alive) is None
    assert supersnap.wedged_supervisor_pid(hb(900), start_time_fn=lambda p: None, alive_fn=alive) is None
    assert supersnap.wedged_supervisor_pid(None, start_time_fn=created(-3), alive_fn=alive) is None


def test_disk_space_warns_low_and_is_critical_almost_full():
    from cherrypick.orchestrator import watchdog as wd

    gb = 1024**3
    assert wd._disk_finding(50 * gb, 500 * gb, 10, 2, "C:").status == wd.OK
    assert wd._disk_finding(8 * gb, 500 * gb, 10, 2, "C:").status == wd.WARN
    f = wd._disk_finding(1 * gb, 500 * gb, 10, 2, "C:")
    assert f.status == wd.CRITICAL and f.key == "disk.free" and "1.0 GB free of 500 GB" in f.message
