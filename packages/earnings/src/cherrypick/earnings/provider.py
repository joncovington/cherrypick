"""Price open positions from the shared stream cache, and refuse rather than guess.

The monitoring loop marks every open position once a minute. Doing that through the broker means one
subprocess and one fresh DXLink session per position per tick (see `scanner.call_tt`) — seconds of
latency each, for data the suite's single producer is already streaming into
`~/.cherrypick/data/marketdata/stream_cache.db`. So this module reads that cache, and the broker is
kept for the two things only it can do: pricing something nobody subscribed, and confirming a close.

**Quotes come back keyed by OCC symbol**, not by the DXLink streamer symbol they were stored under.
That is deliberate: `scanner.compute_generic_exit_debit` and the strategies' `evaluate_position`
functions all index quotes by the leg's own `symbol`, so a snapshot from here drops into the existing
pricing path unchanged and the REST fallback returns the identical shape. The translation is done from
each leg's stored `streamer_symbol`, captured from the broker's own chain at entry — never derived
here, because a symbol this module invented would silently read as "no quote".

**Refusals are ordinary.** A stale quote, a crossed quote, a leg nobody is streaming: each returns
`{"ok": False, "reason": ...}` with the counts behind it, which the caller records as an unusable mark
and steps past. The alternative — pricing a position off a leg quoted an hour ago — produces a
management decision that looks real and isn't, which is worse than not deciding.

Read-only, always: the producer is writing this file live, and a reader that could mutate it would be
a reliability bug in someone else's module.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from cherrypick.core import home as _home

# Read-only opens go through cherrypick.core.db.connect_ro: it percent-escapes the path, so a
# directory containing '?', '#' or '%' cannot silently change the URI's meaning. The local
# copies interpolated the path raw, where a '#' truncated the URI and opened a DIFFERENT,
# empty database — which a provider reports as "nothing cached" rather than as an error.
from cherrypick.core.db import connect_ro as _connect_ro

# Shared with every other provider — see cherrypick.core.streamcache.
from cherrypick.core.streamcache import usable_quote as _usable_quote

# A minute-cadence loop reading a continuously-streamed cache: anything older than this is a feed
# that stopped, not a market that went quiet. Deliberately longer than flies' 120s — an earnings
# structure's wings are far out of the money and genuinely trade less often than an index ATM strike.
DEFAULT_MAX_QUOTE_AGE_SECONDS = 300

# Spot tolerates more age than a leg quote: it gates the pin-risk guard and breach checks, which move
# on the scale of a strike, not a tick.
DEFAULT_MAX_SPOT_AGE_SECONDS = 600


def cache_path() -> Path:
    """The suite's canonical shared cache. One producer writes it; every module reads it."""
    return _home.data_dir() / "marketdata" / "stream_cache.db"




def _fail(reason: str, **extra) -> dict:
    """A refusal, not an error. `extra` carries whatever explains it afterwards — how many legs were
    stale, which ones were missing — so a barren stretch reads as "the feed was thin" rather than
    being mistaken for "nothing happened"."""
    return {"ok": False, "reason": reason, **extra}




def spread_pct(quote: dict) -> float | None:
    """Bid-ask width as a fraction of mid — the sanity gate on whether a mark is worth acting on.

    Opening-auction spreads are the reason this exists: for the first ten minutes an earnings name's
    options can quote a width wider than the edge being managed, and a target computed from that mid
    is arithmetic, not a price.
    """
    mid = quote.get("mid")
    if not mid or mid <= 0:
        return None
    return (quote["ask"] - quote["bid"]) / mid


def leg_quotes(conn, streamer_symbols, *, now_ts: float, max_age: float) -> tuple[dict, list, list]:
    """`({streamer_symbol: quote}, fresh, rejected)` for the symbols the cache can serve."""
    wanted = [s for s in streamer_symbols if s]
    if not wanted:
        return {}, [], []
    placeholders = ",".join("?" for _ in wanted)
    rows = conn.execute(
        f"SELECT symbol, bid, ask, mid, updated_at FROM stream_quotes WHERE symbol IN ({placeholders})",
        wanted,
    ).fetchall()
    found = {r["symbol"]: r for r in rows}

    quotes, fresh, rejected = {}, [], []
    for symbol in wanted:
        row = found.get(symbol)
        quote = _usable_quote(row, now_ts, max_age) if row is not None else None
        if quote is None:
            rejected.append(symbol)
        else:
            quotes[symbol] = quote
            fresh.append(symbol)
    return quotes, fresh, rejected


