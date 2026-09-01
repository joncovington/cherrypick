"""Snapshot provider — turns MEIC's live stream cache into the snapshot the engine consumes.

Read-only (`?mode=ro`), always. MEIC's streamer owns that database; this module only ever reads it, so
running flies can never disturb the loop that is actually trading. This is the same piggyback path
`cherrypick-gex` uses, and it means the suite runs one streamer rather than three.

Nothing here makes a decision. The provider's whole job is to hand `engine.py` a snapshot that is
**fresh, complete, and honestly labelled** — and to refuse rather than guess when it isn't. Two failure
modes matter enough to be gates rather than warnings:

  Stale quotes.    A cached bid/ask from twenty minutes ago will happily price a fill that could never
                   have happened. On 0DTE, a few minutes is a different market. Legs older than
                   `max_quote_age_seconds` are dropped, and a structure missing a leg simply isn't
                   offered — the engine reports `missing_leg_quotes` and moves on.
  Crossed quotes.  bid > ask means a torn read or a broken feed, not an opportunity.

Precondition: MEIC's streamer must be running, and must be subscribed to the symbol. Open interest
(and therefore GEX) exists only because the streamer subscribes DXLink Summary for its ATM window.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path

# One ET for the suite — see cherrypick.core.clock.
from cherrypick.core.clock import ET as _ET

# Read-only opens go through cherrypick.core.db.connect_ro: it percent-escapes the path, so a
# directory containing '?', '#' or '%' cannot silently change the URI's meaning. The local
# copies interpolated the path raw, where a '#' truncated the URI and opened a DIFFERENT,
# empty database — which a provider reports as "nothing cached" rather than as an error.
from cherrypick.core.db import connect_ro as _connect_ro
from cherrypick.core.gex import compute_gex

# Shared with every other provider — see cherrypick.core.streamcache.
# read_spot is re-exported deliberately: the loops call provider.read_spot and their tests
# monkeypatch it there, so it is unused *inside* this module and ruff will drop it otherwise.
from cherrypick.core.streamcache import read_spot  # noqa: F401
from cherrypick.core.streamcache import usable_quote as _usable_quote

DEFAULT_MAX_QUOTE_AGE_SECONDS = 120
DEFAULT_STRIKE_WINDOW_PCT = 0.015

# GEX inputs get their own, much longer age limit than quotes. Greeks and open interest move on a
# different clock: OI is a once-a-day exchange snapshot delivered over DXLink Summary events, and
# gamma updates far less often than a quote does, so the 120s quote limit would reject a perfectly
# good surface. What this is here to catch is the case that was previously invisible -- a stale or
# dead feed producing a GEX number that looks exactly as confident as a live one. Measured against
# the cache on 2026-08-01, a healthy session sits well inside this and a dead feed is days outside.
DEFAULT_MAX_GEX_INPUT_AGE_SECONDS = 1800  # 30 minutes
# Below this many contributing strikes the surface is too sparse to locate a wall or a flip on.
# `compute_gex` already counts them (`strikes_with_data`); nothing consumed the number until now.
DEFAULT_MIN_GEX_STRIKES = 20


def now_et() -> datetime:
    return datetime.now(_ET)


def minute_of_day(when: datetime) -> int:
    return when.hour * 60 + when.minute


def _fail(symbol: str, reason: str, **extra) -> dict:
    """A refusal, not an error. `extra` carries any telemetry the caller can use to explain the
    refusal afterwards — e.g. how many quotes were rejected as stale on a `no_fresh_quotes`."""
    return {"ok": False, "symbol": symbol, "reason": reason, **extra}


def _chain_for_expiration(conn, symbol: str, expiration: str) -> list[dict]:
    entries = []
    for row in conn.execute(
        "SELECT data_json FROM stream_chain WHERE expiration = ? AND underlying_symbol = ?",
        (expiration, symbol),
    ):
        try:
            opt = json.loads(row["data_json"])
        except (ValueError, TypeError):
            continue
        sym, strike = opt.get("streamer_symbol"), opt.get("strike_price")
        if not sym or strike is None:
            continue
        entries.append(
            {
                "strike_price": float(strike),
                "streamer_symbol": sym,
                # The broker-side (OCC) symbol and instrument type ride along for the live scaffold's
                # order builders; paper never reads them, and old cache rows just leave them None.
                "occ_symbol": opt.get("symbol"),
                "instrument_type": opt.get("instrument_type"),
                "option_type": opt.get("option_type", ""),
                "shares_per_contract": opt.get("shares_per_contract") or 100,
            }
        )
    return entries


def nearest_expiration(conn, symbol: str, today: str) -> str | None:
    """Soonest cached expiration for this underlying that has not already passed.

    Filtering on `underlying_symbol` matters: SPX and XSP share 0DTE dates, so an expiration-only
    match would silently blend two chains with a 10x strike difference between them.

    `today` is the ET trading date and is required rather than derived from SQLite's clock, because
    getting that comparison wrong is what this function used to do. It ranked candidates by
    `ABS(JULIANDAY(expiration) - JULIANDAY('now'))` — absolute distance from the current UTC INSTANT,
    while an expiration is midnight. Past 12:00 UTC (08:00 ET) tomorrow is therefore always "nearer"
    than today, so from mid-morning on it returned tomorrow's date whenever a chain for it happened
    to be cached. `stream_chain` retains old rows, so on 2026-08-20 that was a six-day-old copy of
    the 08-21 chain: every quote in it was stale, the snapshot was refused as `no_fresh_quotes` on
    every tick, and the module recorded 212 refusals and not one iteration for the session.

    ISO dates sort lexicographically, so plain ordering is chronological and the comparison holds at
    any time of day.
    """
    row = conn.execute(
        "SELECT expiration FROM stream_chain WHERE underlying_symbol = ? AND expiration >= ? "
        "GROUP BY expiration ORDER BY expiration LIMIT 1",
        (symbol, today),
    ).fetchone()
    return row["expiration"] if row else None


def snapshot_kwargs(config: dict) -> dict:
    """The `build_snapshot` data-quality knobs read out of a loaded config's `defaults` block.

    One helper because four call sites (paper_loop's tick and its streamer probe, live_loop's tick
    and its watcher) all need the identical set, and the freshness limits are exactly the kind of
    thing that goes wrong by being applied in three places out of four."""
    defaults = config.get("defaults", {}) or {}
    return {
        "max_quote_age_seconds": defaults.get("max_quote_age_seconds", DEFAULT_MAX_QUOTE_AGE_SECONDS),
        "max_gex_input_age_seconds": defaults.get(
            "max_gex_input_age_seconds", DEFAULT_MAX_GEX_INPUT_AGE_SECONDS
        ),
        "min_gex_strikes": defaults.get("min_gex_strikes", DEFAULT_MIN_GEX_STRIKES),
    }


def build_snapshot(
    db_path,
    symbol: str,
    *,
    when: datetime | None = None,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
    strike_window_pct: float = DEFAULT_STRIKE_WINDOW_PCT,
    max_gex_input_age_seconds: float | None = DEFAULT_MAX_GEX_INPUT_AGE_SECONDS,
    min_gex_strikes: int = DEFAULT_MIN_GEX_STRIKES,
) -> dict:
    """Build one engine-ready snapshot for `symbol`, or a `{"ok": False, "reason": ...}` refusal.

    The refusal path is not an error path. A streamer that hasn't cached open interest yet, a symbol
    outside RTH, a chain with no fresh quotes — these are ordinary and frequent, and the loop logs them
    and carries on rather than treating them as failures.
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
            # MEIC hit exactly this with RUT: a subscribed symbol that never streamed a Trade event.
            return _fail(symbol, "no_spot_price")

        expiration = nearest_expiration(conn, symbol, when.date().isoformat())
        if not expiration:
            return _fail(symbol, "no_chain_cached")

        entries = _chain_for_expiration(conn, symbol, expiration)
        if not entries:
            return _fail(symbol, "no_chain_entries")

        # Only strikes near spot are tradeable here, and pulling the whole chain would mean thousands
        # of quote rows per iteration for structures the arms would never centre on.
        window = strike_window_pct * spot
        near = [e for e in entries if abs(e["strike_price"] - spot) <= window]
        if not near:
            return _fail(symbol, "no_strikes_near_spot")

        now_ts = time.time()
        by_symbol = {e["streamer_symbol"]: e for e in near}
        placeholders = ", ".join("?" * len(by_symbol))
        puts: dict[float, dict] = {}
        calls: dict[float, dict] = {}
        stale = 0
        for row in conn.execute(
            f"SELECT symbol, bid, ask, mid, updated_at FROM stream_quotes WHERE symbol IN ({placeholders})",
            list(by_symbol),
        ):
            quote = _usable_quote(row, now_ts, max_quote_age_seconds)
            if quote is None:
                stale += 1
                continue
            entry = by_symbol[row["symbol"]]
            quote["streamer_symbol"] = row["symbol"]
            quote["occ_symbol"] = entry.get("occ_symbol")
            quote["instrument_type"] = entry.get("instrument_type")
            target = calls if "C" in entry["option_type"].upper() else puts
            target[entry["strike_price"]] = quote

        if not puts and not calls:
            # Carry the rejected count out: this is the "the data was thin" refusal, and the number
            # of stale quotes behind it is exactly what tells a barren session from a broken feed.
            return _fail(symbol, "no_fresh_quotes", rejected=stale)

        # GEX is computed over the FULL chain, not the near-spot window: walls and the gamma flip are
        # properties of the whole surface, and truncating it would move them.
        greeks, oi, gex_input_stats = _greeks_and_oi(
            conn,
            [e["streamer_symbol"] for e in entries],
            now_ts=now_ts,
            max_age_seconds=max_gex_input_age_seconds,
        )
        gex = compute_gex(entries, greeks, oi, spot)
        gex_stats = {
            "strikes_with_data": gex.get("strikes_with_data", 0) if gex.get("ok") else 0,
            "min_strikes": min_gex_strikes,
            "max_input_age_seconds": max_gex_input_age_seconds,
            "oldest_input_age_seconds": gex_input_stats["oldest_age"],
            "greeks_fresh": gex_input_stats["greeks_fresh"],
            "greeks_stale": gex_input_stats["greeks_stale"],
            "oi_fresh": gex_input_stats["oi_fresh"],
            "oi_stale": gex_input_stats["oi_stale"],
        }
        # Coverage refusal. A GEX surface built from a handful of surviving strikes still returns
        # ok=True with a confident-looking wall and flip; downgrading it to a refusal here is what
        # lets `select_center` fall back to ATM (its `atm_gex_unavailable` path) instead of centring
        # a real butterfly on noise. The reason string is carried so the Decision Journal can tell a
        # thin session from a broken feed -- the same distinction `quote_stats.rejected` draws.
        if gex.get("ok") and gex_stats["strikes_with_data"] < min_gex_strikes:
            gex = {
                "ok": False,
                "error": (
                    f"insufficient GEX coverage — {gex_stats['strikes_with_data']} strikes with data "
                    f"(need {min_gex_strikes}); {gex_input_stats['greeks_stale']} greeks and "
                    f"{gex_input_stats['oi_stale']} OI rows rejected as stale"
                ),
                "insufficient_coverage": True,
            }
            gex_stats["refused"] = "insufficient_coverage"

        today = when.date()
        try:
            dte = (date.fromisoformat(expiration) - today).days
        except (ValueError, TypeError):
            dte = None

        return {
            "ok": True,
            "symbol": symbol,
            "date": today.isoformat(),
            "expiration": expiration,
            "dte": dte,
            "underlying_price": spot,
            "now_min": minute_of_day(when),
            "puts": puts,
            "calls": calls,
            "gex": gex,
            # The session's own open/high/low and prior close (2026-08-04). Descriptive input for
            # the `trend` regime tag only -- no gate reads it. See `_session_bounds`.
            "session": _session_bounds(conn, symbol, today.isoformat()),
            # Kept so a session's results can be audited against how good its data actually was —
            # a day that skipped every entry on stale quotes should be visible as that, not as a
            # day the strategy found nothing.
            "quote_stats": {
                "fresh": len(puts) + len(calls),
                "rejected": stale,
                "max_age_seconds": max_quote_age_seconds,
            },
            # The same audit trail for the GEX surface. Without this, a session that centred every
            # butterfly on stale gamma is indistinguishable from one that centred on live gamma.
            "gex_stats": gex_stats,
        }
    finally:
        conn.close()


