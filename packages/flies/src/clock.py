"""One clock for the module, and it reads Eastern.

Every timestamp this module persists is ET and carries its offset. That is not a formatting
preference — it is what makes the record self-describing. Until 2026-07-27 the DB writers used a bare
`datetime.now()`, so `fly_positions.entry_time` held naive machine-local time while the engine's own
session logic ran on ET (`provider.now_et`), and the same row could read `07:45` next to an
`entry_window` of `09:45-14:30`. Nothing was wrong with the trading; the record was simply not
readable without knowing which machine wrote it, and any analysis that compared a stored time against
a market hour was silently two hours out. A stored instant that needs external context to interpret is
a bug waiting for the reader who doesn't have that context.

Offsets are kept on the string (`2026-07-27T11:06:01-04:00`) rather than normalised away, so DST is
unambiguous across a March/November boundary and `datetime.fromisoformat` round-trips exactly.
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
    """Today's ET date. Deliberately not the local date — after 20:00 in Mountain (23:00 Pacific)
    the local calendar day is already tomorrow in ET, which would file a session under the wrong
    trade_date."""
    return now_et().date().isoformat()
