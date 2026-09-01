"""Snapshot provider — turns the shared stream cache into what the engine, triggers, and the mark
path consume. Read-only, always: this module never writes the cache, only the standalone streamer
does.

Nothing here decides anything; it refuses rather than guesses on a stale or missing input, and a
refusal is ordinary telemetry the caller records, not an error.

`gamma_flip_reading` recomputes the flip fresh each tick from the stream cache via
`cherrypick.core.gex` — the same basis MEIC's negative-GEX gate reads, NOT the GEX recorder's own
~5-min history, so a stalled recorder can never silently freeze this module's trigger.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from cherrypick.core import gex as _gex
from cherrypick.core.db import connect_ro as _connect_ro
from cherrypick.core.streamcache import chain_for_expiration as _chain_for_expiration
from cherrypick.core.streamcache import greeks_for as _greeks
from cherrypick.core.streamcache import occ_root, read_spot  # noqa: F401
from cherrypick.core.streamcache import usable_quote as _usable_quote

from cherrypick.bwb.clock import now_et  # noqa: F401

DEFAULT_MAX_QUOTE_AGE_SECONDS = 300
GAMMA_FLIP_BASIS = "live_stream_cache"


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
    """Everything `engine.plan_entry` needs: the full put+call chain at the target expiration (calls
    for the ATM straddle read, puts for the BWB itself) plus fresh quotes and greeks."""
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
        syms = [e["streamer_symbol"] for e in chain]
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
    """Per-leg quotes + spot for a position's open legs — the marking substrate. `ok` means EVERY
    leg is priceable; a partial mark is still recorded as a refusal row by the caller."""
    when = when or now_et()
    db_path = Path(db_path)
    out: dict = {"ok": False, "spot": None, "quotes": {}, "fresh": 0, "stale": 0, "max_spread_pct": None}
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
        out["max_spread_pct"] = round(widest, 4) if widest is not None else None

        if out["spot"] is None:
            return {**out, "reason": "no_spot_price"}
        if out["stale"]:
            return {**out, "reason": "missing_leg_quotes"}
        return {**out, "ok": True}
    finally:
        conn.close()


def gamma_flip_reading(db_path, symbol: str, expiration: str, root: str, *, max_age_seconds: float) -> dict:
    """The live gamma_flip read for the trigger tick — recomputed fresh from the stream cache's own
    chain/greeks/OI, the same basis MEIC's own gate reads. `{"ok": False, "reason": ...}` on any
    missing input (no chain, no OI cached yet, no spot) — a trigger can only fire on a MEASURED
    tick, never a guess."""
    db_path = Path(db_path)
    if not db_path.exists():
        return {"ok": False, "reason": "stream_cache_missing"}
    conn = _connect_ro(db_path)
    try:
        tr = conn.execute("SELECT last FROM stream_trades WHERE symbol = ?", (symbol,)).fetchone()
        spot = float(tr["last"]) if tr and tr["last"] is not None else None
        if not spot:
            return {"ok": False, "reason": "no_spot_price"}
        chain = _chain_for_expiration(conn, symbol, expiration, root)
        if not chain:
            return {"ok": False, "reason": "no_chain"}
        now_ts = time.time()
        syms = [e["streamer_symbol"] for e in chain]
        greeks = _greeks(conn, syms, now_ts=now_ts, max_age_seconds=max_age_seconds * 6)
        oi = {}
        placeholders = ", ".join("?" * len(syms)) if syms else ""
        if syms:
            for r in conn.execute(
                f"SELECT symbol, open_interest FROM stream_oi WHERE symbol IN ({placeholders})", syms
            ):
                if r["open_interest"] is not None:
                    oi[r["symbol"]] = r["open_interest"]
        result = _gex.compute_gex(chain, greeks, oi, spot)
        if not result.get("ok"):
            return {"ok": False, "reason": "insufficient_gex_data"}
        return {
            "ok": True,
            "gamma_flip": result.get("gamma_flip"),
            # Same compute, one more field: the wall book's placement level. Riding this reading
            # rather than a second one keeps the wall on the identical basis as the flip book's
            # trigger — one number, one provenance, no second source to drift.
            "call_wall": result.get("call_wall"),
            "spot": spot,
            "basis": GAMMA_FLIP_BASIS,
        }
    finally:
        conn.close()
