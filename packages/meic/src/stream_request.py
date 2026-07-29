"""Declare this module's stream needs so the standalone streamer keeps them fresh in the shared cache.

Writes ``~/.cherrypick/state/stream_requests/meic.json`` — the streamer reads the union across every
installed module and streams exactly that. Unlike flies (a pure underlying consumer), MEIC *does* need
``leg_sources``: an open IC's four option legs must stay subscribed after spot walks the ATM window away
from them, or their marks / per-side stops / force-closes price off frozen quotes. The entry points the
streamer at the PAPER ledger with the canonical open-trades query (same status set as ``db.py``'s
``get_open_trades``); the streamer re-runs it every subscription poll, so a newly opened structure's
legs subscribe within ~30s with no restart. Live is deliberately absent — the live loop doesn't exist
yet; when it does, a second entry pointing at ``paths.live_db_path()`` is one line.

Until 2026-07-29 this file did not exist and ``stream_requests/meic.json`` was hand-written at the
2026-07-21 cutover: it still listed all seven retired symbols and its leg query read the LIVE ledger
(whose open-trades query returns nothing), so open paper positions' legs were never explicitly
subscribed — they survived only while they happened to sit inside the ATM window. The width study made
that acute (~40–120 concurrent structures/day, all reading leg quotes), which is why this writer exists.

Best-effort by design: a failed write must never break the paper loop. An unregistered symbol is a
data-availability problem the readers already surface, not a reason to crash a scheduled run. Thin
sibling of ``flies/src/stream_request.py`` (candidate to consolidate into
``cherrypick.core.streamrequests`` later).
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE = _HERE / "_core"
if _CORE.is_dir() and str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from cherrypick.core import home as _home  # noqa: E402

import paths as _paths  # noqa: E402

_MODULE = "meic"
_log = logging.getLogger("paper_loop")

# The canonical open-trades status set (db.py get_open_trades) over the DDL's four leg columns. The
# streamer opens the DB read-only and treats every non-null result cell as a symbol to keep subscribed.
_LEG_QUERY = (
    "SELECT put_symbol, call_symbol, long_put_symbol, long_call_symbol FROM ic_trades "
    "WHERE status IN ('pending','open','partial','partial_entry')"
)


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
    payload = {
        "symbols": _clean_symbols(symbols),
        "legs": [],
        "leg_sources": [{"db": str(_paths.paper_db_path()), "query": _LEG_QUERY}],
    }
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)
    return path


def register(config: dict) -> None:
    """Best-effort: declare the configured ``symbols`` (and the paper ledger's open legs) to the
    streamer. Never raises into the caller."""
    try:
        symbols = config.get("symbols") or ([config["symbol"]] if config.get("symbol") else [])
        write(symbols)
    except Exception as exc:  # noqa: BLE001 — registration is advisory, never fatal to the loop
        _log.warning("stream request registration failed: %s", exc)
