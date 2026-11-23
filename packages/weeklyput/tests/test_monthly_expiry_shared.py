"""The module's monthly expiry is the suite's: the third Friday, or the trading day before it when
that Friday is a holiday. It carried its own copy of that rule; this pins the copy it replaced, over
every month of 2000-2040 (Good Friday and the Juneteenth and Independence Day Fridays included)."""

from datetime import date

from cherrypick.core import calendar as cal

from cherrypick.weeklyput import clock


def _the_copy_that_was_here(year: int, month: int) -> date:
    d = cal.nth_weekday(year, month, 4, 3)
    return d if cal.is_trading_day(d) else cal.previous_trading_day(d)


def test_monthly_expiry_matches_the_rule_it_carried_for_every_month():
    held = 0
    for year in range(2000, 2041):
        for month in range(1, 13):
            got = clock.monthly_expiry(year, month)
            assert got == _the_copy_that_was_here(year, month), (year, month)
            held += got.weekday() != 4  # a holiday Friday moved the expiry to Thursday
    assert held > 0  # the range does contain holiday Fridays, so the shifted branch was compared


def test_a_holiday_third_friday_moves_the_expiry_to_thursday():
    assert clock.monthly_expiry(2022, 4) == date(2022, 4, 14)  # Good Friday 2022 was the 15th
    assert clock.monthly_expiry(2023, 12) == date(2023, 12, 15)  # an ordinary third Friday