def _session_bounds(conn, symbol: str, trade_date: str) -> dict:
    """The day's own open/high/low and the prior close, straight off the shared cache's
    `stream_summary` row for (symbol, trade_date). Empty dict when there is no row.

    **This is the reference point in time that `classify_regime` spent three weeks asserting no
    single snapshot carries.** It was wrong, and the cost was real: the trend dimension was never
    built, so 2026-08-04's two losing gex entries -- both legging into the side a 106-point up day
    was against -- carried no tag that could have flagged them. The reasoning had been that a trend
    read needs spot now versus spot N minutes ago, which is cross-tick state this module refuses to
    keep. It does not: the streamer already persists the session's open, so `spot - day_open` is a
    plain read of one row, no history and no state. The rule that decisions come from one
    pre-fetched snapshot is intact; only the belief about what a snapshot can contain has changed.

    Scoped to `trade_date` deliberately. The row is keyed by (symbol, trade_date) and a stale row
    from a previous session would silently supply a reference point from the wrong day -- which is
    worse than having none, because it reads as a confident trend rather than a missing one.

    `day_close` is not returned: SPX never publishes it through this path (NULL in every observed
    row), so a caller reading it would get None for the one symbol this module actually trades.
    """
    row = conn.execute(
        "SELECT day_open, day_high, day_low, prev_day_close FROM stream_summary "
        "WHERE symbol = ? AND trade_date = ?",
        (symbol, trade_date),
    ).fetchone()
    if row is None:
        return {}
    return {
        key: (float(row[key]) if row[key] is not None else None)
        for key in ("day_open", "day_high", "day_low", "prev_day_close")
    }


