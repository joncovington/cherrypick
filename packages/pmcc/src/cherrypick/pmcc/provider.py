"""Snapshot provider — turns the shared stream cache into what the engine and the marker consume.

Read-only (`?mode=ro`), always. The standalone streamer daemon owns that database; this module only
ever reads it, so running pmcc can never disturb a producer someone else depends on. Chain metadata
for the two computed expirations exists in the cache because this module's stream request declares
them via the `expirations` field — there is no broker fallback here, deliberately: the paper path
is credential-free (the calendars posture), and a missing chain is a data-availability refusal the
loop records, not a reason to grow a REST client.

Nothing here decides anything. The provider's job is a snapshot that is fresh, complete, and
honestly labelled — and a refusal rather than a guess when it isn't. Refusals are ordinary and
frequent (a streamer still warming up, a deep strike outside the producer's quote window); they are
recorded by the caller and are not errors.

The quote window here is ONE-SIDED AND DEEP, unlike every sibling module's symmetric ATM band: the
long leg (85-90 delta since the 2026-08-23 redesign) lives noticeably below spot, so the entry fetch
spans `[spot × (1 − deep_window_pct), spot × 1.02]`. Whether those strikes actually carry quotes depends
on the producer honoring this module's `window_hints` — a gap there surfaces as `no_deep_itm_long`
refusals, which is exactly the signal `stream_window.py` escalates on.

The OCC-root filter is kept from calendars for a different reason: leveraged ETFs split often, and
a split leaves ADJUSTED roots (`TNA1`) sharing expirations with the standard root. Only the
configured root is admitted; an expiration listing only adjusted roots refuses `not_root_listed`.
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

from cherrypick.pmcc.clock import now_et  # noqa: F401  (re-exported for the loop's convenience)

DEFAULT_MAX_QUOTE_AGE_SECONDS = 300
# The 85-90-delta long (2026-08-23 redesign) sits far shallower than the old ~99-delta long, so the
# deep window needed to see it is much narrower — 0.20 comfortably covers an 85-delta TQQQ strike
# with margin, versus the old design's 0.45.
DEFAULT_DEEP_WINDOW_PCT = 0.20


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
    deep_window_pct: float = DEFAULT_DEEP_WINDOW_PCT,
) -> dict:
    """Everything `engine.plan_entry` needs for one symbol, or a refusal.

    `plan` is `clock.expiration_plan`'s output — the two COMPUTED dates. The chain query is an
    exact match, so the MEIC-style silent-nearest-fallback trap cannot occur by construction: a
    date the cache does not hold is a `no_short_chain`/`no_long_chain` refusal, never a different
    date.
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

        short_chain = _chain_for_expiration(conn, symbol, plan["short_expiration"], root)
        long_chain = _chain_for_expiration(conn, symbol, plan["long_expiration"], root)
        if not short_chain or not long_chain:
            missing = "no_short_chain" if not short_chain else "no_long_chain"
            date = plan["short_expiration"] if not short_chain else plan["long_expiration"]
            any_root = conn.execute(
                "SELECT COUNT(*) FROM stream_chain WHERE expiration = ? AND underlying_symbol = ?",
                (date, symbol),
            ).fetchone()[0]
            return _fail(symbol, "not_root_listed" if any_root else missing)

        # Quotes for both chains, bounded to the deep one-sided window: the long lives 30–45% below
        # spot, the short between the long and spot, and nothing above spot matters beyond a small
        # sanity margin.
        lo, hi = spot * (1.0 - deep_window_pct), spot * 1.02
        near = [e for e in short_chain + long_chain if lo <= e["strike_price"] <= hi]
        if not near:
            return _fail(symbol, "no_strikes_in_window")
        now_ts = time.time()
        by_symbol = {e["streamer_symbol"]: e for e in near}
        quotes, stale = _quotes_for(conn, list(by_symbol), now_ts, max_quote_age_seconds)
        if not quotes:
            return _fail(symbol, "no_fresh_quotes", rejected=stale)

        greeks = _greeks(conn, list(quotes), now_ts=now_ts, max_age_seconds=max_quote_age_seconds * 6)

        return {
            "ok": True,
            "symbol": symbol,
            "date": when.date().isoformat(),
            "spot": spot,
            "short_expiration": plan["short_expiration"],
            "long_expiration": plan["long_expiration"],
            "short_dte": plan["short_dte"],
            "long_dte": plan["long_dte"],
            "short_chain": short_chain,
            "long_chain": long_chain,
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
    acting (a close priced off one good leg and one guess is not a measurement).
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
        symbol = (legs[0].get("position_symbol") or legs[0].get("symbol") or "").strip().upper()
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


def read_session(db_path, symbol: str, trade_date: str) -> dict | None:
    """Today's `stream_summary` row for one symbol — the keltner reversal gate's live inputs
    (`day_low`, `prev_day_close`). None when the cache has no row yet (early session, cold feed) —
    the gate refuses `keltner_no_day_low` rather than guessing."""
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    conn = _connect_ro(db_path)
    try:
        r = conn.execute(
            "SELECT * FROM stream_summary WHERE symbol = ? AND trade_date = ?",
            (symbol.strip().upper(), trade_date),
        ).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


# --------------------------------------------------------------------------- calibration
def _open_interest(conn, streamer_syms: list[str]) -> dict:
    """Open interest per streamer symbol. Deliberately NOT age-bounded: OI is published once a day
    against the prior session, so a freshness gate would reject every row every morning and report
    an empty ladder as an illiquid one."""
    out: dict[str, int] = {}
    for i in range(0, len(streamer_syms), 900):
        chunk = streamer_syms[i : i + 900]
        placeholders = ", ".join("?" * len(chunk))
        for r in conn.execute(
            f"SELECT symbol, open_interest FROM stream_oi WHERE symbol IN ({placeholders})", chunk
        ):
            if r["open_interest"] is not None:
                out[r["symbol"]] = int(r["open_interest"])
    return out


def _raw_quote(row, now_ts: float, max_age: float) -> dict:
    """`_usable_quote`'s reading WITHOUT its rejection: the same bid/ask/mid/age, plus `usable` and
    the reason it would have been refused. The selectors must refuse a torn quote; a calibration
    step that dropped those rows would be measuring the ladder it wishes it had."""
    bid, ask, updated = row["bid"], row["ask"], row["updated_at"]
    if bid is None or ask is None or updated is None:
        return {
            "bid": None,
            "ask": None,
            "mid": None,
            "age_seconds": None,
            "usable": False,
            "refusal": "no_quote",
        }
    bid, ask = float(bid), float(ask)
    age = round(now_ts - float(updated), 1)
    mid = float(row["mid"]) if row["mid"] is not None else (bid + ask) / 2.0
    refusal = None
    if age > max_age:
        refusal = "stale"
    elif ask <= 0 or bid < 0:
        refusal = "non_positive"
    elif bid > ask:
        refusal = "crossed"
    return {
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "age_seconds": age,
        "usable": refusal is None,
        "refusal": refusal,
    }


def ladder_snapshot(
    db_path,
    symbol: str,
    expiration: str,
    *,
    root: str,
    when: datetime | None = None,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
    deep_window_pct: float = DEFAULT_DEEP_WINDOW_PCT,
) -> dict:
    """Every ITM call strike for one (symbol, expiration) with exactly what a short-leg selector
    would see: strike, moneyness, delta, mid, intrinsic, extrinsic, absolute and %-of-mid spread,
    open interest, and the age of each input.

    This is the CALIBRATION read, not a decision path — it exists so `short_delta_target` is set
    against an observed ladder rather than declared from theory, and so the deep-ITM liquidity risk
    stays monitorable afterwards. Two deliberate departures from the entry snapshot:

    1. Rows are returned WITH their staleness rather than filtered by it (`usable` plus the refusal
       per row). A measurement step that hides stale rows cannot measure staleness.
    2. Greeks use the same loose age bound the selectors get, so the delta column here is the delta
       the selector would actually have read — not a fresher one this function could have fetched.

    Refusal-shaped like every other builder: `{"ok": False, "reason": ...}`.
    """
    symbol = symbol.strip().upper()
    db_path = Path(db_path)
    when = when or now_et()
    if not db_path.exists():
        return _fail(symbol, "stream_cache_missing")

    conn = _connect_ro(db_path)
    try:
        tr = conn.execute("SELECT last, updated_at FROM stream_trades WHERE symbol = ?", (symbol,)).fetchone()
        spot = float(tr["last"]) if tr and tr["last"] is not None else None
        if not spot:
            return _fail(symbol, "no_spot_price")
        now_ts = time.time()
        spot_age = round(now_ts - float(tr["updated_at"]), 1) if tr["updated_at"] is not None else None

        chain = _chain_for_expiration(conn, symbol, expiration, root)
        if not chain:
            any_root = conn.execute(
                "SELECT COUNT(*) FROM stream_chain WHERE expiration = ? AND underlying_symbol = ?",
                (expiration, symbol),
            ).fetchone()[0]
            return _fail(symbol, "not_root_listed" if any_root else "no_chain", expiration=expiration)

        lo = spot * (1.0 - deep_window_pct)
        itm = sorted(
            (e for e in chain if e["option_type"] == "call" and lo <= e["strike_price"] < spot),
            key=lambda e: e["strike_price"],
        )
        if not itm:
            return _fail(symbol, "no_strikes_in_window", expiration=expiration)

        syms = [e["streamer_symbol"] for e in itm]
        raw: dict[str, dict] = {}
        for i in range(0, len(syms), 900):
            chunk = syms[i : i + 900]
            placeholders = ", ".join("?" * len(chunk))
            for r in conn.execute(
                f"SELECT symbol, bid, ask, mid, updated_at FROM stream_quotes "
                f"WHERE symbol IN ({placeholders})",
                chunk,
            ):
                raw[r["symbol"]] = _raw_quote(r, now_ts, max_quote_age_seconds)
        greeks = _greeks(conn, syms, now_ts=now_ts, max_age_seconds=max_quote_age_seconds * 6)
        oi = _open_interest(conn, syms)

        rows = []
        for e in itm:
            sym, strike = e["streamer_symbol"], e["strike_price"]
            q = raw.get(sym) or {
                "bid": None,
                "ask": None,
                "mid": None,
                "age_seconds": None,
                "usable": False,
                "refusal": "no_quote",
            }
            g = greeks.get(sym) or {}
            mid = q["mid"]
            intrinsic = spot - strike
            spread = (q["ask"] - q["bid"]) if q["bid"] is not None and q["ask"] is not None else None
            rows.append(
                {
                    "strike": strike,
                    "streamer_symbol": sym,
                    "moneyness_pct": round(intrinsic / spot, 5),
                    "delta": g.get("delta"),
                    "iv": g.get("iv"),
                    "mid": mid,
                    "bid": q["bid"],
                    "ask": q["ask"],
                    "intrinsic": round(intrinsic, 4),
                    "extrinsic": round(mid - intrinsic, 4) if mid is not None else None,
                    "spread_abs": round(spread, 4) if spread is not None else None,
                    "spread_pct_of_mid": round(spread / mid, 5) if spread is not None and mid else None,
                    "open_interest": oi.get(sym),
                    "quote_age_seconds": q["age_seconds"],
                    "usable": q["usable"],
                    "refusal": q["refusal"],
                }
            )

        spacings = sorted({round(b["strike"] - a["strike"], 4) for a, b in zip(rows, rows[1:], strict=False)})
        return {
            "ok": True,
            "symbol": symbol,
            "expiration": expiration,
            "date": when.date().isoformat(),
            "spot": spot,
            "spot_age_seconds": spot_age,
            "deep_window_pct": deep_window_pct,
            "strike_spacings": spacings,
            "rows": rows,
            "counts": {
                "itm_strikes": len(rows),
                "quoted": sum(1 for r in rows if r["mid"] is not None),
                "usable": sum(1 for r in rows if r["usable"]),
                "greeked": sum(1 for r in rows if r["delta"] is not None),
                "oi_present": sum(1 for r in rows if r["open_interest"] is not None),
                "below_intrinsic": sum(1 for r in rows if r["extrinsic"] is not None and r["extrinsic"] <= 0),
            },
        }
    finally:
        conn.close()
