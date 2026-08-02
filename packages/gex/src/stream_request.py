"""Declare this module's stream needs so the standalone streamer keeps them fresh in the shared cache.

Writes ``~/.cherrypick/state/stream_requests/gex.json`` — the streamer reads the union across every
installed module and streams exactly that. gex registers the underlyings its dashboard/section surfaces
(all of ``symbols``, since the viewer can switch between them). It declares no ``legs``.

Best-effort by design: a failed write must never break a gex command. An unregistered symbol is a
data-availability problem the provider already surfaces (it reads the cache read-only and reports when a
symbol has no live GEX), not a reason to fail a read.

Thin standalone equivalent of ``packages/streamer/src/registry.py``'s writer — a consumer cannot import
that package. The write itself (path convention, symbol cleaning, atomic rename) lives in
``cherrypick.core.streamrequests`` since 2026-07-29; this file is the module-name + logger adapter.
"""

from __future__ import annotations

import logging
from pathlib import Path

from cherrypick.core import streamrequests as _sr

_MODULE = "gex"
_log = logging.getLogger("cherrypick-gex")


def write(symbols) -> Path:
    """Atomically (over)write this module's request file — delegated to core (write-then-rename, so a
    concurrent reader in the streamer never sees a partial file)."""
    return _sr.write_request(_MODULE, symbols)


def register(config: dict) -> None:
    """Best-effort: declare the configured ``symbols`` to the streamer. Never raises into the caller."""
    try:
        write(config.get("symbols") or [])
    except Exception as exc:  # noqa: BLE001 — registration is advisory, never fatal to a read
        _log.warning("stream request registration failed: %s", exc)
