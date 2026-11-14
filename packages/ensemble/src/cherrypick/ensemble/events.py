"""Event days an arm skips on.

An arm names an event by label. Labels with a calendar rule are computed (`rule_days`); the rest are
data handed in as ISO dates, with the ranges the data speaks for. `occurs` answers True, False, or None
when a label has data but not for that date: the engine never reads None as "quiet", it passes the gate
and records `unknown`, so a backtest can label the rows that ran without it.

Labels
    PMI-Mfg, PMI-Svcs   the 1st and 3rd trading day of the month (the survey releases follow that rule;
                        `core.events.RULE_EVENTS` holds the same two)
    OPEX                the monthly options expiry: the third Friday, the Thursday when that is a holiday
    FOMC                the decision days in `core.calendar` (unknown for a year it does not hold)
    MonthEnd            the last trading day of a month
    QuarterEnd          the last trading day of March, June, September and December
    FirstWeekly         the first Friday of the month (the Thursday when that is a holiday): the first
                        weekly-cycle expiry
    CPI, Sentiment      data: no rule publishes them (see `packages/backtest/scripts/event_history_item75.py`)
"""

from __future__ import annotations

from datetime import date, timedelta

from cherrypick.core import calendar as cal

RULE_LABELS = ("PMI-Mfg", "PMI-Svcs", "OPEX", "FOMC", "MonthEnd", "QuarterEnd", "FirstWeekly")


def _first_trading_days(year: int, month: int, n: int) -> date:
    d = date(year, month, 1)
    if not cal.is_trading_day(d):
        d = cal.next_trading_day(d)
    return cal.nth_trading_day(d, n - 1)


def _friday_or_thursday(d: date) -> date:
    return d if cal.is_trading_day(d) else d - timedelta(days=1)


def rule_days(label: str, year: int, month: int) -> list[date] | None:
    """The days in that month carrying a rule label, or None when the label has no rule here (or the
    FOMC year is not held)."""
    if label == "PMI-Mfg":
        return [_first_trading_days(year, month, 1)]
    if label == "PMI-Svcs":
        return [_first_trading_days(year, month, 3)]
    if label == "OPEX":
        return [_friday_or_thursday(cal.nth_weekday(year, month, cal.FRI, 3))]
    if label == "FirstWeekly":
        return [_friday_or_thursday(cal.nth_weekday(year, month, cal.FRI, 1))]
    if label in ("MonthEnd", "QuarterEnd"):
        if label == "QuarterEnd" and month % 3:
            return []
        nxt = date(year + (month == 12), month % 12 + 1, 1)
        return [cal.previous_trading_day(nxt)]
    if label == "FOMC":
        if not cal.fomc_year_known(year):
            return None
        return [d for d in cal.fomc_dates(year) if d.month == month]
    return None


class EventCalendar:
    """`data` maps a data label to its ISO dates; `covered` maps it to [(first, last)] ISO ranges the
    dates are complete for. A label in neither is unknown everywhere."""

    def __init__(self, data: dict[str, list[str]] | None = None, covered: dict[str, list] | None = None):
        self.data = {k: set(v) for k, v in (data or {}).items()}
        self.covered = {k: [tuple(r) for r in v] for k, v in (covered or {}).items()}

    def _on(self, label: str, day: date) -> bool | None:
        if label in RULE_LABELS:
            days = rule_days(label, day.year, day.month)
            return None if days is None else day in days
        ranges = self.covered.get(label)
        if ranges is None or not any(a <= day.isoformat() <= b for a, b in ranges):
            return None
        return day.isoformat() in self.data.get(label, set())

    def occurs(self, label: str, first: date, last: date) -> bool | None:
        """Any day first..last carries the label. True as soon as one does; None when some day is
        unknown and none is known to; False otherwise."""
        unknown = False
        d = first
        while d <= last:
            v = self._on(label, d)
            if v:
                return True
            unknown = unknown or v is None
            d += timedelta(days=1)
        return None if unknown else False
