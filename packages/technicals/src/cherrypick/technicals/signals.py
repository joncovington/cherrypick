"""The vendor's six scan rules, as declared rules over our trend scores, CCI and RSI.

The vendor's scan list (`trade-ideas.json`, saved with every chart capture) flags ~140 names a day,
each with its rule. Refitted 2026-09-29 once `trend.py` reproduced the vendor's exact trend scores,
on the two lists saved (2026-09-25 and 09-28) against the ~370 other stocks the store holds:

| Rule                    | Ours                                                          | Caught  | False |
|-------------------------|---------------------------------------------------------------|---------|-------|
| BearishCounterTrend     | both trends >= 3, RSI(14) >= 72                                | 16/16   | 6     |
| CciRallyInBearishTrend  | short trend <= -2, CCI(5) > 100 yesterday and lower today      | 6/6     | 4     |
| CciDipInBullishTrend    | short trend >= 3, CCI(5) < -100 yesterday and back above today | 19/19   | 4     |
| BullishCounterTrend     | short trend <= -3, long <= 0, RSI(14) <= 32                    | 99/106  | 68    |
| BullishTrendFollowing   | long trend >= -1, short trend 0..2, RSI(14) 40..50             | 63/78   | 18    |
| BearishTrendFollowing   | short trend -1..1, CCI(14) > 50                                | 42/53   | 15    |

245 of 278 (88%), against 234 (84%) for the rules as first fitted on the old trend baseline. Those
rules were written against a baseline whose -4 meant "below every average"; on the true scores -4
means a close under the lower Bollinger band, so they had to move -- on the true scores unchanged
they caught 55%. The thresholds are whole labels where the lists allow (Bullish is 3..4, Bearish
-3..-4). Both lists were used in fitting, so there is no out-of-sample day yet; `score-signals`
re-scores on every list the collector saves. Most false flags are BullishCounterTrend, the same
structural excess the stage rule shows: the vendor lists fewer names than prices alone would.
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
    if r.short >= 3 and r.long >= 3 and r.rsi14 >= 72:
        out.append("BearishCounterTrend")
    if r.short <= -2 and r.cci5_prev > 100 and r.cci5 < r.cci5_prev:
        out.append("CciRallyInBearishTrend")
    if r.short >= 3 and r.cci5_prev < -100 and r.cci5 > -100:
        out.append("CciDipInBullishTrend")
    if r.short <= -3 and r.long <= 0 and r.rsi14 <= 32:
        out.append("BullishCounterTrend")
    if r.long >= -1 and 0 <= r.short <= 2 and 40 <= r.rsi14 <= 50:
        out.append("BullishTrendFollowing")
    if -1 <= r.short <= 1 and r.cci14 > 50:
        out.append("BearishTrendFollowing")
    return out
