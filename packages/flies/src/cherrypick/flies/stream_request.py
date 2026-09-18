"""Declare this module's stream needs so the standalone streamer keeps them fresh in the shared cache.

Writes ``~/.cherrypick/state/stream_requests/flies.json`` (paper) or ``flies-live.json`` (live) — the
streamer reads the union across every file in that directory and streams exactly that, regardless of
filename, so paper and live get distinct request files rather than one module name stomping the
other's registration (they tick on independent schedules; each has its own `window_hints` escalation
state in its own DB, so a shared file would have paper's next unescalated write erase live's, and vice
versa). Each file declares the underlyings the loop prices butterflies on and, since 2026-09-17, a
``leg_sources`` entry pointing at that loop's OWN ledger.

**Why legs, when the structures sit near the money.** Until 2026-09-17 this file said flies "declares
no legs — its structures stay near the money, inside the streamer's ATM window". That was a hope: the
window is centred on spot each pass, so a position's legs are inside it only while spot stays close to
where the position was entered, and the sessions where spot does NOT — a large move — are exactly the
sessions where a defined-risk short vertical needs its marks most. A live position in that state would
have been priced off frozen quotes with nothing on the decision path able to tell (the 2026-07-01
lesson: a stalled feed looks like a quiet market). The ledger could not even say which contracts a
position held (symbol/centre/width/side only), so ``fly_positions`` gained four ``*_leg_symbol``
columns the same day (see ``db.py``), written at entry and completion by both loops from the same
leg quote the price came from — the DXLink streamer symbol the cache is keyed by, never OCC, since
the producer subscribes each leg cell verbatim and does no conversion.

The query selects those four columns for every ``status = 'open'`` row; the producer re-runs it
every subscription poll and treats each non-null cell as a symbol, so a row written before the
columns existed (NULL throughout) contributes nothing and a position settling drops its legs on the
next poll with no restart. It is one statement, which the producer requires. ``window_hints``
(``stream_window.py``'s escalation after repeated ``missing_leg_quotes`` refusals) is unchanged and
still matters: the leg source keeps legs we already HOLD quoted; the window is what lets the next
entry find a quote at all.

Best-effort by design: a failed write must never break either loop. An unregistered symbol is a
data-availability problem the provider already surfaces (it refuses on stale/missing rather than
guessing), not a reason to crash a scheduled run.

Thin standalone equivalent of ``packages/streamer/src/registry.py``'s writer — a consumer cannot import
that package. The write itself (path convention, symbol cleaning, atomic rename) lives in
``cherrypick.core.streamrequests`` since 2026-07-29; this file is the module-name + logger + leg-source
adapter.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from cherrypick.core import streamrequests as _sr

from cherrypick.flies import db as _db

_MODULE = "flies"
_MODULE_LIVE = "flies-live"
_log = logging.getLogger("flies_paper_loop")

# Every open row's four leg columns (db.py `_ADDED_POSITION_COLUMNS`). `status = 'open'` is the
# ledger's own open predicate (settled/cancelled/void rows leave it); the producer treats each
# non-null cell as a streamer symbol, so an unused role (no `far` outside bwb, no `completing`
# before completion) and every pre-2026-09-17 row simply contribute nothing.
LEG_QUERY = (
    "SELECT center_leg_symbol, wing_leg_symbol, far_leg_symbol, completing_leg_symbol "
    "FROM fly_positions WHERE status = 'open'"
)


def leg_sources(db_path=None, *, live: bool = False) -> list[dict]:
    """The one leg source a loop declares: the same query, pointed at that loop's own ledger.

    ``db_path`` is the ledger the loop actually opened (``--db``); absent, it resolves the way
    ``db.connect`` does for that loop -- ``db.live_db_path()`` for live, ``FLIES_DB_PATH`` then
    ``db.default_db_path()`` for paper -- so the file can never name a ledger the loop is not
    writing."""
    if not db_path:
        db_path = _db.live_db_path() if live else (os.environ.get("FLIES_DB_PATH") or _db.default_db_path())
    return [_sr.leg_source(db_path, LEG_QUERY)]


def write(symbols, window_hints=None, *, live: bool = False, db_path=None) -> Path:
    """Atomically (over)write this loop's request file — delegated to core (write-then-rename, so a
    concurrent reader in the streamer never sees a partial file). ``window_hints`` is an optional
    ``{symbol: strike_count}`` request for a WIDER-than-default per-symbol ATM window (see
    ``stream_window.py``, which computes it from real ``missing_leg_quotes`` refusals). ``live=True``
    writes the LIVE loop's own file (see module docstring for why paper/live never share one), with
    the leg source pointed at the live ledger."""
    module = _MODULE_LIVE if live else _MODULE
    return _sr.write_request(
        module, symbols, leg_sources=leg_sources(db_path, live=live), window_hints=window_hints
    )


def register(config: dict, window_hints=None, *, live: bool = False, db_path=None) -> None:
    """Best-effort: declare the configured ``symbols`` (and any ``window_hints``) and this loop's
    open legs (``db_path``: the ledger the loop opened) to the streamer. Never raises into the
    caller."""
    _sr.register_best_effort(
        write, config.get("symbols") or [], window_hints=window_hints, live=live, db_path=db_path, log=_log
    )
