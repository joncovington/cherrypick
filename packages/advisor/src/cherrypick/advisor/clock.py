"""The session clock — ET, because the trading day is ET and nothing else is.

Every date in this package is a **session** date: the ET calendar day whose book a fact belongs to.
A checkpoint fired at 14:30 ET runs on a machine that may be in any timezone, and UTC rolls over
mid-afternoon ET — so deriving "today" from the local clock is how a pack ends up describing
tomorrow's empty book.

The next-session walk goes through `cherrypick.core.calendar`, the suite's NYSE calendar, so Friday
advice lands on Monday and nothing is ever issued for a holiday.
"""

from __future__ import annotations

from datetime import date, datetime

from cherrypick.core import calendar as _calendar

# See cherrypick.core.clock: one definition of "now, in ET" for the suite. `session_today` below is
# this module's own — it names the ET calendar day whether or not it is a trading day, which is a
# different question from the trading-session helpers under it.
from cherrypick.core.clock import ET, now_et  # noqa: F401


def session_today() -> str:
    """The ET calendar day, trading day or not. Callers that must not act on a non-trading day
    check :func:`is_trading_session` — this function answers "what day is it", nothing more."""
    return now_et().date().isoformat()


def is_trading_session(session: str) -> bool:
    return _calendar.is_trading_day(date.fromisoformat(session))


def next_session(session: str) -> str:
    """The next NYSE trading day after `session` — the one an advice artifact is written for."""
    return _calendar.next_trading_day(date.fromisoformat(session)).isoformat()


def previous_sessions(session: str, n: int) -> list[str]:
    """The `n` trading days before `session`, oldest first — the trend window."""
    out: list[date] = []
    cursor = date.fromisoformat(session)
    for _ in range(max(0, n)):
        cursor = _calendar.previous_trading_day(cursor)
        out.append(cursor)
    return [d.isoformat() for d in reversed(out)]


def sessions_between(start: str, end: str, *, cap: int) -> int:
    """Trading sessions strictly after `start` up to and including `end`, counting no further
    than `cap` -- how long an experiment has been on the calendar, as opposed to how many sessions
    it was actually enacted for."""
    try:
        cursor = date.fromisoformat(start)
        last = date.fromisoformat(end)
    except (TypeError, ValueError):
        return 0
    n = 0
    while n < cap:
        cursor = _calendar.next_trading_day(cursor)
        if cursor > last:
            break
        n += 1
    return n


def rth_close_hhmm(session: str) -> str:
    """The session's regular-hours close as HH:MM ET, from the suite calendar: 13:00 on a declared
    half day, 16:00 otherwise."""
    return _calendar.session_close_hhmm(date.fromisoformat(session))


def rth_close_iso(session: str) -> str:
    """The session's regular-hours close as an ET timestamp -- the moment a read of "the market at
    the close" should be taken at. Distinct from `end_of_session_iso` (23:59:59, an artifact's
    expiry): the regime recorder's last sample lands seconds before the bell, so a read clamped to
    end-of-day is hours stale by construction while one clamped here is seconds old."""
    hh, mm = (int(x) for x in rth_close_hhmm(session).split(":"))
    return (
        datetime.combine(date.fromisoformat(session), datetime.min.time(), tzinfo=ET)
        .replace(hour=hh, minute=mm)
        .isoformat()
    )


def end_of_session_iso(session: str) -> str:
    """23:59:59 ET on the target session — an advice artifact's `expires_at`.

    The artifact already names its one session and is re-validated against it; the expiry is the
    belt to that suspenders, and it is set at the END of the session so a loop that starts late (or
    a settlement pass that runs after the close) still reads the advice it ran the day under.
    """
    return datetime.combine(
        date.fromisoformat(session), datetime.max.time().replace(microsecond=0), tzinfo=ET
    ).isoformat()
