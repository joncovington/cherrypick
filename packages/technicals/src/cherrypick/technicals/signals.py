"""The vendor's six scan rules, as declared rules over our trend scores, CCI and RSI.

The vendor's scan list (`trade-ideas.json`, saved with every chart capture) flags ~185 names a day,
each with its rule. Fitted on the 2026-09-25 list against the ~370 other stocks the store holds:

| Rule                    | Ours                                                        | Caught | False |
|-------------------------|-------------------------------------------------------------|--------|-------|
| BearishCounterTrend     | both trends +4, RSI(14) >= 72                                | 8/8    | 3     |
| CciRallyInBearishTrend  | short trend -4, CCI(5) > 100 yesterday and lower today       | 4/4    | 2     |
| CciDipInBullishTrend    | short trend +4, CCI(5) < -100 yesterday and back above today | 15/16  | 5     |
| BullishCounterTrend     | both trends -4, RSI(14) <= 32                                | 52/58  | 31    |
| BullishTrendFollowing   | long trend >= 2, short trend 0..2, CCI(14) < 0               | 40/55  | 10    |
| BearishTrendFollowing   | short trend -3..2, CCI(14) > 50                              | 33/39  | 7     |

The CCI rules run on a 5-period CCI -- the list says so -- where the plan had assumed 14. The trends
are `trend.py`'s baseline, itself right on ~3 days in 4, which caps how well any rule built on them
can do. One day is one day: these are declared, and `score-signals` re-scores them on every scan
list the collector saves, rather than being tuned further on this one.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import indicators, trend

RULES = (
    "BearishCounterTrend",
    "CciRallyInBearishTrend",
    "CciDipInBullishTrend",
    "BullishCounterTrend",
    "BullishTrendFollowing",
    "BearishTrendFollowing",
)


@dataclass(frozen=True)
class Readings:
    short: int | None
    long: int | None
    cci5: float | None
    cci5_prev: float | None
    cci14: float | None
    rsi14: float | None


def readings(highs: list[float], lows: list[float], closes: list[float]) -> Readings | None:
    """The last session's inputs; None when there is not enough history for every one of them."""
    if len(closes) < 2:
        return None
    short = trend.scores(closes, trend.SHORT_TERM)[-1]
    long_ = trend.scores(closes, trend.LONG_TERM)[-1]
    cci5 = indicators.cci(highs, lows, closes, 5)
    r = Readings(
        short,
        long_,
        cci5[-1],
        cci5[-2],
        indicators.cci(highs, lows, closes, 14)[-1],
        indicators.rsi(closes, 14)[-1],
    )
    return None if any(v is None for v in vars(r).values()) else r


def matches(r: Readings) -> list[str]:
    """Every rule the readings satisfy, in `RULES` order (a name can match more than one)."""
    out = []
    if r.short == 4 and r.long == 4 and r.rsi14 >= 72:
        out.append("BearishCounterTrend")
    if r.short == -4 and r.cci5_prev > 100 and r.cci5 < r.cci5_prev:
        out.append("CciRallyInBearishTrend")
    if r.short == 4 and r.cci5_prev < -100 and r.cci5 > -100:
        out.append("CciDipInBullishTrend")
    if r.short == -4 and r.long == -4 and r.rsi14 <= 32:
        out.append("BullishCounterTrend")
    if r.long >= 2 and 0 <= r.short <= 2 and r.cci14 < 0:
        out.append("BullishTrendFollowing")
    if -3 <= r.short <= 2 and r.cci14 > 50:
        out.append("BearishTrendFollowing")
    return out
