"""One clock for the module (ET, offset-carrying), plus the session's decision window.

The decision is taken `decision_minutes_before_close` before the regular close, so it lands at
15:50 on an ordinary day and 12:50 on an early close. A fixed 15:50 would miss every half-day
session outright -- the day after Thanksgiving and Christmas Eve are early closes, and a regime
switch that cannot fire on them is a rule nobody wrote down.
"""

from __future__ import annotations

from datetime import date

from cherrypick.core import clock as _clock
from cherrypick.core.clock import (  # noqa: F401
    ET,
    close_min,
    hhmm_to_min,
    minute_of_day,
    now_et,
    now_iso,
    today_iso,
)

WINDOW_DEFAULTS = {
    "decision_minutes_before_close": 10,
    "decision_window_minutes": 8,
}


def decision_window(day: date, params: dict | None = None) -> tuple[int, int]:
    """(first, last) minute of day in which the session's decision may be taken. A tick inside it
    that cannot act (a stale quote, an unmeasured regime) retries on the next tick; a window that
    closes unacted records a miss, never a late fill."""
    return _clock.decision_window(day, params, WINDOW_DEFAULTS)
