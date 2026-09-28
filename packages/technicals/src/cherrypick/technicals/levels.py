"""The grid the vendor's support and resistance levels sit on -- solved -- and the rank.

**The grid (fitted 2026-09-27 on 35 names, 192 levels, every one explained).** Each level is either
the high or the low of the last `WINDOW` sessions, taken as printed, or a point on a grid:

- anchored at that window's low,
- stepping by the "nice" number nearest -- by ratio, not by difference -- to (high - low) / 100.

Two details were each found by a miss. The window is 250 sessions, not 252: ATI's, CSCO's and STLD's
lows sat on the 252nd session and the grid only fitted once they fell out. And the step is nearest
by ratio: ADI's range / 100 is 2.24, nearer 2.00 by difference but 2.50 by ratio, and its levels are
2.50 apart. On the vendor's own bars this places all 192 levels; on ours it needs dividends right
(`dividends.reconcile`), which is why it is scored on the names whose bars agree to the cent.

**Which grid points become levels is still open.** 83% of level dates are swing highs (the bar's
high beats the two either side), supports included, but the level's price is not simply that high
snapped to the grid. That is the next fit, on more captures.

**The 1-10 rank** is the decile of the name's ~6-month return percentile: Spearman 0.95 against the
vendor's rank over 34 names, within one step on 33, exact on about 40% -- the vendor ranks within its
own universe, which is not ours, and 34 names cannot pin it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

WINDOW = 250
NICE_STEPS = (0.01, 0.02, 0.05, 0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 2.5, 5.0, 10.0, 20.0, 25.0, 50.0, 100.0)
RANK_SESSIONS = 125


@dataclass(frozen=True)
class Grid:
    low: float  # the window's low: the anchor, and a level itself
    high: float  # the window's high: a level itself
    step: float

    def snap(self, price: float) -> float:
        """The grid point nearest `price`."""
        return round(self.low + round((price - self.low) / self.step) * self.step, 4)  # a price, not a float

    def on_grid(self, price: float, tol: float = 0.011) -> bool:
        k = (price - self.low) / self.step
        return abs(k - round(k)) * self.step < tol

    def explains(self, price: float, tol: float = 0.006) -> bool:
        """Whether a level at `price` is one this grid can produce: an extreme, or a grid point."""
        return abs(price - self.low) < tol or abs(price - self.high) < tol or self.on_grid(price)


def nice_step(raw: float) -> float:
    return min(NICE_STEPS, key=lambda s: abs(math.log(s / raw))) if raw > 0 else NICE_STEPS[0]


def grid(highs: list[float], lows: list[float], window: int = WINDOW) -> Grid | None:
    """The grid for the last `window` bars; None with fewer bars than that or a flat range."""
    if len(highs) < window or len(lows) < window:
        return None
    lo, hi = min(lows[-window:]), max(highs[-window:])
    if hi <= lo:
        return None
    return Grid(lo, hi, nice_step((hi - lo) / 100.0))


def rank(returns: dict[str, float]) -> dict[str, int]:
    """1-10 for each name: the decile of its return's percentile within `returns` (ties counted as
    at-or-below, so the best name is 10 and the worst is 1)."""
    values = sorted(returns.values())
    n = len(values)
    out = {}
    for sym, r in returns.items():
        at_or_below = sum(1 for v in values if v <= r)
        out[sym] = min(10, max(1, math.ceil(10 * at_or_below / n)))
    return out
