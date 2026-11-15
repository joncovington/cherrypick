"""cherrypick.core.chainmeasures -- the measures an entry gate reads off an option chain, one definition each.

Several modules and studies carry their own realised vol, IV rank or skew; this is the copy a new gate
reads, so two modules gating on "IV rank 20 to 60" mean the same 20. Existing copies move here one at a
time, each as a measurement change at a declared boundary (#1177, #1178): until then, where one differs
it is named below.

**Units.** Every volatility this returns is in vol points (an annualised 18% is 18.0). Ranks and
percentiles run 0 to 100. A chain row's own `iv`, as feeds state it, is a fraction (0.18).

**Where an IV or a delta comes from is the caller's explicit choice, never a fallback.** `iv_from`
is `"row"` (the row's own `iv`) or `"mid"` (implied from the row's mid with `core.bs.implied_vol`
against the chain's parity forward, `core.chaindelta`'s method); `delta_from` likewise. A row missing
the chosen source is skipped, never filled from the other, so one chain is never two sources mixed
(the rule `chaindelta` states, for the same reason).

**Carry.** Implied from mids at zero rate and dividend against the parity forward, which already
carries both: the paper loops' treatment (#786), so a gate in a backtest reads what its paper arm reads.

The measures (and what each reads, `NEEDS`):

- `realized_vol(closes)`: the sample standard deviation of the closes' log returns times the square
  root of 252 -- overview's `realized_vol` and contango's `realised_vol` (pmcc's item 36 is the same
  quantity as a fraction).
- `iv_rank(value, history)`: where `value` sits between the lowest and highest of `history` and itself,
  0 to 100 (50 when they are all equal). `iv_percentile(value, history)`: the share of `history`
  strictly below `value`. MEIC's replay has both on a 0 to 1 scale; technicals' `iv_rank` reads a
  year's high and low already in the IV store.
- `atm_iv(rows, ...)`: the mean of the call and put IV at the listed strike nearest the parity
  forward that has both.
- `delta_iv(rows, right, delta, ...)`: the IV of the `right` row whose |delta| is nearest `delta`
  (ties to the lower strike). Nearest, not interpolated: the strike a trade would use.
- `skew_25d(rows, ...)`: the 25-delta put's IV minus the 25-delta call's -- super_bull's item 74
  definition.
- `iv_minus_rv(iv, closes)`: an IV in vol points less `realized_vol(closes)`.
- `term_spread(near, far)`: the far expiry's ATM IV minus the near one's (positive: upward sloping).
  `term_ratio(near, far)`: near over far (under 1: contango, the VIX/VIX3M reading).
- `net_greeks(legs, multiplier)`: delta, gamma, theta and vega of a set of legs, each leg's per-share
  greek times its signed quantity times the multiplier. A greek any leg lacks is None for the whole
  position, never a partial sum.

Pure functions over values the caller supplies: no clock, no cache, no network.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from cherrypick.core import bs, chaindelta

TRADING_DAYS = 252

#: What each measure reads, so a caller can tell before a run whether its data covers a range.
#: `closes`: daily closes; `iv_history`: a series of the same IV reading over the lookback;
#: `chain`: one expiry's quotes at the decision instant; `legs`: the chosen legs' greeks.
NEEDS: dict[str, tuple[str, ...]] = {
    "realized_vol": ("closes",),
    "iv_rank": ("iv_history",),
    "iv_percentile": ("iv_history",),
    "atm_iv": ("chain",),
    "delta_iv": ("chain",),
    "skew_25d": ("chain",),
    "iv_minus_rv": ("chain", "closes"),
    "term_spread": ("chain",),
    "term_ratio": ("chain",),
    "net_greeks": ("legs",),
}

_SOURCES = ("row", "mid")


def realized_vol(closes: Sequence[float]) -> float | None:
    """Annualised close-to-close volatility of `closes`, in vol points (11 closes: 10 returns). None
    under three closes or on a non-positive close."""
    if len(closes) < 3 or any(c is None or c <= 0 for c in closes):
        return None
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:], strict=False)]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS) * 100.0


def iv_rank(value: float, history: Sequence[float]) -> float | None:
    """`value`'s place between the low and high of `history` and itself, 0 to 100; 50 when flat. None
    without history."""
    if not history:
        return None
    window = [*history, value]
    lo, hi = min(window), max(window)
    return 50.0 if hi == lo else 100.0 * (value - lo) / (hi - lo)


def iv_percentile(value: float, history: Sequence[float]) -> float | None:
    """The share of `history` strictly below `value`, 0 to 100. None without history."""
    if not history:
        return None
    return 100.0 * sum(1 for h in history if h < value) / len(history)


def _check(name: str, source: str) -> None:
    if source not in _SOURCES:
        raise ValueError(f"{name} must be one of {list(_SOURCES)}, not {source!r}")


def _row_iv(row: Mapping[str, Any], *, iv_from: str, forward: float | None, years: float) -> float | None:
    """One row's IV in vol points from the chosen source only."""
    if iv_from == "row":
        iv = row.get("iv")
        try:
            iv = float(iv)
        except (TypeError, ValueError):
            return None
        return iv * 100.0 if math.isfinite(iv) and iv > 0 else None
    k, right, mid = chaindelta._strike(row), chaindelta._right(row), chaindelta._mid(row)
    if forward is None or k is None or right is None or mid is None:
        return None
    iv = bs.implied_vol(forward, k, years, mid, right, 0.0)
    return None if iv is None or iv <= 0.0001 else iv * 100.0


