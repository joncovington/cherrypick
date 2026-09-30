"""The 1M and 6M trend scores on the vendor's -4..+4 scale, and the five-step label shown for each.

**Solved (2026-09-29), not a baseline.** On the vendor's own bars for 116 captured names, this
reproduces its exact daily score on 99.76% of 81,168 short-term days and 99.63% of 63,768 long-term
days. Each score is built from a short and a long simple average (20 and 50 sessions for the short
term, 50 and 200 for the long):

    2 x [close > short SMA] + 2 x [close > long SMA] + [short SMA > long SMA]
      + 2 x [close > long WMA] - 3

and -4 outright when the close is below both SMAs AND below the lower 20-session Bollinger band
(SMA 20 - 2 population standard deviations), for either term. So the range is -4..+4 with every
integer reachable -- which is why the old baseline, a sum of four +-1 signs that can only be even,
was capped at about half the days exact.

How each piece was found, so it is not re-litigated:

- **The lookbacks come from the capture itself.** On every name the vendor's short-term history
  starts on the 50th bar and its long-term on the 200th, so nothing longer than 50 / 200 can be in
  either. The old baseline's SMA 63 could never have been right.
- **The weights are integers.** A free least-squares fit over a library of price-vs-average and
  average-vs-average conditions put ~2, ~2, ~1 on the three core terms and found one more worth ~2.
  Tabulating the vendor's score by the core terms shows it directly: above both averages and below
  both are fixed, and only the mixed cases split, by exactly 2.
- **The fourth term is a WEIGHTED average.** A plain SMA near 36 (139 long term) matched the mixed
  split on ~91% (94%); the WMA of the long length matches on 99.8% (99.9%) -- an SMA 36 is
  centred where a WMA 50 is, which is why it came close.
- **-4 is a band break.** Within the below-both rows, "close under the lower 20-day Bollinger band"
  separates the -4s on 99.97% (short) and 99.92% (long). Sample standard deviation, or dropping
  the below-both condition, both do worse.

The misses left are one point either way on days the two averages are within a whisker of each
other. Nothing tried closes them (a one-day lag, >=, prices rounded to the cent); the likeliest
cause is that the vendor stores each day's score as computed then, before later dividend adjustment
moved the history.

On our own bars the rate depends on the bars agreeing with the vendor's; `score-trends` measures it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .indicators import sma, stdev, wma


@dataclass(frozen=True)
class TrendSpec:
    short: int
    long: int


SHORT_TERM = TrendSpec(20, 50)
LONG_TERM = TrendSpec(50, 200)
BAND_PERIOD = 20  # the Bollinger band that forces -4, the same for both terms
BAND_WIDTH = 2.0
LABELS = {2: "Bullish", 1: "Mildly Bullish", 0: "Neutral", -1: "Mildly Bearish", -2: "Bearish"}


def label(score: float | None) -> str | None:
    """The five-step label of a -4..+4 score: +/-3 and +/-4 are the strong ends, +/-1 and +/-2 the
    mild ones. The vendor's page shows labels, never the number."""
    if score is None:
        return None
    return LABELS[2 if score >= 3 else 1 if score >= 1 else 0 if score == 0 else -1 if score >= -2 else -2]


def scores(closes: list[float], spec: TrendSpec = SHORT_TERM) -> list[int | None]:
    """The score for every close; None until the long average is defined (the vendor's own start)."""
    s, lng, w = sma(closes, spec.short), sma(closes, spec.long), wma(closes, spec.long)
    mid, sd = sma(closes, BAND_PERIOD), stdev(closes, BAND_PERIOD)
    out: list[int | None] = []
    for i, c in enumerate(closes):
        if s[i] is None or lng[i] is None or w[i] is None or mid[i] is None:
            out.append(None)
            continue
        above_s, above_l = c > s[i], c > lng[i]
        if not above_s and not above_l and c < mid[i] - BAND_WIDTH * sd[i]:
            out.append(-4)
            continue
        out.append(2 * above_s + 2 * above_l + (s[i] > lng[i]) + 2 * (c > w[i]) - 3)
    return out
