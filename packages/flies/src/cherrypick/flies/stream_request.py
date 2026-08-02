"""Declare this module's stream needs so the standalone streamer keeps them fresh in the shared cache.

Writes ``~/.cherrypick/state/stream_requests/flies.json`` (paper) or ``flies-live.json`` (live) — the
streamer reads the union across every file in that directory and streams exactly that, regardless of
filename, so paper and live get distinct request files rather than one module name stomping the
other's registration (they tick on independent schedules; each has its own `window_hints` escalation
state in its own DB, so a shared file would have paper's next unescalated write erase live's, and vice
versa). flies is a pure consumer: it registers only the underlyings it prices butterflies on. It
declares no ``legs`` — its structures stay near the money, inside the streamer's ATM window, so it
never needs a symbol kept subscribed beyond it.

Best-effort by design: a failed write must never break the paper loop. An unregistered symbol is a
data-availability problem the provider already surfaces (it refuses on stale/missing rather than
guessing), not a reason to crash a scheduled run.

Thin standalone equivalent of ``packages/streamer/src/registry.py``'s writer — a consumer cannot import
that package. The write itself (path convention, symbol cleaning, atomic rename) lives in
``cherrypick.core.streamrequests`` since 2026-07-29; this file is the module-name + logger adapter.
"""

from __future__ import annotations

import logging
from pathlib import Path

from cherrypick.core import streamrequests as _sr

_MODULE = "flies"
_MODULE_LIVE = "flies-live"
_log = logging.getLogger("flies_paper_loop")


def write(symbols, window_hints=None, *, live: bool = False) -> Path:
    """Atomically (over)write this loop's request file — delegated to core (write-then-rename, so a
    concurrent reader in the streamer never sees a partial file). ``window_hints`` is an optional
    ``{symbol: strike_count}`` request for a WIDER-than-default per-symbol ATM window (see
    ``stream_window.py``, which computes it from real ``missing_leg_quotes`` refusals). ``live=True``
    writes the LIVE loop's own file (see module docstring for why paper/live never share one)."""
    module = _MODULE_LIVE if live else _MODULE
    return _sr.write_request(module, symbols, window_hints=window_hints)


def register(config: dict, window_hints=None, *, live: bool = False) -> None:
    """Best-effort: declare the configured ``symbols`` (and any ``window_hints``) to the streamer.
    Never raises into the caller."""
    try:
        write(config.get("symbols") or [], window_hints=window_hints, live=live)
    except Exception as exc:  # noqa: BLE001 — registration is advisory, never fatal to the loop
        _log.warning("stream request registration failed: %s", exc)
