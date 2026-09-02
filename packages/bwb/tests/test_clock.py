from datetime import date, timedelta

from cherrypick.core import calendar as _cal

from cherrypick.bwb import clock


def test_is_third_friday_monthly_true_on_the_real_third_friday():
    today = clock.now_et().date()
    third_friday = _cal.nth_weekday(today.year, today.month, _cal.FRI, 3)
    assert clock.is_third_friday_monthly(third_friday) is True


def test_is_third_friday_monthly_false_on_an_ordinary_weekly():
    today = clock.now_et().date()
    third_friday = _cal.nth_weekday(today.year, today.month, _cal.FRI, 3)
    other_friday = third_friday + timedelta(days=7)  # the following weekly, same month or next
    assert clock.is_third_friday_monthly(other_friday) is False


def test_weekly_fridays_never_includes_the_monthly_date():
    today = clock.now_et().date()
    weeklies = clock.weekly_fridays(today, weeks_ahead=12)
    for d in weeklies:
        assert not clock.is_third_friday_monthly(d)


def test_weekly_fridays_are_all_strictly_after_today_and_trading_days():
    today = clock.now_et().date()
    weeklies = clock.weekly_fridays(today, weeks_ahead=8)
    assert weeklies  # the real NYSE calendar never produces an empty stretch
    for d in weeklies:
        assert d > today
        assert _cal.is_trading_day(d)


def test_target_expiration_shifts_off_the_am_monthly():
    """When the nearest-to-target date would be the AM-settled third-Friday monthly, the plan must
    land on a DIFFERENT (PM weekly) date — computed relative to the real calendar, never pinned."""
    today = clock.now_et().date()
    third_friday = _cal.nth_weekday(today.year, today.month, _cal.FRI, 3)
    if third_friday <= today:
        third_friday = _cal.nth_weekday(today.year, today.month + 1 if today.month < 12 else 1, _cal.FRI, 3)
    dte_of_monthly = (third_friday - today).days
    plan = clock.target_expiration(today, {"dte_target": dte_of_monthly})
    assert plan is not None
    assert plan["expiration"] != third_friday.isoformat()
    assert not clock.is_third_friday_monthly(date.fromisoformat(plan["expiration"]))


def test_target_expiration_dte_matches_the_returned_date():
    today = clock.now_et().date()
    plan = clock.target_expiration(today, {"dte_target": 7})
    assert plan is not None
    assert plan["dte"] == (date.fromisoformat(plan["expiration"]) - today).days
    assert plan["pm_settled"] is True


# --------------------------------------------------------------------------- tie-break toward longer
def test_nearest_breaks_ties_toward_the_longer_date():
    today = clock.now_et().date()
    shorter = today + timedelta(days=6)
    longer = today + timedelta(days=8)
    # Both are distance 1 from a dte_target of 7 -- the longer one must win.
    chosen = clock._nearest([shorter, longer], today, dte_target=7)
    assert chosen == longer


def test_nearest_picks_the_closer_date_when_not_tied():
    today = clock.now_et().date()
    near = today + timedelta(days=7)
    far = today + timedelta(days=10)
    chosen = clock._nearest([near, far], today, dte_target=7)
    assert chosen == near
