"""Declare this module's stream needs so the standalone streamer keeps them fresh in the shared cache.

Writes ``~/.cherrypick/state/stream_requests/meic.json`` (paper) or ``meic-live.json`` (live) — the
streamer reads the union across every installed module and streams exactly that, regardless of
filename. Unlike flies (a pure underlying consumer until 2026-09-17), MEIC *does* need
``leg_sources``: an open IC's four option legs must stay subscribed after spot walks the ATM window away
from them, or their marks / per-side stops / force-closes price off frozen quotes. Each entry points the
streamer at ONE ledger with the canonical open-trades query (same status set as ``db.py``'s
``get_open_trades``); the streamer re-runs it every subscription poll, so a newly opened structure's
legs subscribe within ~30s with no restart.

Paper and live get separate files because they are separate loops on separate schedules (the
orchestrator drives the paper tick; the live loop is run by hand, ``live_loop.py``), and a shared
file would have whichever loop wrote last erase the other's declaration. Each loop writes its own
from its own preamble — the paper loop via ``register``, the live loop via ``register_live`` — and
the streamer unions the two, so the live ledger's open legs are declared from the moment the live
loop first ticks and stay declared after it exits (the file persists; the query is re-run against
the live ledger every poll). Until 2026-09-17 there was no live entry at all: this docstring said
"the live loop doesn't exist yet", the live loop existed, and it never imported this file, so a
live IC's legs survived only while they happened to sit inside the ATM window.

Until 2026-07-29 this file did not exist and ``stream_requests/meic.json`` was hand-written at the
2026-07-21 cutover: it still listed all seven retired symbols and its leg query read the LIVE ledger
(whose open-trades query returns nothing), so open paper positions' legs were never explicitly
subscribed — they survived only while they happened to sit inside the ATM window. The width study made
that acute (~40–120 concurrent structures/day, all reading leg quotes), which is why this writer exists.

The leg columns hold DXLink *streamer* symbols (``.XSP260917P650``), not OCC strings: ``paper_loop``
copies ``streamer_symbol`` off the chain rows ``tt.py get_option_chain`` returns, and the live loop
saves through the same ``paper._save_trade`` row shape. That is the form the streamer subscribes
verbatim (it does no conversion on leg cells), so the query needs no translation.

Best-effort by design: a failed write must never break either loop. An unregistered symbol is a
data-availability problem the readers already surface, not a reason to crash a scheduled run. The
write itself (path convention, symbol cleaning, atomic rename) lives in
``cherrypick.core.streamrequests``; this file is the module-name + logger + leg-source adapter.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from cherrypick.core import streamrequests as _sr  # noqa: E402

from cherrypick.meic import paths as _paths  # noqa: E402

_MODULE = "meic"
_MODULE_LIVE = "meic-live"
_log = logging.getLogger("paper_loop")
_log_live = logging.getLogger("live_loop")

# The canonical open-trades status set (db.py get_open_trades) over the DDL's four leg columns. The
# streamer opens the DB read-only and treats every non-null result cell as a symbol to keep subscribed.
_LEG_QUERY = (
    "SELECT put_symbol, call_symbol, long_put_symbol, long_call_symbol FROM ic_trades "
    "WHERE status IN ('pending','open','partial','partial_entry')"
)


def leg_sources(*, live: bool = False) -> list[dict]:
    """The one leg source a loop declares: the same query, pointed at that loop's own ledger."""
    db = _paths.live_db_path() if live else _paths.paper_db_path()
    return [_sr.leg_source(db, _LEG_QUERY)]


def write(symbols, *, live: bool = False) -> Path:
    """Atomically (over)write this loop's request file — delegated to core (write-then-rename, so a
    concurrent reader in the streamer never sees a partial file), plus the leg source for the ledger
    this loop writes. ``live=True`` writes the LIVE loop's own file (see module docstring for why
    paper/live never share one)."""
    module = _MODULE_LIVE if live else _MODULE
    return _sr.write_request(module, symbols, leg_sources=leg_sources(live=live))


def register(config: dict) -> None:
    """Best-effort: declare the configured ``symbols`` (and the paper ledger's open legs) to the
    streamer. Never raises into the caller."""
    symbols = config.get("symbols") or ([config["symbol"]] if config.get("symbol") else [])
    _sr.register_best_effort(write, symbols, log=_log)


def register_live(config: dict) -> None:
    """Best-effort: declare the pinned ``live.symbol`` and the LIVE ledger's open legs to the
    streamer, from the live loop's own preamble. Never raises into the caller — a loop that refused
    to tick because it could not write a request file would trade a data-quality problem for an
    outage on the one path that holds real positions."""
    symbol = str((config.get("live") or {}).get("symbol") or "").strip()
    _sr.register_best_effort(write, [symbol] if symbol else [], live=True, log=_log_live)
