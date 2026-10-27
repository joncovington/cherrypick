"""The grid the vendor's support and resistance levels sit on -- solved -- and the rank.

**The grid (fitted 2026-09-27 on 35 names, 192 levels; window refitted 2026-10-27).** Each level is
either the high or the low of the window, taken as printed, or a point on a grid:

- anchored at that window's low,
- stepping by the "nice" number nearest -- by ratio, not by difference -- to (high - low) / 100.

**The window is a calendar year** back from the day the levels were computed, both ends inclusive
(`year_window`): bars dated on or after the same date a year earlier. For a capture that day is the
UTC date it was fetched (`capture_as_of`), which runs ahead of its last session after midnight UTC
and over weekends; for our own chart it is the session. A name listed less than a year gets all its
bars. The first fit said 250 sessions, not 252 -- ATI's, CSCO's and STLD's lows sat on the 252nd
session -- but those captures were fetched on a weekend, so a calendar year back from the fetch date
was 250 sessions back from the Friday. Over all 737 captures whose bars agree to the cent, the year
from the UTC fetch date places 3,738 of 3,761 levels against 3,490 for 250 sessions, and the misses
that once read as the vendor not recomputing AMD and MU nightly all fit it. `SESSION_WINDOW` keeps
the old rule for re-scoring. The step is nearest by ratio: ADI's range / 100 is 2.24, nearer 2.00 by
difference but 2.50 by ratio, and its levels are 2.50 apart. On ours the grid needs dividends right
(`dividends.reconcile`), which is why it is scored on the names whose bars agree to the cent.

**Which grid points become levels is still open.** 83% of level dates are swing highs (the bar's
high beats the two either side), supports included, and most levels sit within about one ATR of
that high, but the level's price is not simply that high snapped to the grid. `level_selection.py`
measures what a selection rule would have to reproduce.

**Gap levels are solved as prices** (2026-09-29): each of the vendor's gap levels is an edge of a true
two-bar gap (a bar's low above the prior high, or its high below the prior low) whose later bar is
inside the same calendar-year window, dated on the bar that edge belongs to. The gap's TOP edge is gap
support and its BOTTOM edge gap resistance, whichever way the gap went -- the low of the bar after a
gap up is support, the high of the bar after a gap down is resistance, and the bars before a gap give
the other edge. 168 of 168 on the stocks and index funds; 70 of 72 on SGOV, a T-bill fund. WHICH gaps
are drawn is open: an unfilled gap mostly needs to be close to an ATR wide, and a gap price has
crossed keeps at most its post-gap edge, but no rule yet says which.

**The 1-10 rank** (solved 2026-09-29, up to noise at the decile edges): the decile of a name's
percentile, across the WHOLE US-listed market, of `0.5 x its 21-session return + its 126-session
return`. On the vendor's own bars that score orders its ranks with 4 pairs out of ~1,500 inverted
(a single 125-session return, the old reading, leaves 20; trend scores add nothing). Ranked within
our ~500 candidates it matched on half and ran one decile low on the rest -- the vendor's universe
is weaker than a curated liquid list -- while ranked across Dolt's whole market (~8,500 names
trading $100k a day, split-affected names dropped) it matches exactly on 52 of 62 captures and
within one on all 62, misses falling both ways. The landing stores each session's nine market
cut-offs (`store.rank_cutoffs`), so a rank is one comparison and needs no market-wide bars.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

SESSION_WINDOW = 250  # the old window, in sessions: kept for re-scoring and as how much a chart draws
NICE_STEPS = (0.01, 0.02, 0.05, 0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 2.5, 5.0, 10.0, 20.0, 25.0, 50.0, 100.0)
RANK_SESSIONS = 125  # the old single-return rank, kept for a session with no market cut-offs
RANK_SHORT, RANK_LONG, RANK_SHORT_WEIGHT = 21, 126, 0.5
RANK_MIN_DOLLAR_VOLUME = 100_000.0  # the market the rank is taken across: names trading at least this


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


@dataclass(frozen=True)
class GapEdge:
    kind: str  # "gapSupport" (a gap's top edge) or "gapResistance" (its bottom edge)
    price: float
    date: str  # the bar the edge belongs to


def year_start(as_of: str) -> str:
    """The same calendar date a year before `as_of` (an ISO date); 29 February falls back to the 28th."""
    y, m, d = int(as_of[:4]), as_of[5:7], as_of[8:10]
    if (m, d) == ("02", "29"):
        d = "28"
    return f"{y - 1:04d}-{m}-{d}"


def year_window(dates: list[str], as_of: str | None = None, *, inclusive: bool = True) -> int | None:
    """How many of the trailing bars sit in the calendar year back from `as_of` (default: the last
    bar's date): bars dated on or after `year_start(as_of)`, or strictly after it when not
    `inclusive`. `dates` are ISO dates, oldest first, ending on or before `as_of`. A name listed
    less than a year gets all its bars, as the vendor draws it; None only with no bars at all."""
    if not dates:
        return None
    start = year_start(as_of or dates[-1][:10])
    inside = (lambda d: d >= start) if inclusive else (lambda d: d > start)
    n = 0
    for d in reversed(dates):
        if not inside(d[:10]):
            break
        n += 1
    return n


def capture_as_of(fetched_at: str | None, session: str) -> str:
    """The date a vendor capture's window runs back from: the UTC date it was fetched, which is later
    than its last session when it was fetched after midnight UTC or over a weekend; the session when
    the fetch time was not recorded."""
    if not fetched_at:
        return session
    try:
        t = datetime.fromisoformat(fetched_at)
    except ValueError:
        return session
    return (t.astimezone(UTC) if t.tzinfo else t).date().isoformat()


def gap_edges(dates: list[str], highs: list[float], lows: list[float], window: int | None) -> list[GapEdge]:
    """Both edges of every two-bar gap whose later bar is inside the last `window` bars (all of them
    when None, a history shorter than the window) -- the candidates the vendor's gap levels are drawn
    from, not a selection of them."""
    out = []
    window = len(dates) if window is None else window
    for i in range(max(1, len(dates) - window), len(dates)):
        if lows[i] > highs[i - 1]:  # gap up: the prior high is the bottom, this low the top
            out += [
                GapEdge("gapResistance", highs[i - 1], dates[i - 1]),
                GapEdge("gapSupport", lows[i], dates[i]),
            ]
        elif highs[i] < lows[i - 1]:  # gap down: this high is the bottom, the prior low the top
            out += [
                GapEdge("gapResistance", highs[i], dates[i]),
                GapEdge("gapSupport", lows[i - 1], dates[i - 1]),
            ]
    return out


def places_gap(edges: list[GapEdge], kind: str, price: float, date: str | None, tol: float = 0.006) -> bool:
    """Whether a vendor gap level is one of `edges`: same side, same bar, same price to the cent."""
    return any(e.kind == kind and e.date == date and abs(e.price - price) < tol for e in edges)


def nice_step(raw: float) -> float:
    return min(NICE_STEPS, key=lambda s: abs(math.log(s / raw))) if raw > 0 else NICE_STEPS[0]


def grid(highs: list[float], lows: list[float], window: int | None) -> Grid | None:
    """The grid for the last `window` bars (`year_window`, or `SESSION_WINDOW` to re-score the old
    rule); None with no window, fewer bars than it, or a flat range."""
    if not window or len(highs) < window or len(lows) < window:
        return None
    lo, hi = min(lows[-window:]), max(highs[-window:])
    if hi <= lo:
        return None
    return Grid(lo, hi, nice_step((hi - lo) / 100.0))


def rank_score(closes: list[float]) -> float | None:
    """0.5 x the 21-session return + the 126-session return, on the last close; None when too short."""
    if len(closes) <= RANK_LONG or closes[-1 - RANK_LONG] <= 0 or closes[-1 - RANK_SHORT] <= 0:
        return None
    return RANK_SHORT_WEIGHT * (closes[-1] / closes[-1 - RANK_SHORT] - 1) + (
        closes[-1] / closes[-1 - RANK_LONG] - 1
    )


def rank_cutoffs(scores: list[float]) -> list[float]:
    """The nine scores that separate the deciles of `scores`, chosen so `rank_from_cutoffs` gives
    exactly ceil(10 x (how many scores are <= x) / n) -- the percentile decile -- without the list."""
    vals = sorted(scores)
    n = len(vals)
    return [vals[(j * n) // 10] for j in range(1, 10)] if n >= 10 else []


def rank_from_cutoffs(score: float, cutoffs: list[float]) -> int:
    return min(10, max(1, 1 + sum(score >= c for c in cutoffs)))


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
