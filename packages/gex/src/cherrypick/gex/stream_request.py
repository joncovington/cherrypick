"""Declare this module's stream needs so the standalone streamer keeps them fresh in the shared cache.

Writes ``~/.cherrypick/state/stream_requests/gex.json`` — the streamer reads the union across every
installed module and streams exactly that. gex registers the underlyings its console surface offers
(all of ``symbols``, since the viewer can switch between them) PLUS the market-regime sampler's
reading symbols as quote-only ``legs`` — quote-only means ``legs``, never ``symbols``, because a
``symbols`` entry has the producer maintain an option chain nothing here reads (the overview
2026-08-17 incident). The recorder declares its own legs rather than free-riding on the modules
that happen to stream VIX or the sectors today, so the regime series cannot go dark because some
other module's declaration changed — coverage driven off what this module itself declares.

``history_days`` rides on the legs at 270 (the curve/overview precedent: a year of context on day
one, and the load lesson — 1000 across sixteen symbols never finished) so ``daily_closes`` backfills
from the streamer's own candle history instead of accruing one row per session.

Best-effort by design: a failed write must never break a gex command. An unregistered symbol is a
data-availability problem the provider already surfaces (it reads the cache read-only and reports
when a symbol has no live GEX), not a reason to fail a read.

Thin standalone equivalent of ``packages/streamer/src/registry.py``'s writer — a consumer cannot import
that package. The write itself (path convention, symbol cleaning, atomic rename) lives in
``cherrypick.core.streamrequests`` since 2026-07-29; this file is the module-name + logger adapter.
"""

from __future__ import annotations

import logging
from pathlib import Path

from cherrypick.core import streamrequests as _sr

from cherrypick.gex import regime as _regime

_MODULE = "gex"
_HISTORY_DAYS = 270
_log = logging.getLogger("cherrypick-gex")


def regime_legs(symbols) -> list[str]:
    """The market-regime sampler's quote-only legs: every reading symbol not already declared as an
    underlying. Derived from ``regime.READINGS`` so a new reading is covered the moment it is
    declared — the coverage guard in the tests drives off the same list."""
    declared = {str(s).strip().upper() for s in (symbols or [])}
    # Futures ride as legs too, from the resolved contract map — quote-only, like every other
    # reading. Their symbols carry an exchange suffix and are NOT upper-cased or otherwise cleaned
    # here: `/VXU26:XCBF` is what the instruments endpoint returned and what DXLink expects.
    wanted = set(_regime.READINGS.values()) | set(_regime.futures_symbols().values())
    return sorted(wanted - declared)


def write(symbols, prints_symbols=()) -> Path:
    """Atomically (over)write this module's request file — delegated to core (write-then-rename, so a
    concurrent reader in the streamer never sees a partial file).

    ``entry_symbols=[]`` (2026-10-15): gex computes and records, and enters nothing. Its roster is
    what the suite's same-index lint judges, and SPX beside XSP -- recorded together for MEIC's
    replay -- is two instruments watched, not one directional bet held twice.

    ``prints_symbols`` (2026-11-01) declares the opt-in ``TimeAndSale`` event on those symbols'
    windows, for the print recorder (`service.record_prints`). Naming only the opt-in event keeps
    the four default events (`streamrequests.window_event_set`), so this never narrows what gex's
    profile reads; the producer serves prints on the nearest window only."""
    legs = regime_legs(symbols)
    return _sr.write_request(
        _MODULE,
        symbols,
        legs=legs,
        history_days={leg: _HISTORY_DAYS for leg in legs},
        window_events={s: ["TimeAndSale"] for s in prints_symbols},
        entry_symbols=[],
    )


def register(config: dict) -> None:
    """Best-effort: declare the configured ``symbols`` + the regime legs to the streamer, and the
    print event when the ``prints`` block is on. Never raises into the caller."""
    from cherrypick.gex import service as _service  # local: service imports this module's caller

    prints = _service.prints_settings(config)
    _sr.register_best_effort(
        write,
        config.get("symbols") or [],
        prints["symbols"] if prints["enabled"] else [],
        log=_log,
    )
