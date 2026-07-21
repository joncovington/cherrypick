"""Declare this module's stream needs so the standalone streamer keeps them fresh in the shared cache.

Writes ``~/.cherrypick/state/stream_requests/flies.json`` — the streamer reads the union across every
installed module and streams exactly that. flies is a pure consumer: it registers only the underlyings it
prices butterflies on. It declares no ``legs`` — its structures stay near the money, inside the
streamer's ATM window, so it never needs a symbol kept subscribed beyond it.

Best-effort by design: a failed write must never break the paper loop. An unregistered symbol is a
data-availability problem the provider already surfaces (it refuses on stale/missing rather than
guessing), not a reason to crash a 2-minute scheduled run.

Thin standalone equivalent of ``packages/streamer/src/registry.py``'s writer — a consumer cannot import
that package, so the tiny write is duplicated (candidate to consolidate into
``cherrypick.core.streamrequests`` later). See ``docs/streamer-package-plan.md``.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

_CORE = Path(__file__).resolve().parent / "_core"
if _CORE.is_dir() and str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from cherrypick.core import home as _home  # noqa: E402

_MODULE = "flies"
_log = logging.getLogger("flies_paper_loop")


def _clean_symbols(symbols) -> list[str]:
    out: set[str] = set()
    for s in symbols or []:
        if isinstance(s, str) and s.strip():
            out.add(s.strip().upper())
    return sorted(out)


def write(symbols) -> Path:
    """Atomically (over)write this module's request file. Write-then-rename so a concurrent reader in the
    streamer never sees a partial file."""
    directory = _home.ensure(_home.state_dir() / "stream_requests")
    path = directory / f"{_MODULE}.json"
    payload = {"symbols": _clean_symbols(symbols), "legs": [], "leg_sources": []}
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)
    return path


def register(config: dict) -> None:
    """Best-effort: declare the configured ``symbols`` to the streamer. Never raises into the caller."""
    try:
        write(config.get("symbols") or [])
    except Exception as exc:  # noqa: BLE001 — registration is advisory, never fatal to the loop
        _log.warning("stream request registration failed: %s", exc)
