"""The suite's clock: what "now" means, in Eastern, everywhere.

Every module here trades a US market, so every timestamp it persists and every session it files a
result under is an ET fact. That is not the machine's clock and not UTC, and the difference is not
cosmetic — a late-evening Pacific run is already tomorrow in ET, and a UTC-midnight comparison flips
at 20:00 ET. Both mistakes file real rows under the wrong day.

This is here because four modules had written the same six functions and roughly ten more sites
re-derived `ZoneInfo("America/New_York")` inline, which is the shape that lets a difference appear
without anyone choosing it. It clears `packages/core`'s own bar precisely: two packages would
otherwise disagree about what date a session belongs to.

Deliberately ONLY the primitives. Each module's own `clock.py` keeps its date arithmetic — the
calendars week anchors, the pmcc expiration plan — because those are strategy skeletons that share a
name and nothing else, and folding them together would invent a coupling the strategies do not have.
"""

from __future__ import annotations

from datetime import datetime

try:
    from zoneinfo import ZoneInfo

    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - only where zoneinfo has no tz database
    import pytz

    ET = pytz.timezone("America/New_York")


def now_et() -> datetime:
    """Timezone-aware 'now' in Eastern."""
    return datetime.now(ET)


def now_iso() -> str:
    """ET timestamp for persistence: seconds precision, offset included."""
    return now_et().isoformat(timespec="seconds")


def today_iso() -> str:
    """Today's ET date — deliberately not the local date (a late-evening Pacific run is already
    tomorrow in ET, which would file a session under the wrong day)."""
    return now_et().date().isoformat()


def minute_of_day(when: datetime) -> int:
    """Minutes since midnight, for comparing against a config 'HH:MM' window.

    Takes the caller's `when` rather than reading a clock, so a rule that decides something can be
    handed the tick being evaluated instead of whenever the process happens to run. Reading the
    clock inside a decision rule is what made five earnings evaluators answer differently depending
    on the day the suite was started.
    """
    return when.hour * 60 + when.minute


RTH_OPEN_MINUTE = 9 * 60 + 30
RTH_CLOSE_MINUTE = 16 * 60


def in_rth(when: datetime) -> bool:
    """Regular trading hours (09:30-16:00 ET) on an NYSE trading day.

    A naive `when` is read as ET; an aware one is converted. Half-days still count to 16:00 -- a
    shorter honest session is not worth a second calendar. One definition because three readers of
    the GEX history disagreed about what an off-hours row is, and each one that guessed wrong read
    a frozen overnight value as the session's.
    """
    from cherrypick.core import calendar as _calendar  # calendar imports nothing from here

    if when.tzinfo is not None:
        when = when.astimezone(ET)
    if not _calendar.is_trading_day(when.date()):
        return False
    return RTH_OPEN_MINUTE <= minute_of_day(when) < RTH_CLOSE_MINUTE


def rth_bounds(session: str) -> tuple[float, float]:
    """Epoch seconds of 09:30 and 16:00 ET on an ISO `session` date, DST-correct."""
    day = datetime.fromisoformat(session)
    open_ = day.replace(hour=RTH_OPEN_MINUTE // 60, minute=RTH_OPEN_MINUTE % 60, tzinfo=ET)
    close = day.replace(hour=RTH_CLOSE_MINUTE // 60, minute=RTH_CLOSE_MINUTE % 60, tzinfo=ET)
    return open_.timestamp(), close.timestamp()


def hhmm_to_min(value: str, default: int) -> int:
    """A config 'HH:MM' as minutes-of-day, falling back rather than crashing on junk."""
    try:
        hours, minutes = str(value).split(":")
        return int(hours) * 60 + int(minutes)
    except (TypeError, ValueError, AttributeError):
        return default
