"""Quiet-hours cadence and the trading-day gate on spawn-per-tick paper jobs (2026-10-12).

Every interval job ran at its session cadence around the clock: ~18,000 process launches on a Sunday
that traded nothing. Fake clocks throughout; 2026-08-10 is a Monday, 08-08 a Saturday.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from test_jobspec import derive, suite_cfg  # pytest puts tests/ on sys.path (no package)

from cherrypick.orchestrator import jobspec
from cherrypick.orchestrator.jobspec import JobSpec

pytestmark = pytest.mark.unit

ET = ZoneInfo("America/New_York")


def et(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=ET)


def quiet_spec(**kw):
    base = dict(
        id="j",
        argv=("py", "x"),
        kind=jobspec.KIND_INTERVAL,
        interval_seconds=30,
        quiet_interval_seconds=300,
        active_start="09:00",
        active_end="17:00",
    )
    base.update(kw)
    return JobSpec(**base)


def _launches(spec, start, hours, holidays=None):
    """How many times the job would start over `hours`, evaluated every second like the daemon."""
    state: dict = {}
    n, t, end = 0, start, start + timedelta(hours=hours)
    while t < end:
        fire, _, patch = jobspec.should_start(spec, state, t, holidays)
        state.update(patch)
        n += bool(fire)
        t += timedelta(seconds=1)
    return n


def test_active_hours_keep_the_fast_cadence():
    spec = quiet_spec()
    assert jobspec.effective_interval(spec, et(2026, 8, 10, 12, 0)) == 30
    assert _launches(spec, et(2026, 8, 10, 12, 0), 1) == 120


def test_overnight_and_weekends_run_at_the_quiet_cadence():
    spec = quiet_spec()
    assert jobspec.effective_interval(spec, et(2026, 8, 10, 20, 0)) == 300
    assert jobspec.effective_interval(spec, et(2026, 8, 8, 12, 0)) == 300  # Saturday noon
    assert _launches(spec, et(2026, 8, 8, 0, 0), 24) == 288


def test_a_holiday_is_quiet_all_day():
    spec = quiet_spec()
    assert jobspec.is_quiet(spec, et(2026, 7, 3, 12, 0), holidays={"2026-07-03"})


def test_a_quiet_wait_never_delays_the_first_active_run():
    # Booked at 08:58 for 09:03 by the quiet cadence; the active window starts at 09:00 and must
    # not wait out the rest of a five-minute gap.
    spec = quiet_spec()
    state: dict = {}
    _, _, patch = jobspec.should_start(spec, state, et(2026, 8, 10, 8, 58))
    state.update(patch)
    assert not jobspec.should_start(spec, state, et(2026, 8, 10, 8, 59))[0]
    fire, reason, _ = jobspec.should_start(spec, state, et(2026, 8, 10, 9, 0))
    assert fire, reason


def test_a_day_of_launches_before_and_after():
    # trade-notify's own numbers: 2,880 a day at 30 s before; 288 on a Sunday after, and on a
    # trading day 960 inside 09:00-17:00 plus 192 outside.
    spec = quiet_spec()
    assert _launches(spec, et(2026, 8, 9, 0, 0), 24) == 288
    weekday = _launches(spec, et(2026, 8, 10, 0, 0), 24)
    assert 1140 <= weekday <= 1160


def test_quiet_must_not_be_faster_than_active():
    with pytest.raises(ValueError):
        quiet_spec(quiet_interval_seconds=10)
    with pytest.raises(ValueError):
        quiet_spec(active_end=None)


def test_the_notify_and_service_jobs_declare_a_quiet_cadence():
    jobs, errors = derive(suite_cfg())
    by_id = {j.id: j for j in jobs}
    for job_id in ("trade-notify", "desk-notify", "power-watch", "earnings-dolt"):
        assert by_id[job_id].quiet_interval_seconds, job_id
    assert by_id["watchdog"].quiet_interval_seconds is None


def test_spawn_per_tick_paper_jobs_run_on_trading_days_only():
    jobs, _ = derive(suite_cfg())
    by_id = {j.id: j for j in jobs}
    meic = by_id["meic-paper"]
    assert meic.kind == jobspec.KIND_INTERVAL and meic.trading_days_only
    assert not jobspec.should_start(meic, {}, et(2026, 8, 8, 12, 0))[0]  # Saturday
    assert jobspec.should_start(meic, {}, et(2026, 8, 10, 23, 0))[0], "overnight on a trading day still ticks"
    # The resident module's off-session tick is not this job and keeps its own shape.
    assert not by_id["flies-paper-offsession"].trading_days_only


def test_a_module_can_keep_ticking_every_day():
    cfg = suite_cfg()
    cfg["modules"]["meic"]["paper"]["tick_on_non_trading_days"] = True
    jobs, _ = derive(cfg)
    assert not {j.id: j for j in jobs}["meic-paper"].trading_days_only
