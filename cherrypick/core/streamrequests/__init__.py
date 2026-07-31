"""The consumer side of the streamer's subscription registry.

Every module that reads the shared stream cache declares what it needs by writing one file,
``<state>/stream_requests/<module>.json``; the standalone streamer (``packages/streamer``) reads the
union across every file and streams exactly that. This module owns the WRITE: the path convention, the
symbol cleaning, and the atomic write-then-rename (so a concurrent reader in the streamer never sees a
partial file). It consolidates three byte-similar per-module writers (flies, gex, meic) that each
carried a "candidate to consolidate into cherrypick.core.streamrequests" note — this is that module.

Consumers keep a thin ``stream_request.py`` adapter for their module name, logger, and (MEIC) their
``leg_sources`` spec; the adapter's ``register(config)`` stays best-effort — a failed write must never
break a paper loop or a CLI read, because an unregistered symbol is a data-availability problem the
readers already surface, not a reason to crash.

Payload shape (see ``packages/streamer/src/registry.py``, the reader):
  - ``symbols``: underlyings to stream (spot + ATM window + GEX + opening range).
  - ``legs``: optional explicit static extra streamer-symbols.
  - ``leg_sources``: ``{"db": path, "query": select}`` specs — the streamer opens each DB read-only and
    re-runs the query every subscription poll, treating each non-null result cell as an extra symbol to
    keep subscribed beyond the ATM window (how MEIC keeps its open IC legs fresh).
  - ``window_hints``: optional ``{symbol: strike_count}`` — a module's request for a WIDER-than-default
    per-symbol ATM window (e.g. flies escalating after repeated ``missing_leg_quotes`` refusals). The
    streamer takes the max hint per symbol across every module's file, so one module's need is never
    narrowed by another's silence on that symbol. Absent/empty is the common case (accept the default).
"""

from __future__ import annotations

import json
from pathlib import Path

from cherrypick.core import home as _home


def request_path(module: str) -> Path:
    """Where this module's request file lives (directory created if absent)."""
    return _home.ensure(_home.state_dir() / "stream_requests") / f"{module}.json"


def clean_symbols(symbols) -> list[str]:
    """Deduped, uppercased, stripped, sorted — junk entries dropped rather than crashed on."""
    out: set[str] = set()
    for s in symbols or []:
        if isinstance(s, str) and s.strip():
            out.add(s.strip().upper())
    return sorted(out)


def clean_window_hints(window_hints) -> dict[str, int]:
    """Deduped/uppercased/validated ``{symbol: strike_count}`` — non-string symbols, non-positive or
    non-integer counts are dropped rather than crashed on, same posture as `clean_symbols`."""
    out: dict[str, int] = {}
    for symbol, count in (window_hints or {}).items():
        if isinstance(symbol, str) and symbol.strip() and isinstance(count, int) and count > 0:
            out[symbol.strip().upper()] = count
    return out


def write_request(module: str, symbols, legs=(), leg_sources=(), window_hints=None) -> Path:
    """Atomically (over)write a module's request file and return its path.

    Write-then-rename so a concurrent reader never sees a partial file. Raises on I/O failure —
    best-effort behavior (log and continue) belongs in the module's ``register()`` adapter, which
    knows its own logger.
    """
    path = request_path(module)
    payload = {
        "symbols": clean_symbols(symbols),
        "legs": [str(leg) for leg in legs],
        "leg_sources": [dict(source) for source in leg_sources],
        "window_hints": clean_window_hints(window_hints),
    }
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)
    return path
