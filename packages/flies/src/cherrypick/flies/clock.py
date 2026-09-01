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

# ET and the "what does now mean" primitives live in cherrypick.core.clock: four modules had written
# the same functions and ~10 more sites re-derived the zone inline, which is how two of them come to
# disagree about what date a session belongs to. The arithmetic BELOW is this module's own.
from cherrypick.core.clock import ET, hhmm_to_min, minute_of_day, now_et, now_iso, today_iso  # noqa: F401


