"""The clock times of one trading session, as a pure function of the date.

A normal NYSE session closes at 16:00 ET and an early-close ("half") day at 13:00
(`cherrypick.core.calendar`). Every exit clock this package keeps is an OFFSET from the close, so a
half day shifts them all by the same three hours instead of running them against a market that has
already shut:

    expiry close      40 minutes before the close      (15:20 normally; 15:30 before 2026-11-06)
    pin guard window  ends at the close
    management        ends 20 minutes before the close (15:40 normally)
    eod               the close to 30 minutes after it (16:00-16:30 normally)

On a half day there is no entry scan at all (`half_day`): the scan starts at 15:35 and the entry
window runs to 15:55, both after the close. A normal day returns exactly the times this package
always used.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from cherrypick.core import calendar as _calendar

NORMAL_CLOSE_MINUTES = 16 * 60
EXPIRY_CLOSE_BEFORE_CLOSE = 40
MANAGEMENT_END_BEFORE_CLOSE = 20
EOD_LENGTH = 30


@dataclass(frozen=True)
class SessionClock:
    session: date
    half_day: bool
    close: int
    management_end: int
    eod_start: int
    eod_end: int

    @property
    def expiry_close(self) -> int:
        return self.close - EXPIRY_CLOSE_BEFORE_CLOSE

    @property
    def shift(self) -> int:
        """Minutes this session closes earlier than a normal one (0 on a normal day)."""
        return NORMAL_CLOSE_MINUTES - self.close


def _hhmm_minutes(value: str) -> int:
    hour, minute = (int(x) for x in value.split(":"))
    return hour * 60 + minute


def for_date(d: date) -> SessionClock:
    close = _hhmm_minutes(_calendar.session_close_hhmm(d))
    return SessionClock(
        session=d,
        half_day=bool(_calendar.is_early_close(d)),
        close=close,
        management_end=close - MANAGEMENT_END_BEFORE_CLOSE,
        eod_start=close,
        eod_end=close + EOD_LENGTH,
    )
