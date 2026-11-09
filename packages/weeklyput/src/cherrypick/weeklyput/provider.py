"""Read-only views of the shared stream cache: one cycle's chain, an open put's mark, a posted close.

Opened `?mode=ro` through `cherrypick.core`, always; the streamer owns the cache. Nothing here
decides anything, and every read refuses rather than guesses: an expiry the cache does not hold, a
root that is not the PM-settled weekly, a stale quote, a close not yet posted.

**The chain** is the cycle's exact expiry, filtered to the configured OCC root (SPXW for SPX, so an
AM-settled product sharing the date can never be priced). Calls and puts both: the forward is
parity, so the at-the-money calls matter even though only a put is sold.

**The settlement print** is the expiry session's posted close from `stream_summary`, the
exchange's own daily close: that session's `day_close`, or the next session's `prev_day_close`,
which is how a live-written session's close usually arrives (`core.streamcache.daily_bars`). Never
the last trade: an ETF trades after the bell, and its 16:20 last print is not its close.
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

from cherrypick.core import calendar as _cal
from cherrypick.core import streamcache as _sc
from cherrypick.core.db import connect_ro as _connect_ro

DEFAULT_MAX_QUOTE_AGE_SECONDS = 120


def stream_cache_path(config: dict) -> str:
    return str(_sc.cache_path(config))


def _fail(symbol: str, reason: str, **extra) -> dict:
    return {"ok": False, "symbol": symbol, "reason": reason, **extra}


def cycle_chain(
    db_path,
    symbol: str,
    expiration: str,
    *,
    root: str,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
) -> dict:
    """{"ok": True, "spot", "chain": [(strike, right, bid, ask)], "symbols": {(strike, right):
    {streamer_symbol, occ_symbol}}, "quote_stats"} or a refusal."""
    symbol = symbol.strip().upper()
    path = Path(db_path)
    if not path.exists():
        return _fail(symbol, "stream_cache_missing")
    conn = _connect_ro(path)
    try:
        entries = _sc.chain_for_expiration(conn, symbol, expiration, root)
        if not entries:
            any_root = conn.execute(
                "SELECT COUNT(*) FROM stream_chain WHERE expiration = ? AND underlying_symbol = ?",
                (expiration, symbol),
            ).fetchone()[0]
            return _fail(symbol, "root_not_listed" if any_root else "no_chain")
        tr = conn.execute("SELECT last FROM stream_trades WHERE symbol = ?", (symbol,)).fetchone()
        spot = float(tr["last"]) if tr and tr["last"] is not None else None
        by_symbol = {e["streamer_symbol"]: e for e in entries}
        quotes, stale = _sc.usable_quotes(conn, list(by_symbol), time.time(), max_quote_age_seconds)
        if not quotes:
            return _fail(symbol, "no_fresh_quotes", rejected=stale)
        chain, symbols = [], {}
        for sym, q in quotes.items():
            e = by_symbol[sym]
            right = "C" if e["option_type"] == "call" else "P"
            chain.append((e["strike_price"], right, q.get("bid"), q.get("ask")))
            symbols[(e["strike_price"], right)] = {
                "streamer_symbol": sym,
                "occ_symbol": e["occ_symbol"],
            }
        return {
            "ok": True,
            "symbol": symbol,
            "spot": spot,
            "chain": chain,
            "symbols": symbols,
            "quote_stats": {"fresh": len(quotes), "rejected": stale, "listed": len(entries)},
        }
    finally:
        conn.close()


def band_put_symbols(
    db_path, symbol: str, expiration: str, *, root: str, spot: float, band: tuple[float, float]
) -> list[str]:
    """The streamer symbols of the listed puts between `band[0]` and `band[1]` below spot (fractions),
    from the chain metadata the cache holds for that expiry. Empty when the cache holds none yet."""
    path = Path(db_path)
    if not path.exists() or not spot:
        return []
    lo, hi = spot * (1 - band[1]), spot * (1 - band[0])
    conn = _connect_ro(path)
    try:
        entries = _sc.chain_for_expiration(conn, symbol.strip().upper(), expiration, root)
    finally:
        conn.close()
    return sorted(
        e["streamer_symbol"] for e in entries if e["option_type"] == "put" and lo <= e["strike_price"] <= hi
    )


def read_spot(db_path, symbol: str, *, max_age_seconds: float | None = None) -> float | None:
    return _sc.read_spot(db_path, symbol, max_age_seconds=max_age_seconds)


def leg_mark(db_path, streamer_symbol: str, *, max_quote_age_seconds: float) -> dict | None:
    """The open put's usable quote, or None."""
    path = Path(db_path)
    if not path.exists():
        return None
    conn = _connect_ro(path)
    try:
        quotes, _ = _sc.usable_quotes(conn, [streamer_symbol], time.time(), max_quote_age_seconds)
    finally:
        conn.close()
    return quotes.get(streamer_symbol)


def posted_close(db_path, symbol: str, session: date, *, today: date | None = None) -> float | None:
    """`session`'s posted close for `symbol`, or None until it has posted. `today` is the ET date
    (the caller's clock)."""
    path = Path(db_path)
    if not path.exists():
        return None
    conn = _connect_ro(path)
    try:
        rows = {
            r["trade_date"]: r
            for r in conn.execute(
                "SELECT trade_date, day_close, prev_day_close FROM stream_summary "
                "WHERE symbol = ? AND trade_date IN (?, ?)",
                (symbol.strip().upper(), session.isoformat(), _cal.next_trading_day(session).isoformat()),
            )
        }
    except Exception:  # noqa: BLE001 -- an unreadable table is "not posted", and the caller waits
        return None
    finally:
        conn.close()
    # The next session's prior close first: the exchange's settle, written once the session has
    # closed. A session's own `day_close` is trusted only once that session is over (the backfill
    # writes it then); on the expiry evening itself a live row's field may not be the close yet.
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
