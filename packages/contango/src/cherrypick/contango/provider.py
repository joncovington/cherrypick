"""Inputs, read-only: quotes and the VIX/VIX3M prints from the shared stream cache, and fund
distributions from the technicals store. This module writes neither.

Nothing here decides anything; a stale or missing input comes back as None, and the caller records
the refusal.

**Distributions come from the LOCAL technicals store** (`<data home>/technicals/history.db`, pulled
nightly from the Dolt clones), never from a network call: SHV pays a dividend every month, and it
is most of a T-bill fund's return, so a cash stint that ignored them would earn nothing and make
the risk arm look better than it is. The store's dividend rows can land a few days after an
ex-date; the credit lands with them, dated.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from cherrypick.core import home as _home
from cherrypick.core.db import connect_ro as _connect_ro
from cherrypick.core.streamcache import usable_quote as _usable_quote

DEFAULT_MAX_QUOTE_AGE_SECONDS = 120


def stream_cache_path(config: dict) -> str:
    configured = (config.get("source") or {}).get("stream_cache_db")
    if configured:
        return os.path.expanduser(os.path.expandvars(configured))
    return os.path.join(str(_home.home()), "data", "marketdata", "stream_cache.db")


def technicals_db_path(config: dict) -> str:
    configured = (config.get("source") or {}).get("technicals_db")
    if configured:
        return os.path.expanduser(os.path.expandvars(configured))
    return os.path.join(str(_home.home()), "data", "technicals", "history.db")


def read_quotes(db_path, symbols: list[str], *, max_quote_age_seconds: float) -> dict:
    """`{symbol: {"bid", "ask", "mid", "age_seconds"} | None}` -- None for a quote the suite's one
    usability rule rejects (stale, crossed, no ask)."""
    out: dict[str, dict | None] = {s: None for s in symbols}
    db_path = Path(db_path)
    if not db_path.exists() or not symbols:
        return out
    conn = _connect_ro(db_path)
    try:
        now_ts = time.time()
        marks = ", ".join("?" * len(symbols))
        for row in conn.execute(
            f"SELECT symbol, bid, ask, mid, updated_at FROM stream_quotes WHERE symbol IN ({marks})", symbols
        ):
            out[row["symbol"]] = _usable_quote(row, now_ts, max_quote_age_seconds)
        return out
    finally:
        conn.close()


def read_regime_prints(db_path) -> dict:
    """`{"vix": {"value", "age_seconds"} | None, "vix3m": ...}` from `stream_trades`: VIX and VIX3M
    are indexes and publish no quote, so the last print is the reading (how curve and overview read
    the same two symbols)."""
    out: dict[str, dict | None] = {"vix": None, "vix3m": None}
    db_path = Path(db_path)
    if not db_path.exists():
        return out
    conn = _connect_ro(db_path)
    try:
        now_ts = time.time()
        for key, symbol in (("vix", "VIX"), ("vix3m", "VIX3M")):
            row = conn.execute(
                "SELECT last, updated_at FROM stream_trades WHERE symbol = ?", (symbol,)
            ).fetchone()
            if row is None or row["last"] is None or row["updated_at"] is None:
                continue
            out[key] = {
                "value": float(row["last"]),
                "age_seconds": round(now_ts - float(row["updated_at"]), 1),
            }
        return out
    finally:
        conn.close()


def read_dividends(db_path, symbols: list[str], *, since: str, through: str) -> list[dict] | None:
    """Dividend rows `{"symbol", "ex_date", "amount"}` with `since < ex_date <= through`, or None
    when the store cannot be read (a missing store is a refusal to credit, never a zero)."""
    db_path = Path(db_path)
    if not db_path.exists() or not symbols:
        return None
    try:
        conn = _connect_ro(db_path)
    except sqlite3.Error:
        return None
    try:
        marks = ", ".join("?" * len(symbols))
        rows = conn.execute(
            f"SELECT symbol, ex_date, amount FROM dividends WHERE symbol IN ({marks}) "
            "AND ex_date > ? AND ex_date <= ? ORDER BY ex_date",
            [*symbols, since, through],
        ).fetchall()
        return [{"symbol": r["symbol"], "ex_date": r["ex_date"], "amount": float(r["amount"])} for r in rows]
    except sqlite3.Error:
        return None
    finally:
        conn.close()
