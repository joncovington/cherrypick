"""Snapshot provider — turns the shared stream cache into what the engine and the marker consume.

Read-only (`?mode=ro`), always. The standalone streamer daemon owns that database; this module only
ever reads it, so running calendars can never disturb a producer someone else depends on. Chain
metadata for the two calendar expirations exists in the cache because this module's stream request
declares them via the `expirations` field — there is no broker fallback here, deliberately: the
paper path is credential-free (the flies posture), and a missing chain is a data-availability
refusal the loop records, not a reason to grow a REST client.

Nothing here decides anything. The provider's job is a snapshot that is fresh, complete, and
honestly labelled — and a refusal rather than a guess when it isn't. Refusals are ordinary and
frequent (a streamer still warming up, a next-Monday expiration not listed yet); they are recorded
by the caller and are not errors.

The OCC-root filter is load-bearing: on a third-Friday week the cache can hold BOTH the AM-settled
monthly (root `SPX`) and the PM-settled weekly (root `SPXW`) for the same expiration date, and the
whole suite's settlement model assumes PM. Only entries whose OCC root matches the configured root
survive; if none do, the refusal is `not_weekly_listed`, which is the skip-this-week signal.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

# Read-only opens go through cherrypick.core.db.connect_ro: it percent-escapes the path, so a
# directory containing '?', '#' or '%' cannot silently change the URI's meaning. The local
# copies interpolated the path raw, where a '#' truncated the URI and opened a DIFFERENT,
# empty database — which a provider reports as "nothing cached" rather than as an error.
from cherrypick.core.db import connect_ro as _connect_ro

# occ_root is re-exported: the modules' own code calls provider.occ_root.
from cherrypick.core.streamcache import chain_for_expiration as _chain_for_expiration
from cherrypick.core.streamcache import greeks_for as _greeks

# Shared with every other provider — see cherrypick.core.streamcache.
# read_spot is re-exported deliberately: the loops call provider.read_spot and their tests
# monkeypatch it there, so it is unused *inside* this module and ruff will drop it otherwise.
from cherrypick.core.streamcache import (
    occ_root,  # noqa: F401
    read_spot,  # noqa: F401
)
from cherrypick.core.streamcache import usable_quote as _usable_quote

from cherrypick.calendars.clock import now_et  # noqa: F401  (re-exported for the loop's convenience)

DEFAULT_MAX_QUOTE_AGE_SECONDS = 300
DEFAULT_STRIKE_WINDOW_PCT = 0.04




def _fail(symbol: str, reason: str, **extra) -> dict:
    """A refusal, not an error — `extra` carries the telemetry that explains it afterwards."""
    return {"ok": False, "symbol": symbol, "reason": reason, **extra}






def snapshot_kwargs(config: dict) -> dict:
    """The data-quality knobs read out of config's `defaults` — one helper so every call site
    applies the identical freshness limits (the flies four-call-sites lesson)."""
    defaults = config.get("defaults", {}) or {}
    return {
        "max_quote_age_seconds": defaults.get("max_quote_age_seconds", DEFAULT_MAX_QUOTE_AGE_SECONDS),
    }




def build_entry_snapshot(
    db_path,
    symbol: str,
    front_expiration: str,
    back_expiration: str,
    *,
    root: str,
    when: datetime | None = None,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
    strike_window_pct: float = DEFAULT_STRIKE_WINDOW_PCT,
) -> dict:
    """Everything `engine.plan_entry` needs for one symbol, or a refusal.

    The two expirations are the caller's COMPUTED dates — the query is an exact match, so the
    MEIC-style silent-nearest-fallback trap cannot occur by construction: a date the cache does not
    hold is a `no_front_chain`/`no_back_chain` refusal, never a different date.
    """
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

        front = _chain_for_expiration(conn, symbol, front_expiration, root)
        back = _chain_for_expiration(conn, symbol, back_expiration, root)
        # Distinguish "the cache has no rows for that date" from "it has rows but none are the
        # configured weekly root" — the second is the skip-this-week signal on a third-Friday week.
        if not front or not back:
            missing = "no_front_chain" if not front else "no_back_chain"
            any_root = conn.execute(
                "SELECT COUNT(*) FROM stream_chain WHERE expiration = ? AND underlying_symbol = ?",
                (front_expiration if not front else back_expiration, symbol),
            ).fetchone()[0]
            return _fail(symbol, "not_weekly_listed" if any_root else missing)

        # Quotes for both chains, bounded to strikes near spot — an EM landing outside this window
        # would be extraordinary (a 4DTE EM runs ~1-2% of spot against the 4% default).
        window = strike_window_pct * spot
        near = [e for e in front + back if abs(e["strike_price"] - spot) <= window]
        if not near:
            return _fail(symbol, "no_strikes_near_spot")
        now_ts = time.time()
        quotes: dict[str, dict] = {}
        stale = 0
        by_symbol = {e["streamer_symbol"]: e for e in near}
        for i in range(0, len(by_symbol), 900):
            chunk = list(by_symbol)[i : i + 900]
            placeholders = ", ".join("?" * len(chunk))
            for row in conn.execute(
                f"SELECT symbol, bid, ask, mid, updated_at FROM stream_quotes "
                f"WHERE symbol IN ({placeholders})",
                chunk,
            ):
                quote = _usable_quote(row, now_ts, max_quote_age_seconds)
                if quote is None:
                    stale += 1
                    continue
                quotes[row["symbol"]] = quote
        if not quotes:
            return _fail(symbol, "no_fresh_quotes", rejected=stale)

        greeks = _greeks(conn, list(quotes), now_ts=now_ts, max_age_seconds=max_quote_age_seconds * 6)

        return {
            "ok": True,
            "symbol": symbol,
            "date": when.date().isoformat(),
            "spot": spot,
            "front_expiration": front_expiration,
            "back_expiration": back_expiration,
            "front": front,
            "back": back,
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
    """Per-leg quotes + greeks + spot for a position's open legs — the marking substrate.

    Always returns per-leg detail (None for an unusable leg) so a partial mark is still recorded
    as refusal rows; `ok` is position-level and means EVERY leg is priceable, which is the bar for
    acting (a side close priced off one good leg and one guess is not a measurement).
    """
    when = when or now_et()
    db_path = Path(db_path)
    out: dict = {
        "ok": False,
        "spot": None,
        "quotes": {},
        "greeks": {},
        "fresh": 0,
        "stale": 0,
        "max_spread_pct": None,
    }
    if not legs:
        return {**out, "reason": "no_legs"}
    if not db_path.exists():
        return {**out, "reason": "stream_cache_missing"}

    conn = _connect_ro(db_path)
    try:
        symbol = (legs[0].get("position_symbol") or legs[0].get("symbol") or "SPX").strip().upper()
        tr = conn.execute("SELECT last FROM stream_trades WHERE symbol = ?", (symbol,)).fetchone()
        out["spot"] = float(tr["last"]) if tr and tr["last"] is not None else None

        now_ts = time.time()
        streamer_syms = [leg["streamer_symbol"] for leg in legs]
        placeholders = ", ".join("?" * len(streamer_syms))
        rows = {
            r["symbol"]: r
            for r in conn.execute(
                f"SELECT symbol, bid, ask, mid, updated_at FROM stream_quotes "
                f"WHERE symbol IN ({placeholders})",
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
        out["greeks"] = _greeks(conn, streamer_syms, now_ts=now_ts, max_age_seconds=max_quote_age_seconds * 6)

        if out["spot"] is None:
            return {**out, "reason": "no_spot_price"}
        if out["stale"]:
            return {**out, "reason": "missing_leg_quotes"}
        return {**out, "ok": True}
    finally:
        conn.close()




