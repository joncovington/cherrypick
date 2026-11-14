"""Read-only views of the shared stream cache: a symbol's spot, its daily bars, one expiry's quote rows, an
open position's legs, a posted close.

Opened `?mode=ro` through `cherrypick.core`, always; the streamer owns the cache and this module never
writes it. Nothing here decides anything, and every read refuses rather than guesses: a quote older than
the bound, a crossed one, a chain the cache does not hold, a close not yet posted. What it returns has the
shape `snapshot.build` takes (quote dicts with `strike`, `right`, `bid`, `ask`, and `delta`, `gamma`, `iv`
and `oi` where the cache holds them), so the paper path and the backtest build the same snapshot.

**The settlement print** is the expiry session's posted close from `stream_summary`: the next session's
`prev_day_close`, or the session's own `day_close` once it is over. Never a last trade, which for an ETF
runs after the bell.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import date
from pathlib import Path

from cherrypick.core import calendar as _cal
from cherrypick.core import streamcache as _sc
from cherrypick.core.db import connect_ro as _connect_ro

#: Greeks and open interest move slowly; a Greeks row may be this many times older than a quote.
GREEKS_AGE_FACTOR = 6
_CHUNK = 900


def stream_cache_path(config: dict) -> str:
    return str(_sc.cache_path(config))


def _open(db_path):
    path = Path(db_path)
    return _connect_ro(path) if path.exists() else None


def read_spot(db_path, symbol: str, *, max_age_seconds: float | None = None) -> float | None:
    return _sc.read_spot(db_path, symbol, max_age_seconds=max_age_seconds)


def daily_bars(db_path, symbol: str, today: date) -> list[dict]:
    """Completed daily bars before `today`, oldest first (`core.streamcache.daily_bars`)."""
    conn = _open(db_path)
    if conn is None:
        return []
    try:
        return _sc.daily_bars(conn, symbol, through=today.isoformat())
    finally:
        conn.close()


def day_open(db_path, symbol: str, session: date) -> float | None:
    """The session's open from `stream_summary`, when the feed has written it."""
    conn = _open(db_path)
    if conn is None:
        return None
    try:
        r = conn.execute(
            "SELECT day_open FROM stream_summary WHERE symbol = ? AND trade_date = ?",
            (symbol.upper(), session.isoformat()),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return float(r["day_open"]) if r and r["day_open"] else None


def listed_expirations(db_path, symbol: str) -> list[str]:
    """The expirations the broker listed for `symbol` at the producer's last full-chain fetch."""
    conn = _open(db_path)
    if conn is None:
        return []
    try:
        return _sc.listed_expirations(conn, symbol.upper())[0]
    finally:
        conn.close()


def _open_interest(conn, symbols: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in range(0, len(symbols), _CHUNK):
        chunk = symbols[i : i + _CHUNK]
        marks = ", ".join("?" * len(chunk))
        try:
            for r in conn.execute(
                f"SELECT symbol, open_interest FROM stream_oi WHERE symbol IN ({marks})", chunk
            ):
                if r["open_interest"] is not None:
                    out[r["symbol"]] = int(r["open_interest"])
        except sqlite3.Error:
            return out
    return out


def _rows_for(
    conn, entries: list[dict], now_ts: float, max_age: float, *, greeks: bool
) -> tuple[list[dict], dict]:
    by_symbol = {e["streamer_symbol"]: e for e in entries}
    quotes, rejected = _sc.usable_quotes(conn, list(by_symbol), now_ts, max_age)
    gk = (
        _sc.greeks_for(conn, list(quotes), now_ts=now_ts, max_age_seconds=max_age * GREEKS_AGE_FACTOR)
        if greeks
        else {}
    )
    oi = _open_interest(conn, list(quotes)) if greeks else {}
    rows = []
    for sym, q in quotes.items():
        e = by_symbol[sym]
        g = gk.get(sym) or {}
        row = {
            "strike": e["strike_price"],
            "right": "C" if e["option_type"] == "call" else "P",
            "bid": q["bid"],
            "ask": q["ask"],
            "delta": g.get("delta"),
            "gamma": g.get("gamma"),
            "iv": g.get("iv"),
            "oi": oi.get(sym),
            "streamer_symbol": sym,
            "occ_symbol": e["occ_symbol"],
        }
        rows.append({k: v for k, v in row.items() if v is not None})
    rows.sort(key=lambda r: (r["strike"], r["right"]))
    return rows, {"fresh": len(quotes), "rejected": rejected, "listed": len(entries)}


def chain_rows(
    db_path, symbol: str, expiration: str, *, root: str, max_age_seconds: float, greeks: bool = True
) -> dict:
    """{"ok": True, "rows": [...], "quote_stats"} for one expiry's whole cached chain, filtered to the OCC
    root, or {"ok": False, "reason"}. Calls and puts both."""
    conn = _open(db_path)
    if conn is None:
        return {"ok": False, "reason": "stream_cache_missing"}
    try:
        entries = _sc.chain_for_expiration(conn, symbol.upper(), expiration, root)
        if not entries:
            return {"ok": False, "reason": "no_chain"}
        rows, stats = _rows_for(conn, entries, time.time(), max_age_seconds, greeks=greeks)
        if not rows:
            return {"ok": False, "reason": "no_fresh_quotes", "quote_stats": stats}
        return {"ok": True, "rows": rows, "quote_stats": stats}
    finally:
        conn.close()


def leg_rows(db_path, legs: list[dict], *, max_age_seconds: float) -> list[dict] | None:
    """Quote rows for a position's legs (ledger rows with `strike`, `right`, `streamer_symbol`), or None
    when any leg has no usable quote: a mark or a close priced off part of a structure is a guess."""
    conn = _open(db_path)
    if conn is None or not legs:
        return None
    try:
        syms = [g["streamer_symbol"] for g in legs]
        quotes, _ = _sc.usable_quotes(conn, syms, time.time(), max_age_seconds)
    finally:
        conn.close()
    if any(s not in quotes for s in syms):
        return None
    return [
        {
            "strike": g["strike"],
            "right": g["right"],
            "bid": quotes[g["streamer_symbol"]]["bid"],
            "ask": quotes[g["streamer_symbol"]]["ask"],
        }
        for g in legs
    ]


def posted_close(db_path, symbol: str, session: date, *, today: date | None = None) -> float | None:
    """`session`'s posted close for `symbol`, or None until it has posted. `today` is the ET date
    (the caller's clock)."""
    conn = _open(db_path)
    if conn is None:
        return None
    try:
        rows = {
            r["trade_date"]: r
            for r in conn.execute(
                "SELECT trade_date, day_close, prev_day_close FROM stream_summary "
                "WHERE symbol = ? AND trade_date IN (?, ?)",
                (symbol.strip().upper(), session.isoformat(), _cal.next_trading_day(session).isoformat()),
            )
        }
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    # The next session's prior close first: the exchange's settle, written once the session has closed.
    # A session's own `day_close` is trusted only once that session is over: on the expiry evening a
    # live row's field may not be the close yet.
    nxt = rows.get(_cal.next_trading_day(session).isoformat())
    if nxt is not None and nxt["prev_day_close"] is not None and float(nxt["prev_day_close"]) > 0:
        return float(nxt["prev_day_close"])
    own = rows.get(session.isoformat())
    if (
        session < (today or date.today())
        and own is not None
        and own["day_close"] is not None
        and float(own["day_close"]) > 0
    ):
        return float(own["day_close"])
    return None
