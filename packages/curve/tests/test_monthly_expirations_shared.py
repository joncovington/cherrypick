"""The monthly-cycle expirations come from the suite's monthly expiry rule. The module carried its own
third-Friday arithmetic and holiday loop; this pins that copy against the shared rule for every month
of 2000-2040, and against the list it produced from a fixed date."""

from datetime import date, timedelta

from cherrypick.core import calendar as cal

from cherrypick.curve import clock


def _third_friday_the_old_way(year: int, month: int) -> date:
    d = date(year, month, 1)
    first_friday = d + timedelta(days=(4 - d.weekday()) % 7)
    return first_friday + timedelta(weeks=2)


def _expirations_the_old_way(today: date, months_ahead: int = 4) -> list[date]:
    out: list[date] = []
    year, month = today.year, today.month
    for _ in range(months_ahead + 1):
        exp = _third_friday_the_old_way(year, month)
        while not cal.is_trading_day(exp):
            exp -= timedelta(days=1)
        if exp > today:
            out.append(exp)
        month += 1
        if month > 12:
            month = 1
            year += 1
    return sorted(out)


def test_expirations_match_the_old_arithmetic_from_every_month_start_and_end():
    for year in range(2000, 2041):
        for month in range(1, 13):
            first = date(year, month, 1)
            for today in (first, first + timedelta(days=19), first + timedelta(days=27)):
                assert clock.monthly_expirations(today) == _expirations_the_old_way(today), today


def test_good_friday_pushes_the_expiry_to_thursday():
    # Good Friday 2022 fell on the third Friday of April (the 15th); the expiry was Thursday the 14th.
    assert date(2022, 4, 14) in clock.monthly_expirations(date(2022, 3, 25))
