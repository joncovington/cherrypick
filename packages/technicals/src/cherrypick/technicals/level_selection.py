"""Which grid points the vendor draws as levels: the measurements, not a rule.

The grid is solved (`levels.py`): every vendor level is a window extreme or a grid point. Which grid
points become levels is not, and this module does not claim to pick them. It measures, for one
capture, the properties a selection rule would have to reproduce, so `score-level-selection` can
re-run them as captures accumulate and a rule can be fitted against numbers rather than hunches.

Three questions, each measured against a chance baseline, because a rate without one is not a
finding. First measured 2026-09-29 on 108 names and 309 interior levels (newest capture per name,
bars agreeing to the cent):

- **Where the levels sit in the crossing profile.** Count, for each grid point, the sessions whose
  range crosses it. Levels sit where price spent LITTLE time -- mean percentile 0.35 against 0.5 for
  chance -- and are local minima of that profile at about twice the rate of a random grid point
  (0.61 vs 0.36 at +-1 step; 0.35 vs 0.14 at +-5). Volume at price says the same thing. Touch
  counts, the obvious support/resistance construction, point the WRONG way.
- **Given a level's date, its price.** 81% of dated bars are swing highs (the high beats two bars
  either side), supports included, and nearly every level is within half an ATR of its bar. Of the
  grid points within 0.75 ATR of the bar (about 14), the least-crossed one is the level 28% of the
  time, against 8% by chance; the bar's high rounded up to the grid, 26%. Neither is a rule.
- **Which swing highs are dated.** Nothing tried separates them much: the rise into the high,
  prominence and volume rank picked highs at about the 0.57-0.60 percentile, recency 0.43.

The price side and the date side are measured separately on purpose: a rule has to get both, and
knowing which half is missing is the point of the exercise.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from .levels import Grid

NEAR_ATR = 0.75  # how far either side of the dated bar a candidate grid point may sit
LOCAL_MIN_WIDTHS = (1, 3, 5)
ATR_PERIOD = 14


@dataclass
class Tally:
    """Counts summed across captures; the rates are taken once at the end."""

    interior: int = 0  # levels that are grid points, not window extremes
    crossing_pct: float = 0.0  # summed percentile of each level's crossing count among grid points
    local_min: dict[int, int] = field(default_factory=lambda: dict.fromkeys(LOCAL_MIN_WIDTHS, 0))
    grid_points: int = 0
    grid_local_min: dict[int, int] = field(default_factory=lambda: dict.fromkeys(LOCAL_MIN_WIDTHS, 0))
    dated: int = 0  # interior levels whose date is a bar in the window
    dated_swing_high: int = 0
    near_candidates: int = 0  # summed size of the candidate set around each dated bar
    chance: float = 0.0  # summed 1 / candidates: the exact-hit rate of a random pick
    price_rules: dict[str, dict[str, int]] = field(default_factory=dict)
    picked_swing_highs: int = 0
    date_pct: dict[str, float] = field(default_factory=dict)

    def add_rule(self, name: str, predicted: int, actual: int) -> None:
        r = self.price_rules.setdefault(name, {"exact": 0, "within1": 0})
        r["exact"] += predicted == actual
        r["within1"] += abs(predicted - actual) <= 1


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], n: int = ATR_PERIOD) -> float:
    """A plain mean of the last `n` true ranges -- a scale for "near", not an indicator to match."""
    trs = [
        max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        for i in range(1, len(highs))
    ][-n:]
    return sum(trs) / len(trs) if trs else 0.0


def swing_highs(highs: Sequence[float]) -> list[int]:
    """Bars whose high beats the two bars either side."""
    return [
        i
        for i in range(2, len(highs) - 2)
        if highs[i] > max(highs[i - 2], highs[i - 1], highs[i + 1], highs[i + 2])
    ]


def crossings(highs: Sequence[float], lows: Sequence[float], points: Sequence[float]) -> list[int]:
    """For each price, the number of bars whose range contains it."""
    return [sum(lo <= p <= hi for hi, lo in zip(highs, lows, strict=True)) for p in points]


def is_local_min(profile: Sequence[int], i: int, width: int) -> bool:
    return profile[i] == min(profile[max(0, i - width) : i + width + 1])


def percentile(values: Sequence[float], x: float) -> float:
    """Where `x` (one of `values`) ranks among them, 0..1, ties split evenly."""
    if len(values) < 2:
        return 0.5
    below = sum(v < x for v in values) + 0.5 * (sum(v == x for v in values) - 1)
    return below / (len(values) - 1)


def measure(
    dates: Sequence[str],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
    grid: Grid,
    vendor_levels: Sequence[tuple[float, str]],
    tally: Tally,
) -> None:
    """Add one capture's measurements to `tally`. The bars are the grid's window; `vendor_levels` are
    (price, date) pairs, support and resistance alike (the side is only which way the close sits)."""
    n_steps = int(round((grid.high - grid.low) / grid.step))
    points = [grid.low + k * grid.step for k in range(n_steps + 1)]
    profile = crossings(highs, lows, points)
    a = atr(highs, lows, closes)
    at = {d: i for i, d in enumerate(dates)}
    swings = swing_highs(highs)
    swing_set = set(swings)

    tally.grid_points += len(points)
    for w in LOCAL_MIN_WIDTHS:
        tally.grid_local_min[w] += sum(is_local_min(profile, k, w) for k in range(len(points)))

    picked = set()
    for price, date in vendor_levels:
        if abs(price - grid.low) < 0.006 or abs(price - grid.high) < 0.006 or not grid.on_grid(price):
            continue
        k = int(round((price - grid.low) / grid.step))
        if not 0 <= k < len(points):
            continue  # a grid point outside this window's range: a different window drew it
        tally.interior += 1
        tally.crossing_pct += percentile(profile, profile[k])
        for w in LOCAL_MIN_WIDTHS:
            tally.local_min[w] += is_local_min(profile, k, w)

        j = at.get(date)
        if j is None or a <= 0:
            continue
        tally.dated += 1
        tally.dated_swing_high += j in swing_set
        if j in swing_set:
            picked.add(j)
        near = [
            q for q in range(len(points)) if lows[j] - NEAR_ATR * a <= points[q] <= highs[j] + NEAR_ATR * a
        ]
        if not near:
            continue
        tally.near_candidates += len(near)
        tally.chance += 1 / len(near)
        frac = (highs[j] - grid.low) / grid.step
        tally.add_rule("bar high, nearest grid point", round(frac), k)
        tally.add_rule("bar high, next grid point up", math.ceil(frac - 1e-9), k)
        tally.add_rule(
            "least-crossed grid point near the bar",
            min(near, key=lambda q: (profile[q], abs(points[q] - highs[j]))),
            k,
        )

    if picked and len(swings) >= 3:
        features = {
            "rise into the high (10 sessions)": lambda i: highs[i] - min(lows[max(0, i - 10) : i + 1]),
            "prominence": lambda i: _prominence(highs, i),
            "volume": lambda i: volumes[i],
            "recency": lambda i: i,
        }
        tally.picked_swing_highs += len(picked)
        for name, f in features.items():
            values = [f(i) for i in swings]
            tally.date_pct[name] = tally.date_pct.get(name, 0.0) + sum(
                percentile(values, f(i)) for i in picked
            )


def _prominence(highs: Sequence[float], i: int) -> int:
    """How many bars the high dominates on its weaker side."""
    left = 0
    while i - left - 1 >= 0 and highs[i - left - 1] < highs[i]:
        left += 1
    right = 0
    while i + right + 1 < len(highs) and highs[i + right + 1] < highs[i]:
        right += 1
    return min(left, right)


def summary(t: Tally) -> dict:
    def rate(num: float, den: int) -> float | None:
        return round(num / den, 3) if den else None

    return {
        "interior_levels": t.interior,
        "crossing_percentile": {"levels": rate(t.crossing_pct, t.interior), "chance": 0.5},
        "local_min_of_crossings": {
            f"+-{w}": {
                "levels": rate(t.local_min[w], t.interior),
                "grid_points": rate(t.grid_local_min[w], t.grid_points),
            }
            for w in LOCAL_MIN_WIDTHS
        },
        "dated_levels": t.dated,
        "dated_bar_is_swing_high": rate(t.dated_swing_high, t.dated),
        "price_given_date": {
            "candidates_per_level": rate(t.near_candidates, t.dated),
            "chance_exact": rate(t.chance, t.dated),
            **{
                name: {"exact": rate(r["exact"], t.dated), "within1": rate(r["within1"], t.dated)}
                for name, r in t.price_rules.items()
            },
        },
        "which_swing_highs_are_dated": {
            "picked": t.picked_swing_highs,
            "chance": 0.5,
            **{name: rate(v, t.picked_swing_highs) for name, v in t.date_pct.items()},
        },
    }
