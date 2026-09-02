"""The expiration plan: DTE windows, holiday shifts, and date-boundary stability."""

from datetime import date

from cherrypick.pmcc import clock


def test_plan_midweek_hits_short_target():
    # Wednesday 2026-08-19: the nearest Friday (08-21) is only 2 DTE, below the [5, 9] short
    # window, so the SECOND Friday (08-28, 9 DTE -- the window's own max) is taken. Long: 09-04 is
    # 16 DTE (below the [17, 25] floor), so 09-11 at 23 DTE (nearest 21) wins.
    plan = clock.expiration_plan(date(2026, 8, 19))
    assert plan is not None
    assert plan["short_expiration"] == "2026-08-28"
    assert plan["short_dte"] == 9
    assert plan["long_expiration"] == "2026-09-11"
    assert plan["long_dte"] == 23
    assert plan["long_expiration"] > plan["short_expiration"]


def test_plan_thursday_hits_short_target():
    # Thursday 2026-08-20: the nearest Friday (08-21) is 1 DTE, below the window, so 08-28 at 8 DTE
    # (nearest the 7-day target) is taken.
    plan = clock.expiration_plan(date(2026, 8, 20))
    assert plan["short_expiration"] == "2026-08-28"
    assert plan["short_dte"] == 8


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
