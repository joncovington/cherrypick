"""cherrypick.core.chaindelta -- an option chain's deltas computed from its own quotes, not the feed's.

Measured 2026-10-26 on four recorded SPX 0DTE sessions: the data feed's streamed delta lags its quotes
by about a minute or more. It matched a Black-Scholes delta best when that delta was computed from
the mids ~60 s EARLIER (median |gap| 0.004 against 0.011 at the same instant), and when a strike's mid
moved $0.05 or more between samples its streamed delta was unchanged 85% of the time. A strike chosen
by "nearest 0.16 delta" on the stream differed from the one the current mids give about 38% of the
time. And no history source can reproduce that staleness, so a replay on vendor data could not choose
paper's strikes.

This computes delta from the chain's own mids at the instant they were read, the same way on a live
chain, a recorded one or a vendor's, so a decision made on it can be replayed exactly:

- **forward** by put-call parity at the strike where |call mid - put mid| is smallest (F = K + C - P;
  zero rate, which is what a 0DTE carry rounds to);
- **implied vol** per strike from its mid (`core.bs.implied_vol`, bisection);
- **delta** from that vol (`core.bs.delta`), against the forward.

Pure. A strike with no usable mid, or whose mid no vol can price, gets None: never the feed's value
as a fallback, which would quietly mix the two sources in one chain.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from cherrypick.core import bs

ET = ZoneInfo("America/New_York")
YEAR_SECONDS = 365.0 * 86400.0
#: Below this the model's delta is a step function and means nothing; such a chain is refused.
MIN_YEARS = 60.0 / YEAR_SECONDS

__all__ = ["years_to_close", "forward_from_parity", "computed_deltas", "with_computed_delta"]


def years_to_close(now: datetime, *, close: time = time(16, 0)) -> float:
    """Calendar time from `now` to that ET day's `close` (a 0DTE SPX/XSP expiry), in years."""
    now_et = now.astimezone(ET)
    end = datetime.combine(now_et.date(), close, ET)
    return max(0.0, (end - now_et).total_seconds()) / YEAR_SECONDS


def _mid(row: Mapping[str, Any]) -> float | None:
    bid, ask = row.get("bid"), row.get("ask")
    try:
        bid, ask = float(bid), float(ask)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(bid) and math.isfinite(ask)) or ask <= 0 or bid < 0 or ask < bid:
        return None
    return (bid + ask) / 2.0


def _right(row: Mapping[str, Any]) -> str | None:
    t = str(row.get("option_type") or row.get("right") or "").upper()
    return "C" if t.startswith("C") else "P" if t.startswith("P") else None


def _strike(row: Mapping[str, Any]) -> float | None:
    k = row.get("strike_price", row.get("strike"))
    try:
        return float(k)
    except (TypeError, ValueError):
        return None


def forward_from_parity(rows: Iterable[Mapping[str, Any]]) -> float | None:
    """F = K + C - P at the strike whose call and put mids are closest; None without a pair."""
    calls, puts = {}, {}
    for r in rows:
        k, right, mid = _strike(r), _right(r), _mid(r)
        if k is None or right is None or mid is None:
            continue
        (calls if right == "C" else puts)[k] = mid
    best = None
    for k, c in calls.items():
        p = puts.get(k)
        if p is None:
            continue
        gap = abs(c - p)
        if best is None or gap < best[0]:
            best = (gap, k + c - p)
    return None if best is None else best[1]


def computed_deltas(rows: list[Mapping[str, Any]], *, years: float, rate: float = 0.0) -> list[float | None]:
    """Each row's delta from its own mid, in row order; all None when the chain has no parity pair or
    no time left (`MIN_YEARS`)."""
    if years < MIN_YEARS:
        return [None] * len(rows)
    forward = forward_from_parity(rows)
    if forward is None:
        return [None] * len(rows)
    out: list[float | None] = []
    for r in rows:
        k, right, mid = _strike(r), _right(r), _mid(r)
        if k is None or right is None or mid is None:
            out.append(None)
            continue
        iv = bs.implied_vol(forward, k, years, mid, right, rate)
        out.append(None if iv is None or iv <= 0.0001 else bs.delta(forward, k, years, iv, right, rate))
    return out


def with_computed_delta(rows: list[Mapping[str, Any]], *, years: float, rate: float = 0.0) -> list[dict]:
    """Copies of `rows` with `delta` replaced by the computed one, the feed's kept as `delta_stream`."""
    deltas = computed_deltas(rows, years=years, rate=rate)
    return [{**r, "delta_stream": r.get("delta"), "delta": d} for r, d in zip(rows, deltas, strict=True)]
