"""Our own support and resistance: confirmed swing points in our bars, nearest first.

The chart's default levels and the setups watchlist's support and resistance. Nothing here reads
vendor data. The vendor's levels stay on the chart as a comparison ("Vendor's view"), and
`docs/setups.md` records how often ours land near theirs. That figure is a check, never a target
these parameters are tuned to.

A swing high is a bar whose high is above every high of the SWING_K bars before it and not exceeded
by any of the SWING_K bars after it; a swing low the mirror. The trailing bars make it confirmed: a
swing is not one until SWING_K sessions have printed after it, so the newest bars cannot be one.
Resistance is a swing high above the last close and support a swing low below it, the textbook
reading (a broken level is not turned into its opposite). Within the last WINDOW sessions, the
PER_SIDE nearest on each side are kept, skipping any within MERGE of one already kept, so two highs a
few cents apart are one level. Each level is dated on its swing bar and drawn from there.
"""

from __future__ import annotations

from .levels import WINDOW

# Two trading weeks either side: a high or low the market respected for a month, the textbook
# "major swing". Checked against the vendor's view on 129 captures (2026-10-04): within 1% of one of
# theirs on 20% of their levels at 5, 26% at 10, 25% at 20 -- recorded, not tuned to.
SWING_K = 10
PER_SIDE = 2
MERGE = 0.01  # a level within 1% of a nearer one on the same side is the same level


def swing_highs(highs: list[float], k: int = SWING_K) -> list[int]:
    return [
        i
        for i in range(k, len(highs) - k)
        if highs[i] > max(highs[i - k : i]) and highs[i] >= max(highs[i + 1 : i + k + 1])
    ]


def swing_lows(lows: list[float], k: int = SWING_K) -> list[int]:
    return [
        i
        for i in range(k, len(lows) - k)
        if lows[i] < min(lows[i - k : i]) and lows[i] <= min(lows[i + 1 : i + k + 1])
    ]


def _nearest(candidates: list[tuple[float, int]], price: float, per_side: int) -> list[tuple[float, int]]:
    kept: list[tuple[float, int]] = []
    for value, i in sorted(candidates, key=lambda c: abs(c[0] - price)):
        if any(abs(value - v) <= MERGE * v for v, _ in kept):
            continue
        kept.append((value, i))
        if len(kept) == per_side:
            break
    return kept


def levels(
    dates: list[str],
    highs: list[float],
    lows: list[float],
    close: float,
    window: int = WINDOW,
    per_side: int = PER_SIDE,
) -> list[dict]:
    """Support and resistance as of the last bar: [{kind, value, date}], nearest first per side."""
    start = max(0, len(highs) - window)
    res = [(highs[i], i) for i in swing_highs(highs) if i >= start and highs[i] > close]
    sup = [(lows[i], i) for i in swing_lows(lows) if i >= start and lows[i] < close]
    return [
        {"kind": "resistance", "value": v, "date": dates[i]} for v, i in _nearest(res, close, per_side)
    ] + [{"kind": "support", "value": v, "date": dates[i]} for v, i in _nearest(sup, close, per_side)]
