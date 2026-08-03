"""The user-curated watchlist — ``~/.cherrypick/data/scout/watchlist.json``.

Scout's universe is the watchlist plus earnings-calendar names (see ``calendar_service``, M2), never
the whole market. This module owns the one file: atomic tmp+``os.replace`` writes (so a concurrent
reader — the SSE poller — never sees a partial file), symbol cleaning, and the CLI/API share it so
``run.py watchlist add`` and a browser POST can never disagree about what's on the list.

On every change this also best-effort registers scout's symbols with the shared streamer via
``cherrypick.core.streamrequests.write_request`` — free ecosystem cache-warming if the standalone
streamer producer is running. Scout never requires it and a failed registration must not break a
watchlist edit.
"""

from __future__ import annotations

import json
from pathlib import Path


def clean_symbols(symbols) -> list[str]:
    """Deduped, uppercased, stripped, sorted — junk entries dropped rather than crashed on. Mirrors
    ``cherrypick.core.streamrequests.clean_symbols`` so the two never disagree about what a valid
    symbol looks like."""
    out: set[str] = set()
    for s in symbols or []:
        if isinstance(s, str) and s.strip():
            out.add(s.strip().upper())
    return sorted(out)


def load(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return clean_symbols(data.get("symbols") if isinstance(data, dict) else data)


def save(path: Path, symbols: list[str]) -> list[str]:
    cleaned = clean_symbols(symbols)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps({"symbols": cleaned}), encoding="utf-8")
    tmp.replace(path)
    _register_stream_request(cleaned)
    return cleaned


def add(path: Path, symbols) -> list[str]:
    return save(path, load(path) + list(symbols or []))


def remove(path: Path, symbols) -> list[str]:
    drop = set(clean_symbols(symbols))
    return save(path, [s for s in load(path) if s not in drop])


def _register_stream_request(symbols: list[str]) -> None:
    """Best-effort — a failed write must never break a watchlist edit or a CLI read."""
    try:
        from cherrypick.core import streamrequests

        streamrequests.write_request("scout", symbols)
    except Exception:
        pass
