"""Declare this module's stream needs so the standalone streamer keeps them fresh in the shared cache.

Writes ``~/.cherrypick/state/stream_requests/earnings.json``; the streamer reads the union across
every file in that directory and streams exactly that. The write itself (path convention, symbol
cleaning, atomic rename) lives in ``cherrypick.core.streamrequests`` — this file is the module-name,
logger, and what-do-we-actually-need adapter. A consumer cannot import ``packages/streamer``.

Earnings is unlike the other consumers in two ways that decide the shape of this request.

**Its symbols are the underlyings of open positions, and nothing else.** Every other module streams a
fixed handful of index symbols. This one holds defined-risk structures on whatever names reported
earnings that night, so the set turns over completely from week to week. Candidates are deliberately
NOT registered: the entry scan prices its candidates through the broker directly (it is a Dolt-and-REST
scan of dozens of names, most of which are rejected), so adding them here would ask the producer to
stream a nightly churn of symbols for structures that mostly never get opened. The set therefore grows
only when a position is actually opened — once, in the evening, after that day's entry pass — and the
producer's recycle for the new symbols lands after the close rather than during a session.

**Its legs sit outside the streamer's ATM window.** The producer auto-subscribes an at-the-money window
of the nearest expiration per underlying; an earnings structure's wings, and a calendar's back month,
are neither. So the legs come through ``leg_sources``, which the producer re-runs every subscription
poll — meaning a position opening or closing is picked up with no restart at all, and only the
underlying set can ever require one.

The query reads ``open_leg_symbols`` (the flat table maintained at entry and cleared at close) but
joins ``trades`` and filters on ``closed_at``, so a close that failed to clear its rows cannot leave
symbols subscribed forever. It is one statement, which the producer requires.

Best-effort by design: a failed write must never break the loop. An unregistered symbol is a
data-availability problem the provider already surfaces — it refuses on stale or missing quotes rather
than guessing — not a reason to fail a scheduled run.
"""

from __future__ import annotations

import logging
from pathlib import Path

from cherrypick.core import streamrequests as _sr

from cherrypick.earnings import paths as _paths

_MODULE = "earnings"
_log = logging.getLogger("earnings_paper_loop")

# Joined and filtered rather than a bare read of open_leg_symbols: the table is maintained, but a
# close that failed partway through would otherwise keep its legs subscribed indefinitely, and the
# producer has no way to know they are dead.
LEG_QUERY = (
    "SELECT s.streamer_symbol FROM open_leg_symbols s "
    "JOIN trades t ON t.order_id = s.order_id "
    "WHERE t.closed_at IS NULL"
)


def leg_sources(db_path: Path | None = None) -> list[dict]:
    """The producer's dynamic subscription spec for this module's open legs."""
    return [_sr.leg_source(db_path or _paths.paper_db_path(), LEG_QUERY)]


def write(symbols, db_path: Path | None = None) -> Path:
    """Atomically (over)write this module's request file — delegated to core, write-then-rename, so a
    concurrent reader in the producer never sees a partial file."""
    return _sr.write_request(_MODULE, symbols, leg_sources=leg_sources(db_path))


def register(symbols, db_path: Path | None = None) -> None:
    """Best-effort: declare the open positions' underlyings and the leg query. Never raises into the
    caller — registration is advisory, and a loop that refused to run because it could not write a
    request file would trade a data-quality problem for an outage."""
    _sr.register_best_effort(write, symbols, db_path, log=_log)
