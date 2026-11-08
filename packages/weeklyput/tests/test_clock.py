from datetime import date

from conftest import load_script

from cherrypick.weeklyput import clock


def test_calendar_matches_item57():
    """The module sells into exactly the weeks item 57 judged: same expiries, same entry sessions."""
    i57 = load_script("index_put_weekly_item57")
    theirs = i57.weekly_expiries()
    ours = clock.weekly_expiries(i57.FIRST_EXPIRY, i57.LAST_EXPIRY)
    assert ours == theirs
    assert [clock.entry_session(e) for e in ours] == [i57.i46.entry_session(e, 7) for e in theirs]
    assert len(ours) > 400


def test_monthly_is_never_sold():
    assert clock.is_monthly(date(2026, 11, 20))
    assert not clock.is_sample_weekly(date(2026, 11, 20))
    assert clock.is_sample_weekly(date(2026, 11, 13))
    assert clock.entry_session(date(2026, 11, 13)) == date(2026, 11, 6)


def test_holiday_friday_moves_the_expiry_to_thursday():
    # Good Friday 2026-04-03: that week's expiry is Thursday 04-02.
    assert clock.expiry_for(date(2026, 4, 3)) == date(2026, 4, 2)


def test_cycle_entering():
    assert clock.cycle_entering(date(2026, 11, 6)) == date(2026, 11, 13)
    # 11-13's session enters the week of 11-20, a monthly: nothing.
    assert clock.cycle_entering(date(2026, 11, 13)) is None
    assert clock.cycle_entering(date(2026, 11, 10)) is None
    assert clock.next_cycle(date(2026, 11, 9)) == date(2026, 11, 27)


def test_decision_window_follows_an_early_close():
    assert clock.decision_window(date(2026, 11, 6)) == (15 * 60 + 45, 15 * 60 + 55)
    # The day after Thanksgiving closes at 13:00.
    assert clock.decision_window(date(2026, 11, 27)) == (12 * 60 + 45, 12 * 60 + 55)
