"""Snapshot provider — turns the shared stream cache into what the engine and the regime read
consume. Read-only, always: this module never writes the cache, only the standalone streamer does.

Nothing here decides anything; it refuses rather than guesses on a stale or missing input, and a
refusal is ordinary telemetry the caller records, not an error.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from cherrypick.core.db import connect_ro as _connect_ro
from cherrypick.core.streamcache import chain_for_expiration as _chain_for_expiration
from cherrypick.core.streamcache import greeks_for as _greeks
from cherrypick.core.streamcache import occ_root, read_spot  # noqa: F401
from cherrypick.core.streamcache import usable_quote as _usable_quote

from cherrypick.curve.clock import now_et  # noqa: F401

DEFAULT_MAX_QUOTE_AGE_SECONDS = 300


def _fail(symbol: str, reason: str, **extra) -> dict:
    return {"ok": False, "symbol": symbol, "reason": reason, **extra}


def snapshot_kwargs(config: dict) -> dict:
    defaults = config.get("defaults", {}) or {}
    return {"max_quote_age_seconds": defaults.get("max_quote_age_seconds", DEFAULT_MAX_QUOTE_AGE_SECONDS)}


def _quotes_for(conn, streamer_syms: list[str], now_ts: float, max_age: float) -> tuple[dict, int]:
    quotes: dict[str, dict] = {}
    stale = 0
    for i in range(0, len(streamer_syms), 900):
        chunk = streamer_syms[i : i + 900]
        placeholders = ", ".join("?" * len(chunk))
        for row in conn.execute(
            f"SELECT symbol, bid, ask, mid, updated_at FROM stream_quotes WHERE symbol IN ({placeholders})",
            chunk,
        ):
            quote = _usable_quote(row, now_ts, max_age)
            if quote is None:
                stale += 1
                continue
            quotes[row["symbol"]] = quote
    return quotes, stale


def build_entry_snapshot(
    db_path,
    symbol: str,
    plan: dict,
    *,
    root: str,
    when: datetime | None = None,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
) -> dict:
    """Everything `engine.plan_entry` needs: the ATM/OTM call chain at the target expiration plus
    fresh quotes and greeks. `plan` is `clock.target_expiration`'s output."""
    symbol = symbol.strip().upper()
    db_path = Path(db_path)
    when = when or now_et()
    if not db_path.exists():
        return _fail(symbol, "stream_cache_missing")

    conn = _connect_ro(db_path)
    try:
        tr = conn.execute("SELECT last FROM stream_trades WHERE symbol = ?", (symbol,)).fetchone()
        spot = float(tr["last"]) if tr and tr["last"] is not None else None
        if not spot:
            return _fail(symbol, "no_spot_price")

        chain = _chain_for_expiration(conn, symbol, plan["expiration"], root)
        if not chain:
            any_root = conn.execute(
                "SELECT COUNT(*) FROM stream_chain WHERE expiration = ? AND underlying_symbol = ?",
                (plan["expiration"], symbol),
            ).fetchone()[0]
            return _fail(symbol, "not_root_listed" if any_root else "no_chain")

        now_ts = time.time()
        syms = [e["streamer_symbol"] for e in chain if e["option_type"] == "call"]
        quotes, stale = _quotes_for(conn, syms, now_ts, max_quote_age_seconds)
        if not quotes:
            return _fail(symbol, "no_fresh_quotes", rejected=stale)
        greeks = _greeks(conn, list(quotes), now_ts=now_ts, max_age_seconds=max_quote_age_seconds * 6)

        return {
            "ok": True,
            "symbol": symbol,
            "date": when.date().isoformat(),
            "spot": spot,
            "expiration": plan["expiration"],
            "dte": plan["dte"],
            "chain": chain,
            "quotes": quotes,
            "greeks": greeks,
            "quote_stats": {"fresh": len(quotes), "rejected": stale},
        }
    finally:
        conn.close()


def build_mark_snapshot(
    db_path,
    legs: list[dict],
    *,
    when: datetime | None = None,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
) -> dict:
    """Per-leg quotes + spot for a position's open legs — the marking substrate. `ok` means EVERY leg
    is priceable; a partial mark is still recorded as a refusal row by the caller."""
    when = when or now_et()
    db_path = Path(db_path)
    out: dict = {
        "ok": False,
        "spot": None,
        "quotes": {},
        "fresh": 0,
        "stale": 0,
        "max_spread_pct": None,
        "leg_spreads": [],
    }
    if not legs:
        return {**out, "reason": "no_legs"}
    if not db_path.exists():
        return {**out, "reason": "stream_cache_missing"}

    conn = _connect_ro(db_path)
    try:
        symbol = (legs[0].get("position_symbol") or legs[0].get("symbol") or "").strip().upper()
        tr = conn.execute("SELECT last FROM stream_trades WHERE symbol = ?", (symbol,)).fetchone()
        out["spot"] = float(tr["last"]) if tr and tr["last"] is not None else None

        now_ts = time.time()
        streamer_syms = [leg["streamer_symbol"] for leg in legs]
        placeholders = ", ".join("?" * len(streamer_syms))
        rows = {
            r["symbol"]: r
            for r in conn.execute(
                f"SELECT symbol, bid, ask, mid, updated_at FROM stream_quotes WHERE symbol IN ({placeholders})",
                streamer_syms,
            )
        }
        widest = None
        for sym in streamer_syms:
            row = rows.get(sym)
            quote = _usable_quote(row, now_ts, max_quote_age_seconds) if row is not None else None
            out["quotes"][sym] = quote
            if quote is None:
                out["stale"] += 1
                continue
            out["fresh"] += 1
            if quote["mid"] > 0:
                spread_pct = (quote["ask"] - quote["bid"]) / quote["mid"]
                widest = spread_pct if widest is None else max(widest, spread_pct)
                # Both readings of the same width, PER LEG: a percentage alone cannot tell a
                # genuinely illiquid leg from a nearly-worthless one (0.00/0.01 is a 200% ratio and
                # a one-cent width), and the two maxima can sit on different legs.
                out["leg_spreads"].append(
                    {"symbol": sym, "pct": round(spread_pct, 4), "abs": round(quote["ask"] - quote["bid"], 4)}
                )
        out["max_spread_pct"] = round(widest, 4) if widest is not None else None

        if out["spot"] is None:
            return {**out, "reason": "no_spot_price"}
        if out["stale"]:
            return {**out, "reason": "missing_leg_quotes"}
        return {**out, "ok": True}
    finally:
        conn.close()


def read_regime_quotes(db_path, *, max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS) -> dict:
    """`{"vix": {...}, "vix3m": {...}}` live-quote readings for `regime.reading`, or a value-less
    dict per symbol when the cache holds nothing usable — `regime.reading` refuses on that, never
    guesses. Quote-only legs (VIX/VIX3M carry no chain here), so this reads `stream_trades` (the
    last-traded print) rather than `stream_quotes`, matching how `overview` reads the same two
    symbols pre-open."""
    db_path = Path(db_path)
    out = {"vix": None, "vix3m": None}
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
