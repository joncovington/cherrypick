"""The subscription registry — how installed modules tell the streamer what to keep in the cache.

Each consumer module writes ONE file, ``~/.cherrypick/state/stream_requests/<module>.json``:

    {
      "symbols": ["SPX", "XSP"],
      "leg_sources": [
        {"db": "~/.cherrypick/data/meic/meic_trades.db",
         "query": "SELECT put_symbol, call_symbol, long_put_symbol, long_call_symbol
                   FROM ic_trades WHERE status IN ('pending','open','partial','partial_entry')"}
      ],
      "legs": []
    }

- ``symbols``: underlyings the module needs a spot + ATM option window (and GEX / opening-range) for.
- ``leg_sources``: DBs the streamer should pull *dynamic* extra streamer-symbols from — the general,
  configurable form of "keep the legs of my open positions subscribed." Each is a ``{db, query}`` pair;
  the streamer opens ``db`` **read-only** and runs the ``SELECT``, treating every non-null cell of the
  result as a streamer-symbol to subscribe beyond the ATM window. This is how MEIC keeps its open IC legs
  fresh (its symbols are stored columns of ``ic_trades``); any module points the streamer at its own DB
  the same way. Re-run each subscription poll, so a position opening/closing is picked up with no restart.
- ``legs``: an optional explicit static list, for a module that would rather name symbols directly than
  provide a query. Rarely needed; most modules use ``leg_sources`` or nothing.

The streamer reads the **union** across every module's file and streams exactly that. Ownership: the
streamer only ever *reads* these request files and opens the declared DBs **read-only**; it writes
neither. A consumer writes only *its own* ``<module>.json``. So the cache stays producer-write-only and
no package imports another — the registry (plus the module-owned SQL) is the entire coupling surface.
See ``docs/streamer-package-plan.md``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

_CORE = Path(__file__).resolve().parent / "_core"
if _CORE.is_dir() and str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from cherrypick.core import home as _home  # noqa: E402

# A leg-source query gets a short read-only window; a locked/slow trades DB must not stall the poll.
_LEG_QUERY_TIMEOUT_S = 2.0


def requests_dir() -> Path:
    """The directory holding one request file per module (``~/.cherrypick/state/stream_requests``)."""
    return _home.state_dir() / "stream_requests"


def request_path(module: str) -> Path:
    return requests_dir() / f"{module}.json"


def _clean(values, *, upper: bool) -> list[str]:
    out: set[str] = set()
    for v in values or []:
        if isinstance(v, str) and v.strip():
            out.add(v.strip().upper() if upper else v.strip())
    return sorted(out)


def _clean_sources(sources) -> list[dict]:
    out: list[dict] = []
    for s in sources or []:
        if isinstance(s, dict) and isinstance(s.get("db"), str) and isinstance(s.get("query"), str):
            out.append({"db": s["db"], "query": s["query"]})
    return out


def write_request(module: str, symbols, legs=None, leg_sources=None) -> Path:
    """Consumer-side: (over)write this module's request file atomically.

    Called by a module at startup with its underlyings and (if it has open positions) the ``leg_sources``
    pointing at its trades DB. Atomic write-then-rename so a concurrent reader never sees a partial file.
    The reference writer — a consumer that cannot import this package carries an equivalent.
    """
    directory = _home.ensure(requests_dir())
    path = directory / f"{module}.json"
    payload = {
        "symbols": _clean(symbols, upper=True),
        "legs": _clean(legs, upper=False),
        "leg_sources": _clean_sources(leg_sources),
    }
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)  # atomic on the same filesystem (Windows + POSIX)
    return path


def _read_all() -> list[dict]:
    """Every module's request dict, skipping any file that is missing/half-written/corrupt (never fatal —
    a bad request file must not be able to take the streamer down)."""
    out: list[dict] = []
    directory = requests_dir()
    if directory.is_dir():
        for f in sorted(directory.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                out.append(data)
    return out


def union_symbols(seed_symbols=None) -> list[str]:
    """Underlyings to stream: the union of every module's ``symbols`` plus operator-seeded base symbols."""
    symbols: set[str] = set(_clean(seed_symbols, upper=True))
    for data in _read_all():
        symbols.update(_clean(data.get("symbols"), upper=True))
    return sorted(symbols)


def union_legs() -> list[str]:
    """Extra streamer-symbols to keep subscribed: explicit ``legs`` plus everything the declared
    ``leg_sources`` queries currently return, unioned across all modules."""
    legs: set[str] = set()
    for data in _read_all():
        legs.update(_clean(data.get("legs"), upper=False))
        for src in data.get("leg_sources") or []:
            legs.update(_legs_from_source(src))
    return sorted(legs)


def _is_single_select(query: str) -> bool:
    q = query.strip().rstrip(";").strip()
    if not q or ";" in q:  # one statement only
        return False
    return q[:6].lower() == "select"


def _legs_from_source(src) -> list[str]:
    """Open a module-declared DB read-only and collect streamer-symbols from the ``SELECT`` it provides.

    Every non-null string cell in the result set is treated as a streamer-symbol. Never fatal — a missing
    DB, a non-SELECT query, a bad/locked DB just contributes nothing this poll (the streamer keeps running
    on whatever the other sources return).
    """
    if not isinstance(src, dict):
        return []
    db, query = src.get("db"), src.get("query")
    if not isinstance(db, str) or not isinstance(query, str) or not _is_single_select(query):
        return []
    path = os.path.expanduser(os.path.expandvars(db))
    if not os.path.exists(path):
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=_LEG_QUERY_TIMEOUT_S)
        try:
            rows = conn.execute(query).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    out: list[str] = []
    for row in rows:
        for cell in row:
            if isinstance(cell, str) and cell.strip():
                out.append(cell.strip())
    return out
