"""Declare this module's stream needs: the funds it ladders, as underlyings, nothing else.

Writes `~/.cherrypick/state/stream_requests/ladder.json`. A fund needs its bid and ask (the fill
test) and its daily summary (the reference close). Each fund is declared as an underlying (Trade and
Summary) and also as a leg, because a leg is what the streamer subscribes to Quote; an underlying
alone can arrive with a last trade and no book (TQQQ, 2026-10-10). No
option chain is read, so `nearest_window` is false and no expiry or leg is named. A fund still held
after it leaves the config stays requested until its last lot sells, so its marks continue.

Best-effort: a failed write never breaks the loop.
"""

from __future__ import annotations

import logging

from cherrypick.core import streamrequests as _sr

_MODULE = "ladder"
_log = logging.getLogger("ladder_paper_loop")


def names(config: dict) -> dict[str, dict]:
    """{symbol: overrides} for every configured fund that is not switched off."""
    out = {}
    for sym, block in (config.get("names") or {}).items():
        if str(sym).startswith("_"):
            continue
        block = block if isinstance(block, dict) else {}
        if block.get("enabled") is False:
            continue
        out[str(sym).strip().upper()] = {k: v for k, v in block.items() if not str(k).startswith("_")}
    return out


def held_symbols(conn) -> list[str]:
    return [
        r[0]
        for r in conn.execute("SELECT DISTINCT symbol FROM ladder_positions WHERE status = 'open' ORDER BY 1")
    ]


def write(config: dict, conn, db_path: str):
    entering = list(names(config))
    syms = sorted(set(entering) | set(held_symbols(conn)))
    return _sr.write_request(
        _MODULE,
        syms,
        legs=syms,
        nearest_window={s: False for s in syms},
        entry_symbols=entering,
    )


def register(config: dict, conn, db_path: str) -> None:
    """Best-effort: never raises into the caller."""
    _sr.register_best_effort(write, config, conn, db_path, log=_log)