def _greeks_and_oi(
    conn,
    chain_symbols: list[str],
    *,
    now_ts: float | None = None,
    max_age_seconds: float | None = None,
) -> tuple[dict, dict, dict]:
    """Gamma and open interest for `chain_symbols`, dropping rows older than `max_age_seconds`.

    Returns `(greeks, oi, stats)`. Until 2026-08-01 this read both tables with no age filter at all,
    so an hours-stale gamma produced a GEX number indistinguishable from a live one -- on a path
    that picks the live butterfly's centre strike (`engine.select_center`, and `DEFAULT_ARM` is
    "gex"). The quote path in this same module has always rejected stale rows and reported the
    count (`_usable_quote`, `quote_stats`); this extends that discipline to the GEX inputs.

    Passing `max_age_seconds=None` disables the filter and restores the old behaviour, which the
    tests use to isolate the age logic from everything else.
    """
    greeks: dict[str, dict] = {}
    oi: dict[str, int] = {}
    stats = {"greeks_fresh": 0, "greeks_stale": 0, "oi_fresh": 0, "oi_stale": 0, "oldest_age": None}
    if not chain_symbols:
        return greeks, oi, stats

    def _fresh(updated) -> tuple[bool, float | None]:
        if max_age_seconds is None:
            return True, None
        if updated is None:
            return False, None
        age = (now_ts if now_ts is not None else time.time()) - float(updated)
        return age <= max_age_seconds, age

    def _note_age(age: float | None) -> None:
        if age is None:
            return
        if stats["oldest_age"] is None or age > stats["oldest_age"]:
            stats["oldest_age"] = round(age, 1)

    # Chunked: SQLite caps variables per statement (999 by default) and a full SPX chain exceeds it.
    for i in range(0, len(chain_symbols), 900):
        chunk = chain_symbols[i : i + 900]
        placeholders = ", ".join("?" * len(chunk))
        for r in conn.execute(
            f"SELECT symbol, gamma, updated_at FROM stream_greeks WHERE symbol IN ({placeholders})",
            chunk,
        ):
            ok, age = _fresh(r["updated_at"])
            if not ok:
                stats["greeks_stale"] += 1
                continue
            _note_age(age)
            stats["greeks_fresh"] += 1
            greeks[r["symbol"]] = {"gamma": float(r["gamma"] or 0)}
        for r in conn.execute(
            f"SELECT symbol, open_interest, updated_at FROM stream_oi WHERE symbol IN ({placeholders})",
            chunk,
        ):
            ok, age = _fresh(r["updated_at"])
            if not ok:
                stats["oi_stale"] += 1
                continue
            _note_age(age)
            stats["oi_fresh"] += 1
            oi[r["symbol"]] = int(r["open_interest"] or 0)
    return greeks, oi, stats
