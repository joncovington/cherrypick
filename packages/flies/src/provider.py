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
import os
import sqlite3
import sys
import time
from datetime import date, datetime
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_CORE = os.path.join(_HERE, "_core")
if os.path.isdir(_CORE) and _CORE not in sys.path:
    sys.path.insert(0, _CORE)

from cherrypick.core.gex import compute_gex  # noqa: E402

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - only where zoneinfo has no tz database
    import pytz
    _ET = pytz.timezone("America/New_York")

DEFAULT_MAX_QUOTE_AGE_SECONDS = 120
DEFAULT_STRIKE_WINDOW_PCT = 0.015


def now_et() -> datetime:
    return datetime.now(_ET)


def minute_of_day(when: datetime) -> int:
    return when.hour * 60 + when.minute


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    """Strictly read-only. MEIC's streamer is writing this file live; a reader that could mutate it
    would be a reliability bug in someone else's module."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _fail(symbol: str, reason: str, **extra) -> dict:
    """A refusal, not an error. `extra` carries any telemetry the caller can use to explain the
    refusal afterwards — e.g. how many quotes were rejected as stale on a `no_fresh_quotes`."""
    return {"ok": False, "symbol": symbol, "reason": reason, **extra}


def _usable_quote(row, now_ts: float, max_age: float) -> dict | None:
    """A quote we are willing to price a fill against, or None.

    Rejects stale, crossed, and non-positive-ask quotes. Returning None rather than a degraded quote is
    deliberate: a structure with a missing leg is skipped, which costs a sample. A structure priced off
    a bad leg produces a paper result that looks real and isn't, which costs the whole experiment.
    """
    bid, ask, updated = row["bid"], row["ask"], row["updated_at"]
    if bid is None or ask is None or updated is None:
        return None
    if now_ts - float(updated) > max_age:
        return None
    bid, ask = float(bid), float(ask)
    if ask <= 0 or bid < 0 or bid > ask:
        return None
    mid = row["mid"]
    return {"bid": bid, "ask": ask, "mid": float(mid) if mid is not None else (bid + ask) / 2.0,
            "age_seconds": round(now_ts - float(updated), 1)}


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
        entries.append({
            "strike_price": float(strike),
            "streamer_symbol": sym,
            "option_type": opt.get("option_type", ""),
            "shares_per_contract": opt.get("shares_per_contract") or 100,
        })
    return entries


def nearest_expiration(conn, symbol: str) -> str | None:
    """Soonest cached expiration for this underlying.

    Filtering on `underlying_symbol` matters: SPX and XSP share 0DTE dates, so an expiration-only
    match would silently blend two chains with a 10x strike difference between them.
    """
    row = conn.execute(
        "SELECT expiration FROM stream_chain WHERE underlying_symbol = ? "
        "GROUP BY expiration ORDER BY ABS(JULIANDAY(expiration) - JULIANDAY('now')) LIMIT 1",
        (symbol,),
    ).fetchone()
    return row["expiration"] if row else None


def build_snapshot(db_path, symbol: str, *, when: datetime | None = None,
                   max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
                   strike_window_pct: float = DEFAULT_STRIKE_WINDOW_PCT) -> dict:
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

        expiration = nearest_expiration(conn, symbol)
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
            target = calls if "C" in entry["option_type"].upper() else puts
            target[entry["strike_price"]] = quote

        if not puts and not calls:
            # Carry the rejected count out: this is the "the data was thin" refusal, and the number
            # of stale quotes behind it is exactly what tells a barren session from a broken feed.
            return _fail(symbol, "no_fresh_quotes", rejected=stale)

        # GEX is computed over the FULL chain, not the near-spot window: walls and the gamma flip are
        # properties of the whole surface, and truncating it would move them.
        greeks, oi = _greeks_and_oi(conn, [e["streamer_symbol"] for e in entries])
        gex = compute_gex(entries, greeks, oi, spot)

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
            # Kept so a session's results can be audited against how good its data actually was —
            # a day that skipped every entry on stale quotes should be visible as that, not as a
            # day the strategy found nothing.
            "quote_stats": {"fresh": len(puts) + len(calls), "rejected": stale,
                            "max_age_seconds": max_quote_age_seconds},
        }
    finally:
        conn.close()


def _greeks_and_oi(conn, chain_symbols: list[str]) -> tuple[dict, dict]:
    greeks: dict[str, dict] = {}
    oi: dict[str, int] = {}
    if not chain_symbols:
        return greeks, oi
    # Chunked: SQLite caps variables per statement (999 by default) and a full SPX chain exceeds it.
    for i in range(0, len(chain_symbols), 900):
        chunk = chain_symbols[i:i + 900]
        placeholders = ", ".join("?" * len(chunk))
        for r in conn.execute(
            f"SELECT symbol, gamma FROM stream_greeks WHERE symbol IN ({placeholders})", chunk
        ):
            greeks[r["symbol"]] = {"gamma": float(r["gamma"] or 0)}
        for r in conn.execute(
            f"SELECT symbol, open_interest FROM stream_oi WHERE symbol IN ({placeholders})", chunk
        ):
            oi[r["symbol"]] = int(r["open_interest"] or 0)
    return greeks, oi


def read_spot(db_path, symbol: str, *, max_age_seconds: float | None = None) -> float | None:
    """Latest spot for one symbol — used at settlement, where no chain is needed.

    `max_age_seconds` rejects a stale print. This matters more here than anywhere else in the module:
    settlement is the single most consequential price read, it decides every position's P&L at once,
    and it is irreversible once written. Every other read has the staleness gate applied in
    `build_snapshot`; this one did not, so a stalled streamer would have settled the whole session
    against a price hours old without any complaint. Observed 2026-07-20, when the upstream streamer
    stalled twice and was 99 minutes silent half an hour before the settle time.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    conn = _connect_ro(db_path)
    try:
        r = conn.execute(
            "SELECT last, updated_at FROM stream_trades WHERE symbol = ?", (symbol.strip().upper(),)
        ).fetchone()
        if not r or r["last"] is None:
            return None
        if max_age_seconds is not None:
            updated = r["updated_at"]
            if updated is None or (time.time() - float(updated)) > max_age_seconds:
                return None
        return float(r["last"])
    finally:
        conn.close()
