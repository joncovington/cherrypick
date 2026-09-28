"""The 1M and 6M trend scores on the vendor's -4..+4 scale, and the five-step label shown for each.

**A baseline, declared as one.** The vendor's chart data carries both scores daily (704 short-term and
554 long-term days per name). On the two captures on file when this was written (ANET, MSFT;
2026-09-27) no simple construction reproduces the exact number: a sum of four moving-average
conditions matches it on 55-58% of days, a banded distance from one average similarly. What the
report prints is the five-step label, and there the baseline agrees on about 80% of days (short
term) and 79% (long term), within one step on 84% and 81%. Two names, both mostly bullish over the
span, are too few to fit further; `score-trends` re-scores against every capture the collector
saves, and the construction is refit once a week of captures (up to 40 names a night) is in.

The baseline score is the sum of four signs, each price against an average: short term SMA 34,
SMA 50, SMA 63 and EMA 26; long term SMA 100, SMA 150, SMA 200 and EMA 200 -- the single conditions
that track the vendor's scores most closely (price against SMA 50: 0.93 correlation, short term;
against SMA 150: 0.90, long term).
"""

from __future__ import annotations

from .indicators import ema, sma

SHORT_TERM = (("sma", 34), ("sma", 50), ("sma", 63), ("ema", 26))
LONG_TERM = (("sma", 100), ("sma", 150), ("sma", 200), ("ema", 200))
LABELS = {2: "Bullish", 1: "Mildly Bullish", 0: "Neutral", -1: "Mildly Bearish", -2: "Bearish"}


def label(score: float | None) -> str | None:
    """The five-step label of a -4..+4 score: +/-3 and +/-4 are the strong ends, +/-1 and +/-2 the
    mild ones. The vendor's page shows labels, never the number."""
    if score is None:
        return None
    return LABELS[2 if score >= 3 else 1 if score >= 1 else 0 if score == 0 else -1 if score >= -2 else -2]


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def scores(closes: list[float], spec=SHORT_TERM) -> list[int | None]:
    """The score for every close; None until every average in the spec is defined."""
    averages = [(sma if kind == "sma" else ema)(closes, n) for kind, n in spec]
    out: list[int | None] = []
    for i, c in enumerate(closes):
        vals = [a[i] for a in averages]
        out.append(None if any(v is None for v in vals) else sum(_sign(c - v) for v in vals))
    return out
