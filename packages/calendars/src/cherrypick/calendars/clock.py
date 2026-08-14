"""One clock for the module (ET, offset-carrying), plus the week anchor arithmetic.

Every timestamp this module persists is ET and carries its offset — the same rule flies adopted
after a naive `datetime.now()` left its ledger unreadable without knowing which machine wrote it.

The week arithmetic is the strategy's skeleton, so it lives here as pure date functions over
`cherrypick.core.calendar` and nothing else. The rule set:

- The entry session is the week's Monday, or the next trading day of that week when Monday is a
  holiday (2–3 times a year). The resulting structure is TAGGED (`dc_3_6` instead of `dc_4_7`) and
  the tags are never pooled — a 3DTE short is a different trade, not a smaller sample of the same one.
- The front (short) expiration is the last trading day of the entry week — Friday, or Thursday when
  Friday is a holiday (Good Friday).
- The back (long) expiration is the first trading day of the FOLLOWING week — Monday, holiday-shifted
  forward to Tuesday.

Expirations are COMPUTED here and asserted against actual chain rows downstream, never selected with
a nearest-match helper: `cherrypick.core.broker.nearest_expiration` is a two-sided minimizer that can
return a shorter date than asked for, and MEIC's 0DTE selector trap (a silent fallback to the next
cycle) is the standing lesson that a selector's output is never trusted without a post-hoc equality
check.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from cherrypick.core import calendar as _cal

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
    return when.hour * 60 + when.minute


def hhmm_to_min(value: str, default: int) -> int:
    """A config 'HH:MM' as minutes-of-day, falling back rather than crashing on junk."""
    try:
        hours, minutes = str(value).split(":")
        return int(hours) * 60 + int(minutes)
    except (TypeError, ValueError, AttributeError):
        return default


# --------------------------------------------------------------------------- week anchors
def week_monday(day: date) -> date:
    """The Monday of `day`'s calendar week (ISO: Monday..Sunday)."""
    return day - timedelta(days=day.weekday())


def entry_session(week_of: date) -> date | None:
    """The week's entry day: its Monday, else the next trading day of the SAME week (the
    Tuesday-after-a-Monday-holiday rule). None only if the whole Mon–Fri stretch is dark, which the
    NYSE calendar does not produce — kept as None rather than an exception so a caller can treat an
    impossible week as a skipped week instead of a crash."""
    monday = week_monday(week_of)
    for offset in range(5):
        candidate = monday + timedelta(days=offset)
        if _cal.is_trading_day(candidate):
            return candidate
    return None


def front_expiration(entry: date) -> date | None:
    """The short legs' expiration: the LAST trading day of the entry week (Friday, or Thursday on a
    Good Friday week). None if it would not land strictly after the entry day — a week compressed to
    a single trading day has no calendar to build."""
    friday = week_monday(entry) + timedelta(days=4)
    candidate = friday
    while candidate > entry:
        if _cal.is_trading_day(candidate):
            return candidate
        candidate -= timedelta(days=1)
    return None


def back_expiration(entry: date) -> date | None:
    """The long legs' expiration: the FIRST trading day of the week after the entry week (Monday,
    holiday-shifted forward). None mirrors `entry_session`'s impossible-week posture."""
    next_monday = week_monday(entry) + timedelta(days=7)
    for offset in range(5):
        candidate = next_monday + timedelta(days=offset)
        if _cal.is_trading_day(candidate):
            return candidate
    return None


def structure_tag(entry: date, front: date, back: date) -> str:
    """The structure's identity in CALENDAR days from entry: `dc_4_7` for the ordinary
    Monday/Friday/next-Monday week, `dc_3_6` for a Tuesday entry, `dc_4_8` when the following Monday
    is dark. Distinct tags are distinct trades and are never pooled in analysis."""
    return f"dc_{(front - entry).days}_{(back - entry).days}"


def next_entry_session(today: date) -> date | None:
    """The next entry session ON OR AFTER `today` — this week's if it has not passed, else next
    week's. This is what the stream request derives its forward expirations from, so it must only
    ever change value at a date boundary (it takes no clock, only a date, by construction)."""
    this_week = entry_session(week_monday(today))
    if this_week is not None and this_week >= today:
        return this_week
    return entry_session(week_monday(today) + timedelta(days=7))


def week_plan(today: date) -> dict | None:
    """The full set of computed dates for the week `today`'s next entry belongs to, or None when the
    calendar cannot produce one. One helper so the loop, the stream request, and the tests all
    derive the same dates from the same three functions."""
    entry = next_entry_session(today)
    if entry is None:
        return None
    front = front_expiration(entry)
    back = back_expiration(entry)
    if front is None or back is None:
        return None
    return {
        "week_of": week_monday(entry).isoformat(),
        "entry_session": entry.isoformat(),
        "front_expiration": front.isoformat(),
        "back_expiration": back.isoformat(),
        "structure": structure_tag(entry, front, back),
    }