def _row_delta(
    row: Mapping[str, Any], *, delta_from: str, forward: float | None, years: float
) -> float | None:
    if delta_from == "row":
        d = row.get("delta")
        try:
            d = float(d)
        except (TypeError, ValueError):
            return None
        return d if math.isfinite(d) else None
    iv = _row_iv(row, iv_from="mid", forward=forward, years=years)
    k, right = chaindelta._strike(row), chaindelta._right(row)
    if iv is None or k is None or right is None or forward is None:
        return None
    return bs.delta(forward, k, years, iv / 100.0, right, 0.0)


def atm_iv(rows: Iterable[Mapping[str, Any]], *, years: float, iv_from: str) -> float | None:
    """The mean of the call and put IV at the strike nearest the parity forward that has both."""
    _check("iv_from", iv_from)
    rows = list(rows)
    forward = chaindelta.forward_from_parity(rows)
    if forward is None:
        return None
    by: dict[float, dict[str, float]] = {}
    for r in rows:
        k, right = chaindelta._strike(r), chaindelta._right(r)
        iv = _row_iv(r, iv_from=iv_from, forward=forward, years=years)
        if k is None or right is None or iv is None:
            continue
        by.setdefault(k, {})[right] = iv
    pairs = [k for k, sides in by.items() if "C" in sides and "P" in sides]
    if not pairs:
        return None
    k = min(pairs, key=lambda s: (abs(s - forward), s))
    return (by[k]["C"] + by[k]["P"]) / 2.0


def delta_iv(
    rows: Iterable[Mapping[str, Any]],
    right: str,
    delta: float,
    *,
    years: float,
    iv_from: str,
    delta_from: str,
) -> float | None:
    """The IV of the `right` row whose |delta| is nearest `delta` (ties to the lower strike), among rows
    with both an IV and a delta from the chosen sources."""
    _check("iv_from", iv_from)
    _check("delta_from", delta_from)
    if right not in ("C", "P"):
        raise ValueError(f"right must be 'C' or 'P', not {right!r}")
    rows = list(rows)
    forward = chaindelta.forward_from_parity(rows)
    best = None
    for r in rows:
        k = chaindelta._strike(r)
        if chaindelta._right(r) != right or k is None:
            continue
        iv = _row_iv(r, iv_from=iv_from, forward=forward, years=years)
        d = _row_delta(r, delta_from=delta_from, forward=forward, years=years)
        if iv is None or d is None:
            continue
        key = (abs(abs(d) - delta), k)
        if best is None or key < best[0]:
            best = (key, iv)
    return None if best is None else best[1]


def skew_25d(
    rows: Iterable[Mapping[str, Any]], *, years: float, iv_from: str, delta_from: str
) -> float | None:
    """The 25-delta put's IV minus the 25-delta call's, in vol points."""
    rows = list(rows)
    put = delta_iv(rows, "P", 0.25, years=years, iv_from=iv_from, delta_from=delta_from)
    call = delta_iv(rows, "C", 0.25, years=years, iv_from=iv_from, delta_from=delta_from)
    return None if put is None or call is None else put - call


def iv_minus_rv(iv: float | None, closes: Sequence[float]) -> float | None:
    """An IV in vol points less the realised vol of `closes`."""
    rv = realized_vol(closes)
    return None if iv is None or rv is None else iv - rv


def term_spread(near: float | None, far: float | None) -> float | None:
    """The far expiry's ATM IV minus the near one's, in vol points."""
    return None if near is None or far is None else far - near


def term_ratio(near: float | None, far: float | None) -> float | None:
    """The near expiry's ATM IV over the far one's (under 1: contango)."""
    return None if near is None or far is None or far <= 0 else near / far


GREEKS = ("delta", "gamma", "theta", "vega")


def net_greeks(legs: Iterable[Mapping[str, Any]], *, multiplier: float) -> dict[str, float | None]:
    """Each greek over the legs: per-share greek x signed `quantity` x `multiplier`. Delta reads as
    shares, theta in dollars a day and vega in dollars a vol point when the legs state them per share,
    per day and per vol point, as the feeds do. A greek any leg lacks is None."""
    legs = list(legs)
    out: dict[str, float | None] = {}
    for g in GREEKS:
        total = 0.0
        for leg in legs:
            v = leg.get(g)
            try:
                v, q = float(v), float(leg["quantity"])
            except (TypeError, ValueError, KeyError):
                total = None
                break
            if not math.isfinite(v):
                total = None
                break
            total += v * q * multiplier
        out[g] = total if legs else None
    return out
