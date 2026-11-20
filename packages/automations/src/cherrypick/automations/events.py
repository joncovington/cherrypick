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
    TripleWitching      the third Friday of March, June, September and December (the Thursday when that
                        is a holiday): stock options, index options and index futures expire together
    VIXExpiry           the VIX monthly expiration: the Wednesday 30 days before the next month's third
                        Friday (counted from the trading day before that Friday when it is a holiday; the
                        trading day before the Wednesday when it is one)
    FuturesRoll         the equity index futures roll: 8 calendar days before a quarterly expiry, the
                        Thursday before triple witching (the 7th to the 13th, never a holiday)
    CPI, Sentiment      data: no rule publishes them (see `packages/backtest/scripts/event_history_item75.py`)
    PPI, NFP, PCE       data from the calendar store (`core.events.release_dates`: FRED's dates for PPI
                        and the jobs report, BEA's for PCE), complete over the ranges that store covers;
                        a label the item 75 file already carries keeps its dates
    Earnings, ExDividend  the arm's own symbol's dates (`core.symbolevents`): data, complete over the
                        ranges the file gives for that symbol, unknown for a symbol it does not cover
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import calendar as cal
from cherrypick.core import events as core_events
from cherrypick.core import home, symbolevents

RULE_LABELS = (
    "PMI-Mfg",
    "PMI-Svcs",
    "OPEX",
    "FOMC",
    "MonthEnd",
    "QuarterEnd",
    "FirstWeekly",
    "TripleWitching",
    "VIXExpiry",
    "FuturesRoll",
)
#: Data labels read from the suite's calendar store when the item 75 file does not carry them.
STORE_LABELS = ("PPI", "NFP", "PCE")
#: Labels whose dates are the symbol's own; the calendar keys them `label:SYMBOL`.
SYMBOL_LABELS = symbolevents.LABELS

#: Each label's name and when it falls, in words, for a reader who does not know the calendar.
ABOUT = {
    "Earnings": ("Earnings", "The symbol's own earnings report day."),
    "ExDividend": (
        "Ex-dividend",
        "The symbol's own ex-dividend day. A short call can be assigned the day before.",
    ),
    "FOMC": ("Fed decision (FOMC)", "The Federal Reserve's rate decision, 2:00 PM ET, eight times a year."),
    "FirstWeekly": (
        "First weekly expiry",
        "The first Friday of the month (the Thursday when it is a holiday).",
    ),
    "FuturesRoll": (
        "Futures roll",
        "The Thursday a week before triple witching, when index futures roll to the next quarter.",
    ),
    "MonthEnd": ("Month end", "The last trading day of the month."),
    "NFP": ("Jobs report (NFP)", "The monthly employment report, 8:30 AM ET, usually the first Friday."),
    "OPEX": (
        "Monthly options expiry (OPEX)",
        "The third Friday of the month (the Thursday when it is a holiday).",
    ),
    "PCE": ("PCE inflation", "The Fed's preferred inflation measure, 8:30 AM ET, near the end of the month."),
    "PMI-Mfg": (
        "Manufacturing PMI",
        "The ISM manufacturing survey, 10:00 AM ET, the 1st trading day of the month.",
    ),
    "PMI-Svcs": ("Services PMI", "The ISM services survey, 10:00 AM ET, the 3rd trading day of the month."),
    "PPI": ("Producer prices (PPI)", "Wholesale inflation, 8:30 AM ET, mid-month."),
    "QuarterEnd": ("Quarter end", "The last trading day of March, June, September and December."),
    "TripleWitching": (
        "Triple witching",
        "The third Friday of March, June, September and December: stock options, index options and index "
        "futures expire together.",
    ),
    "VIXExpiry": ("VIX expiry", "A Wednesday, 30 days before the next month's third Friday."),
}


def _first_trading_days(year: int, month: int, n: int) -> date:
    d = date(year, month, 1)
    if not cal.is_trading_day(d):
        d = cal.next_trading_day(d)
    return cal.nth_trading_day(d, n - 1)


def _friday_or_thursday(d: date) -> date:
    return d if cal.is_trading_day(d) else d - timedelta(days=1)


def _trading_or_before(d: date) -> date:
    return d if cal.is_trading_day(d) else cal.previous_trading_day(d)


def _vix_expiry_for(year: int, month: int) -> date:
    """The VIX expiration whose SPX options expire in the month after (`year`, `month`)."""
    ny, nm = year + (month == 12), month % 12 + 1
    friday = _trading_or_before(cal.nth_weekday(ny, nm, cal.FRI, 3))
    return _trading_or_before(friday - timedelta(days=30))


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
    if label in ("TripleWitching", "FuturesRoll"):
        if month % 3:
            return []
        third = cal.nth_weekday(year, month, cal.FRI, 3)
        if label == "TripleWitching":
            return [_friday_or_thursday(third)]
        return [third - timedelta(days=8)]
    if label == "VIXExpiry":
        # Thirty days before a third Friday (the 15th to the 21st) is always in the month before it.
        return [_vix_expiry_for(year, month)]
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

    @staticmethod
    def key(label: str, symbol: str | None) -> str:
        """The data key a label is kept under: a symbol label is the symbol's own (`Earnings:SPY`)."""
        return f"{label}:{(symbol or '').upper()}" if label in SYMBOL_LABELS else label

    def _on(self, label: str, day: date, symbol: str | None = None) -> bool | None:
        label = self.key(label, symbol)
        if label in RULE_LABELS:
            days = rule_days(label, day.year, day.month)
            return None if days is None else day in days
        ranges = self.covered.get(label)
        if ranges is None or not any(a <= day.isoformat() <= b for a, b in ranges):
            return None
        return day.isoformat() in self.data.get(label, set())

    def occurs(self, label: str, first: date, last: date, symbol: str | None = None) -> bool | None:
        """Any day first..last carries the label. True as soon as one does; None when some day is
        unknown and none is known to; False otherwise."""
        unknown = False
        d = first
        while d <= last:
            v = self._on(label, d, symbol)
            if v:
                return True
            unknown = unknown or v is None
            d += timedelta(days=1)
        return None if unknown else False

    def dates(self, label: str, first: date, last: date, symbol: str | None = None) -> list[str] | None:
        """The ISO days first..last that carry the label, for a signal that fires on or around an event
        date (`core.signals`' event kind). None when any day in the range is unknown: a signal never
        reads an unknown stretch as quiet."""
        out, d = [], first
        while d <= last:
            v = self._on(label, d, symbol)
            if v is None:
                return None
            if v:
                out.append(d.isoformat())
            d += timedelta(days=1)
        return out


def next_day(cal: EventCalendar, label: str, today: date, horizon: int = 120) -> str | None:
    """The first day from `today` carrying the label, within `horizon` days, or None when none does or
    the calendar stops speaking for the label before one is found (a symbol label never answers here)."""
    if label in SYMBOL_LABELS:
        return None
    for i in range(horizon + 1):
        d = today + timedelta(days=i)
        v = cal._on(label, d)
        if v is None:
            return None
        if v:
            return d.isoformat()
    return None


def load_calendar(path=None, *, store_root=None, symbol_file=None) -> EventCalendar:
    """The event data `packages/backtest/scripts/event_history_item75.py` wrote, if any
    (`<home>/data/public/item75/events.json`, or `path`), with the calendar store's PPI, NFP and PCE
    where that file lacks them (`core.events.release_dates`, `store_root` its folder) and each symbol's
    earnings and ex-dividend dates (`core.symbolevents`, `symbol_file`). A data label no source covers
    is unknown, which passes its gate and is recorded `unknown`."""
    p = Path(path) if path else home.data_dir("public") / "item75" / "events.json"
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {}
    data = dict(doc.get("data") or {})
    covered = dict(doc.get("covered") or {})
    for label in STORE_LABELS:
        if label in data or label in covered:
            continue
        dates, cov = core_events.release_dates(label, root=store_root)
        if cov:
            data[label], covered[label] = dates, cov
    for label, per in symbolevents.read(symbol_file).items():
        for sym, (dates, cov) in per.items():
            if cov:
                k = EventCalendar.key(label, sym)
                data[k], covered[k] = dates, cov
    return EventCalendar(data, covered)
