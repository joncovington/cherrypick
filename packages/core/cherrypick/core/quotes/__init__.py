"""One-off quote and greek snapshots from a streamer instance, for pricing an idea by hand.

`snapshot(symbols)` asks a producer instance (the second login's `margin` by default) for a set of
legs and underlyings through a request file of its own, waits until the instance's cache holds a
quote and greeks for every option, reads them, and removes the request file again. The producer is
the only writer of its cache; this module writes nothing but that request file and only reads the
cache, read-only.

Symbols may be OCC (`SPY   270219P00580000`), streamer (`.SPY270219P580`) or an underlying
(`SPY`, `XSP`). `grid(root, expirations, right, strikes)` builds the streamer symbols of a strike
ladder.

Each option row carries `usable` and `reason`. A leg is unusable when its bid is zero, the quote is
crossed, or its width exceeds max(15% of mid, $0.06): the filter that kept a 65%-wide EEM quote and
a split-adjusted SOXX strike out of a priced idea. Nothing is dropped; the caller sees every row.

Off-hours the producer replays its last quotes, so `age_seconds` and `updated_at` say when the row
was written, not when the market printed it. A weekend snapshot is the last close; the caller
says so.

The main instance is accepted but is not the default: a request there adds to the shared
subscription budget every module draws on.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from cherrypick.core import streamcache, streamrequests
from cherrypick.core.db import connect_ro

DEFAULT_INSTANCE = "margin"
#: Widest usable quote: max(MAX_SPREAD_FRAC of mid, MAX_SPREAD_ABS dollars).
MAX_SPREAD_FRAC = 0.15
MAX_SPREAD_ABS = 0.06
DEFAULT_WAIT_SECONDS = 180.0
POLL_SECONDS = 5.0


def to_streamer(symbol: str) -> str:
    """The cache key for `symbol`: an OCC option becomes its streamer symbol, a streamer option is
    kept, and anything else is an underlying, upper-cased."""
    s = str(symbol).strip()
    if not s:
        raise ValueError("empty symbol")
    if s.startswith("."):
        return s
    converted = streamcache.occ_to_streamer_symbol(s) if len(s) > 6 else ""
    return converted or s.upper()


def grid(root: str, expirations: Iterable[str], right: str, strikes: Iterable[float]) -> list[str]:
    """Streamer symbols for every `(expiration, strike)`: `expirations` as YYMMDD, `right` C or P.
    A whole strike prints without decimals (`.SPY270219P580`), a fractional one as the streamer
    writes it (`.SPY270219P580.5`)."""
    r = str(right).strip().upper()[:1]
    if r not in ("C", "P"):
        raise ValueError(f"right must be C or P, not {right!r}")
    out = []
    for exp in expirations:
        e = str(exp).strip()
        if len(e) != 6 or not e.isdigit():
            raise ValueError(f"expiration must be YYMMDD, not {exp!r}")
        for k in strikes:
            kf = float(k)
            ks = str(int(kf)) if kf.is_integer() else f"{kf:g}"
            out.append(f".{str(root).strip().upper()}{e}{r}{ks}")
    return out


def strike_range(spec: str) -> list[float]:
    """`"540:720:5"` -> 540, 545, ... 720 (inclusive)."""
    parts = [float(p) for p in str(spec).split(":")]
    if len(parts) != 3 or parts[2] <= 0 or parts[1] < parts[0]:
        raise ValueError(f"strikes must be LO:HI:STEP, not {spec!r}")
    lo, hi, step = parts
    n = int(round((hi - lo) / step))
    return [round(lo + i * step, 4) for i in range(n + 1)]


def judge(
    bid, ask, *, max_frac: float = MAX_SPREAD_FRAC, max_abs: float = MAX_SPREAD_ABS
) -> tuple[bool, str]:
    """`(usable, reason)` for one option quote. `reason` is "" when usable."""
    if bid is None or ask is None:
        return False, "no_quote"
    bid, ask = float(bid), float(ask)
    if ask <= 0:
        return False, "no_ask"
    if bid <= 0:
        return False, "zero_bid"
    if bid > ask:
        return False, "crossed"
    mid = (bid + ask) / 2.0
    if ask - bid > max(max_frac * mid, max_abs):
        return False, "too_wide"
    return True, ""


def _read(db_path: Path, syms: list[str], now: float) -> tuple[dict, dict, dict]:
    if not db_path.exists():
        return {}, {}, {}
    conn = connect_ro(db_path)
    try:
        quotes: dict[str, dict] = {}
        trades: dict[str, dict] = {}
        for i in range(0, len(syms), 900):
            chunk = syms[i : i + 900]
            marks = ", ".join("?" * len(chunk))
            for r in conn.execute(
                f"SELECT symbol, bid, ask, updated_at FROM stream_quotes WHERE symbol IN ({marks})", chunk
            ):
                quotes[r["symbol"]] = {"bid": r["bid"], "ask": r["ask"], "updated_at": r["updated_at"]}
            for r in conn.execute(
                f"SELECT symbol, last, updated_at FROM stream_trades WHERE symbol IN ({marks})", chunk
            ):
                trades[r["symbol"]] = {"last": r["last"], "updated_at": r["updated_at"]}
        # Any age: a snapshot reports the age rather than withholding the row.
        greeks = streamcache.greeks_for(conn, syms, now_ts=now, max_age_seconds=float("inf"))
        return quotes, greeks, trades
    finally:
        conn.close()


def _complete(syms: list[str], quotes: dict, greeks: dict, trades: dict) -> list[str]:
    """The symbols still missing what a snapshot needs: an option its quote and greeks, an
    underlying a quote or a trade (an index publishes no quote)."""
    missing = []
    for s in syms:
        if streamcache.is_option_symbol(s):
            if s not in quotes or s not in greeks:
                missing.append(s)
        elif s not in quotes and s not in trades:
            missing.append(s)
    return missing


def _rows(syms: list[str], quotes: dict, greeks: dict, trades: dict, now: float, **filt) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for s in syms:
        q, g, t = quotes.get(s) or {}, greeks.get(s) or {}, trades.get(s) or {}
        bid = streamcache.to_float(q.get("bid"))
        ask = streamcache.to_float(q.get("ask"))
        updated = streamcache.to_float(q.get("updated_at")) or streamcache.to_float(t.get("updated_at"))
        row: dict = {
            "bid": bid,
            "ask": ask,
            "mid": round((bid + ask) / 2.0, 4) if bid is not None and ask is not None else None,
            "width": round(ask - bid, 4) if bid is not None and ask is not None else None,
            "last": streamcache.to_float(t.get("last")),
            "updated_at": updated,
            "age_seconds": round(now - updated, 1) if updated is not None else None,
        }
        if streamcache.is_option_symbol(s):
            row.update({k: g.get(k) for k in ("delta", "gamma", "theta", "vega", "iv")})
            row["occ"] = streamcache.streamer_to_occ_symbol(s)
            row["usable"], row["reason"] = judge(bid, ask, **filt)
        out[s] = row
    return out


def snapshot(
    symbols: Iterable[str],
    *,
    instance: str = DEFAULT_INSTANCE,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    poll_seconds: float = POLL_SECONDS,
    keep_request: bool = False,
    max_spread_frac: float = MAX_SPREAD_FRAC,
    max_spread_abs: float = MAX_SPREAD_ABS,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Request `symbols` from `instance`, wait up to `wait_seconds` for every one, and return
    `{instance, requested, missing, rows, request_file, request_removed}`.

    A symbol the cache never filled is listed in `missing` and has no row; the call never raises for
    one. The request file is named for this process, so two sessions never remove each other's, and
    is removed on the way out (raise or not) unless `keep_request`."""
    syms = list(dict.fromkeys(to_streamer(s) for s in symbols))
    if not syms:
        raise ValueError("no symbols")
    module = f"adhoc-quotes-{os.getpid()}"
    db_path = streamcache.instance_cache_path(instance)
    path = streamrequests.write_request(module, [], legs=syms, instance_name=instance)
    removed = False
    try:
        deadline = clock() + max(0.0, float(wait_seconds))
        while True:
            now = clock()
            quotes, greeks, trades = _read(db_path, syms, now)
            missing = _complete(syms, quotes, greeks, trades)
            if not missing or now >= deadline:
                break
            sleep(poll_seconds)
    finally:
        if not keep_request:
            try:
                path.unlink()
                removed = True
            except FileNotFoundError:
                removed = True
            except OSError:
                removed = False
    rows = _rows(
        [s for s in syms if s not in missing],
        quotes,
        greeks,
        trades,
        now,
        max_frac=max_spread_frac,
        max_abs=max_spread_abs,
    )
    return {
        "instance": instance,
        "cache": str(db_path),
        "requested": len(syms),
        "missing": missing,
        "rows": rows,
        "request_file": str(path),
        "request_removed": removed,
    }
