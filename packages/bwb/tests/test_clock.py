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


def test_weekly_fridays_includes_the_monthly_date_as_a_pm_weekly():
    """The third-Friday date carries a PM weekly beside the AM monthly; the OCC-root filter in the
    provider keeps the AM series out, so the calendar must not drop the date (2026-09-12)."""
    today = clock.now_et().date()
    weeklies = clock.weekly_fridays(today, weeks_ahead=12)
    assert any(clock.is_third_friday_monthly(d) for d in weeklies)
    assert len(weeklies) == 12


def test_weekly_fridays_are_all_strictly_after_today_and_trading_days():
    today = clock.now_et().date()
    weeklies = clock.weekly_fridays(today, weeks_ahead=8)
    assert weeklies  # the real NYSE calendar never produces an empty stretch
    for d in weeklies:
        assert d > today
        assert _cal.is_trading_day(d)


def test_target_expiration_lands_on_the_monthly_date_as_the_pm_weekly_and_says_so():
    """When the next Friday is the third Friday, the plan lands ON it -- the PM weekly listed
    there -- and flags `am_monthly_date` so those weeks can be told apart in a read."""
    today = clock.now_et().date()
    third_friday = _cal.nth_weekday(today.year, today.month, _cal.FRI, 3)
    if third_friday <= today:
        third_friday = _cal.nth_weekday(today.year, today.month + 1 if today.month < 12 else 1, _cal.FRI, 3)
    # Enter on the Monday of the monthly week, so the next Friday after "today" is the monthly date.
    plan = clock.target_expiration(third_friday - timedelta(days=4))
    assert plan is not None
    assert plan["expiration"] == third_friday.isoformat()
    assert plan["pm_settled"] is True
    assert plan["am_monthly_date"] is True


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


def test_weekly_fridays_walks_every_week_not_just_the_first():
    """2026-09-11 (a Friday; the 18th is the AM monthly). The old cursor stepped to the Saturday
    after each Friday, and a Saturday's "this week's Friday" is the same Friday again, so the walk
    never left week one: at most one date, and none at all when that one was the excluded monthly.
    Every book refused `no_expiration_plan` that day and would have all the following week."""
    from datetime import date

    weeklies = clock.weekly_fridays(date(2026, 9, 11), weeks_ahead=8)
    assert [d.isoformat() for d in weeklies] == [
        "2026-09-18",  # the third Friday: traded on its PM weekly
        "2026-09-25",
        "2026-10-02",
        "2026-10-09",
        "2026-10-16",
        "2026-10-23",
        "2026-10-30",
        "2026-11-06",
    ]
    # A Friday entry lands on the following week's Friday, 7 DTE, monthly week or not.
    assert clock.target_expiration(date(2026, 9, 11)) == {
        "expiration": "2026-09-18",
        "dte": 7,
        "pm_settled": True,
        "am_monthly_date": True,
    }


def test_monday_through_thursday_target_that_weeks_friday_and_friday_the_next():
    """The rule as it has always been recorded: the next Friday strictly after today. The week of
    the 14th targets the 18th (the monthly date, on its PM weekly) and Friday the 18th targets the
    25th. `dte_target` does not enter into it -- it is reserved for the backlog experiment."""
    from datetime import date

    picks = {d: clock.target_expiration(date(2026, 9, d), {"dte_target": 7})["expiration"] for d in range(14, 19)}
    assert picks == {14: "2026-09-18", 15: "2026-09-18", 16: "2026-09-18", 17: "2026-09-18", 18: "2026-09-25"}
    # And the week before, matching what the ledger recorded for the 4th through the 10th.
    picks = {d: clock.target_expiration(date(2026, 9, d))["expiration"] for d in (4, 8, 9, 10)}
    assert picks == {4: "2026-09-11", 8: "2026-09-11", 9: "2026-09-11", 10: "2026-09-11"}
