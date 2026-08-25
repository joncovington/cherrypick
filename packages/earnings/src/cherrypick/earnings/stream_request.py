"""Declare this module's stream needs so the standalone streamer keeps them fresh in the shared cache.

Writes ``~/.cherrypick/state/stream_requests/earnings.json``; the streamer reads the union across
every file in that directory and streams exactly that. The write itself (path convention, symbol
cleaning, atomic rename) lives in ``cherrypick.core.streamrequests`` — this file is the module-name,
logger, and what-do-we-actually-need adapter. A consumer cannot import ``packages/streamer``.

Earnings is unlike the other consumers in two ways that decide the shape of this request.

**Its underlyings are the underlyings of open positions, and nothing else.** Every other module
streams a fixed handful of index symbols. This one holds defined-risk structures on whatever names
reported earnings that night, so the set turns over completely from week to week. Candidates are
deliberately NOT registered: the entry scan prices its candidates through the broker directly (it is
a Dolt-and-REST scan of dozens of names, most of which are rejected), so adding them here would ask
the producer to stream a nightly churn of symbols for structures that mostly never get opened. The
set therefore grows only when a position is actually opened.

Those underlyings are declared as ``legs`` rather than ``symbols`` — see `write` for why, and for
the budget arithmetic that forced it. The short version: this module wants a spot quote per name,
not a 488-subscription ATM chain it cannot reach into.

**Its legs sit outside the streamer's ATM window.** The producer auto-subscribes an at-the-money window
of the nearest expiration per underlying; an earnings structure's wings, and a calendar's back month,
are neither. So the legs come through ``leg_sources``, which the producer re-runs every subscription
poll — meaning a position opening or closing is picked up with no restart at all. Since 2026-08-25
the underlyings ride the same mechanism, so nothing this module declares can force a recycle.

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
    concurrent reader in the producer never sees a partial file.

    The underlyings go in ``legs``, NOT ``symbols`` (changed 2026-08-25). Both end up subscribed, but
    they are charged and bound completely differently, and for this module the difference is the
    difference between fitting in the producer's budget and breaking it:

    - a ``symbols`` entry is an UNDERLYING: the producer auto-subscribes an ATM window of its nearest
      expiration, which the estimator prices at ~488 subscriptions apiece, and binds it once at
      startup so a grown set forces a recycle.
    - a ``legs`` entry is one streamer-symbol, re-read every subscription poll, and is rounding error
      in the budget.

    This module was paying the first price for none of the benefit. Its structures' wings and back
    months sit outside the ATM window by construction (see the module docstring), so the 488-symbol
    chain it was buying per name held almost nothing it could use — it wanted the underlying's spot
    quote, which is one subscription. With the control-book widening (docs/control-book-plan.md)
    taking a night's names from about one to potentially dozens, that overpayment stopped being
    merely wasteful: at ~488 each against roughly 4,000 subscriptions of suite headroom, some eight
    names would have exhausted the budget, and the failure mode on the other side of it is the
    2026-08-24 producer outage (79 reconnects on "subscription rate is too high").

    The second-order win matters as much: legs are re-read every poll, so this module can no longer
    force a producer recycle at all, and the "only safe to GROW outside the session" hazard that
    shapes `paper_loop.refresh_stream_request` simply stops applying to it.
    """
    underlyings = sorted({str(s).strip().upper() for s in symbols if str(s).strip()})
    return _sr.write_request(_MODULE, (), legs=underlyings, leg_sources=leg_sources(db_path))


def register(symbols, db_path: Path | None = None) -> None:
    """Best-effort: declare the open positions' underlyings and the leg query. Never raises into the
    caller — registration is advisory, and a loop that refused to run because it could not write a
    request file would trade a data-quality problem for an outage."""
    _sr.register_best_effort(write, symbols, db_path, log=_log)
