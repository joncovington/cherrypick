"""The expiration plan: DTE windows, holiday shifts, and date-boundary stability."""

from datetime import date

from cherrypick.pmcc import clock


def test_plan_ordinary_monday():
    # Monday 2026-08-17: short should land Fri 2026-08-28 (11 DTE — the soonest Friday inside
    # [6, 12]; 08-21 is only 4 days out), long Fri 2026-09-04 (18 DTE, nearest 21 inside [17, 25]).
    plan = clock.expiration_plan(date(2026, 8, 17))
    assert plan is not None
    assert plan["short_expiration"] == "2026-08-28"
    assert plan["long_expiration"] == "2026-09-04"
    assert plan["short_dte"] == 11
    assert plan["long_dte"] == 18
    assert plan["long_expiration"] > plan["short_expiration"]


def test_plan_midweek_hits_target_dtes():
    # Wednesday 2026-08-19: Fri 08-28 is 9 DTE (the target exactly); long Fri 09-11 is 23 DTE
    # against 09-04's 16 (outside [17, 25]), so 09-11 wins.
    plan = clock.expiration_plan(date(2026, 8, 19))
    assert plan["short_expiration"] == "2026-08-28"
    assert plan["short_dte"] == 9
    assert plan["long_expiration"] == "2026-09-11"
    assert plan["long_dte"] == 23


def test_plan_is_date_stable():
    # The plan takes a DATE, so within one day it cannot change — the stream request depends on it.
    assert clock.expiration_plan(date(2026, 8, 19)) == clock.expiration_plan(date(2026, 8, 19))


def test_holiday_friday_shifts_back():
    # Good Friday 2027-03-26: that week's weekly expiration shifts to Thursday 03-25.
    exp = clock.weekly_expiration(date(2027, 3, 22), 0)
    assert exp == date(2027, 3, 25)


def test_labor_day_week_keeps_friday():
    # Labor Day Mon 2026-09-07 does not move the Friday.
    exp = clock.weekly_expiration(date(2026, 9, 8), 0)
    assert exp == date(2026, 9, 11)


def test_roll_expiration_capped_by_long():
    target = clock.roll_expiration(date(2026, 8, 24), "2026-09-04")
    assert target is not None
    assert target["expiration"] <= "2026-09-04"
    # Nearest to the 9-day target among eligible Fridays (08-28 = 4d, 09-04 = 11d) -> 09-04.
    assert target["expiration"] == "2026-09-04"


def test_roll_expiration_none_when_long_passed():
    assert clock.roll_expiration(date(2026, 9, 7), "2026-09-04") is None