def leg_greeks(conn, streamer_symbols, *, now_ts: float, max_age: float) -> dict:
    """`{streamer_symbol: {"iv", "delta"}}` for whatever the cache has fresh.

    Optional by design, exactly as the REST path treats them: a missing IV degrades only the
    crush analysis, and a missing delta means a leg-delta stop skips its check this tick. Neither
    is a reason to refuse to price a position whose quotes are perfectly good.
    """
    wanted = [s for s in streamer_symbols if s]
    if not wanted:
        return {}
    placeholders = ",".join("?" for _ in wanted)
    rows = conn.execute(
        f"SELECT symbol, delta, iv, updated_at FROM stream_greeks WHERE symbol IN ({placeholders})",
        wanted,
    ).fetchall()
    out = {}
    for r in rows:
        updated = r["updated_at"]
        if updated is None or now_ts - float(updated) > max_age:
            continue
        out[r["symbol"]] = {"iv": r["iv"], "delta": r["delta"]}
    return out


def read_spot(conn, symbol: str, *, now_ts: float, max_age: float) -> float | None:
    """Latest underlying print, or None if nobody is streaming it or it has gone stale.

    None is a real answer here rather than a failure: the checks that need spot (pin risk, a breached
    short strike) skip when it is unknown, which is correct — refusing to price the whole position
    because its underlying is unsubscribed would throw away leg quotes that are perfectly good.
    """
    row = conn.execute(
        "SELECT last, updated_at FROM stream_trades WHERE symbol = ?", (symbol.strip().upper(),)
    ).fetchone()
    if not row or row["last"] is None or row["updated_at"] is None:
        return None
    if now_ts - float(row["updated_at"]) > max_age:
        return None
    return float(row["last"])


def legs_from_trade(trade: dict) -> list[dict]:
    """A trade's entry legs, parsed. Returns [] on anything unparseable rather than raising — a row
    with a broken legs_json is a position to skip and report, not a loop to crash."""
    try:
        legs = json.loads(trade.get("legs_json") or "[]")
    except (TypeError, ValueError):
        return []
    return legs if isinstance(legs, list) else []


def snapshot(
    trade: dict,
    *,
    db_path: Path | None = None,
    max_quote_age_seconds: float | None = None,
    max_spot_age_seconds: float | None = None,
    now_ts: float | None = None,
) -> dict:
    """Price one open position from the cache.

    On success: `{"ok": True, "quotes": {occ: {...}}, "spot": float|None, "source": "stream",
    "fresh": n, "stale": n, "max_spread_pct": float|None}`. Quotes are keyed by OCC symbol so the
    result is interchangeable with the REST path's.

    Every leg must be usable. A partial set is refused rather than returned, because
    `compute_generic_exit_debit` needs every leg to produce a number at all — half a structure priced
    is not half an answer, it is no answer.
    """
    now_ts = time.time() if now_ts is None else now_ts
    max_age = DEFAULT_MAX_QUOTE_AGE_SECONDS if max_quote_age_seconds is None else max_quote_age_seconds
    spot_age = DEFAULT_MAX_SPOT_AGE_SECONDS if max_spot_age_seconds is None else max_spot_age_seconds

    legs = legs_from_trade(trade)
    if not legs:
        return _fail("no_legs_recorded")

    # A leg whose streamer symbol was never captured cannot be looked up at all. Report that as its
    # own refusal: it is a gap in what was stored at entry, not a feed problem, and the two want
    # different fixes.
    untranslatable = [leg["symbol"] for leg in legs if not leg.get("streamer_symbol")]
    if untranslatable:
        return _fail("legs_missing_streamer_symbol", missing=untranslatable)

    path = Path(db_path) if db_path is not None else cache_path()
    if not path.exists():
        return _fail("no_stream_cache")

    by_streamer = {leg["streamer_symbol"]: leg["symbol"] for leg in legs}
    conn = _connect_ro(path)
    try:
        raw, fresh, rejected = leg_quotes(conn, list(by_streamer), now_ts=now_ts, max_age=max_age)
        if rejected:
            return _fail(
                "missing_leg_quotes",
                fresh=len(fresh),
                stale=len(rejected),
                missing=[by_streamer[s] for s in rejected],
            )
        greeks = leg_greeks(conn, list(by_streamer), now_ts=now_ts, max_age=max_age)
        spot = read_spot(conn, trade.get("symbol") or "", now_ts=now_ts, max_age=spot_age)
    finally:
        conn.close()

    quotes, widest = {}, None
    for streamer_symbol, occ in by_streamer.items():
        quote = dict(raw[streamer_symbol])
        greek = greeks.get(streamer_symbol) or {}
        quote["iv"] = greek.get("iv")
        quote["delta"] = greek.get("delta")
        quotes[occ] = quote
        width = spread_pct(quote)
        if width is not None:
            widest = width if widest is None else max(widest, width)

    return {
        "ok": True,
        "source": "stream",
        "quotes": quotes,
        "spot": spot,
        "fresh": len(fresh),
        "stale": len(rejected),
        "max_spread_pct": widest,
    }
