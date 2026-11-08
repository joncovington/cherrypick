"""The module's calendar: which expiries it sells into, the entry session, and the decision window.

The calendar is item 57's (docs/edge-hypotheses-plan.md), the sample the unhedged arm passed on:

- a week's expiry is its Friday, or the session before it when the Friday is a holiday;
- a **standard monthly** is the third Friday, or the session before it when that is a holiday,
  and is never sold here (it was item 46's sample);
- a weekly whose 7-day trade shares a session with a monthly's 7-day trade is left out, as the
  study left it out (a holiday Thursday expiry pulls its entry back into the monthly's week);
- the entry session is the first session on or after the expiry minus 7 calendar days.

`scripts/index_put_weekly_item57.py` holds the same rule for the backtest; the test
`test_clock.py::test_calendar_matches_item57` checks the two agree on every week from 2016-03 to
2026-10. A change here is a different rule from the one judged, so it is a declared boundary.

All pure: dates in, dates out. The ET clock is `cherrypick.core.clock`'s.
"""

from __future__ import annotations

from datetime import date, timedelta

from cherrypick.core import calendar as _cal
from cherrypick.core.clock import ET, hhmm_to_min, minute_of_day, now_et, now_iso, today_iso  # noqa: F401

TENOR_DAYS = 7

WINDOW_DEFAULTS = {
    "decision_minutes_before_close": 15,
    "decision_window_minutes": 10,
}


def monthly_expiry(year: int, month: int) -> date:
    """The standard monthly: the third Friday, or the trading day before it when that is a holiday."""
    d = _cal.nth_weekday(year, month, 4, 3)
    return d if _cal.is_trading_day(d) else _cal.previous_trading_day(d)


def is_monthly(expiry: date) -> bool:
    return expiry == monthly_expiry(expiry.year, expiry.month)


def expiry_for(friday: date) -> date:
    """A week's expiry: the Friday, or the session before it when the Friday is a holiday."""
    return friday if _cal.is_trading_day(friday) else _cal.previous_trading_day(friday)


def entry_session(expiry: date, tenor: int = TENOR_DAYS) -> date:
    """The first session on or after `expiry - tenor` calendar days."""
    d = expiry - timedelta(days=tenor)
    return d if _cal.is_trading_day(d) else _cal.next_trading_day(d)


def trade_window(expiry: date) -> tuple[date, date]:
    """The sessions a 7-day trade into `expiry` occupies, entry to expiry."""
    return entry_session(expiry), expiry


def overlaps(a: tuple[date, date], b: tuple[date, date]) -> bool:
    """Two trades overlap when one is still open after the other has been entered. A trade entered
    at the close of the session another expires is not open with it."""
    return a[0] < b[1] and b[0] < a[1]


def _months_around(d: date) -> list[date]:
    out = []
    for shift in (-1, 0, 1):
        y, m = d.year, d.month + shift
        if m == 0:
            y, m = y - 1, 12
        elif m == 13:
            y, m = y + 1, 1
        out.append(monthly_expiry(y, m))
    return out


def is_sample_weekly(expiry: date) -> bool:
    """A weekly this module sells into: not a standard monthly, and its trade shares no session with
    a monthly's 7-day trade."""
    if is_monthly(expiry):
        return False
    window = trade_window(expiry)
    return not any(overlaps(window, trade_window(m)) for m in _months_around(expiry))


def weekly_expiries(first: date, last: date) -> list[date]:
    """Every expiry in [first, last] this module sells into, in order."""
    d = first + timedelta(days=(4 - first.weekday()) % 7)  # the first Friday on or after `first`
    out: list[date] = []
    while d <= last + timedelta(days=7):
        e = expiry_for(d)
        if first <= e <= last and (not out or e != out[-1]) and is_sample_weekly(e):
            out.append(e)
        d += timedelta(days=7)
    return out


def cycle_entering(day: date) -> date | None:
    """The expiry whose entry session is `day`, or None. At most one: the expiries are a week apart."""
    for e in weekly_expiries(day + timedelta(days=1), day + timedelta(days=TENOR_DAYS + 4)):
        if entry_session(e) == day:
            return e
    return None


def next_cycle(day: date) -> date | None:
    """The first expiry this module sells into whose entry session is on or after `day`."""
    for e in weekly_expiries(day, day + timedelta(days=60)):
        if entry_session(e) >= day:
            return e
    return None


def years_to(session: date, expiry: date) -> float:
    """Item 46's time to expiry: calendar days over 365, from the entry session's date."""
    return (expiry - session).days / 365.0


def close_min(day: date) -> int:
    return hhmm_to_min(_cal.session_close_hhmm(day), 16 * 60)


def decision_window(day: date, params: dict | None = None) -> tuple[int, int]:
    """(first, last) minute of day in which an entry may be taken. The study entered at the entry
    session's end-of-day quotes; the paper arm enters in the last minutes before the close, 15:45 to
    15:55 by default (12:45 to 12:55 on an early close). A tick inside the window that cannot act
    retries on the next tick; a window that closes unacted records a miss, never a late fill."""
    p = {**WINDOW_DEFAULTS, **{k: v for k, v in (params or {}).items() if k in WINDOW_DEFAULTS}}
    start = close_min(day) - int(p["decision_minutes_before_close"])
    return start, start + int(p["decision_window_minutes"])
