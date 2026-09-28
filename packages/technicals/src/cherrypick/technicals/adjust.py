"""Split and dividend adjustment of daily bars, as a pure function over raw bars and events.

The store keeps Dolt's RAW bars and its split and dividend tables; the adjusted series is computed
from them on read, so it can be rebuilt from raw at any time and a restated dividend changes it
everywhere at once.

**The method is the vendor's, checked to the cent.** Proportional ("CRSP") adjustment: every bar
before an ex-dividend date is scaled by ``1 - D / P``, where ``D`` is the dividend and ``P`` the raw
close of the session before the ex-date; every bar before a split by ``for / to`` (volume by the
inverse). Against the vendor's own chart data (2026-09-25 capture) this reproduces MSFT's adjusted
closes on all 753 sessions exactly, and ANET's to within one cent on the 21 of 3,012 prices where a
pre-split division lands on a half cent (the vendor's raw prices carry more precision than Dolt's).
That is why adjusted prices are stored and returned UNROUNDED: rounding is a display decision.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Bar:
    date: str  # ISO session date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Split:
    ex_date: str
    to_factor: float  # shares after
    for_factor: float  # shares before


@dataclass(frozen=True)
class Dividend:
    ex_date: str
    amount: float  # per share, on the share basis of its own date


@dataclass(frozen=True)
class AdjustedBar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    price_factor: float  # adjusted = raw * price_factor


def adjust(bars: list[Bar], splits: list[Split] = (), dividends: list[Dividend] = ()) -> list[AdjustedBar]:
    """Adjusted bars, oldest first. An event applies to every bar BEFORE its ex-date; events outside
    the bars' span, and a dividend at or above the prior close (a data error, not a payout), change
    nothing."""
    bars = sorted(bars, key=lambda b: b.date)
    if not bars:
        return []
    split_on = {}
    for s in splits:
        if s.to_factor > 0 and s.for_factor > 0:
            split_on.setdefault(s.ex_date, []).append(s)
    div_on = {}
    for d in dividends:
        if d.amount > 0:
            div_on.setdefault(d.ex_date, []).append(d)
    events = sorted(set(split_on) | set(div_on))

    price_f, volume_f = 1.0, 1.0
    out: list[AdjustedBar] = []
    e = len(events) - 1
    for i in range(len(bars) - 1, -1, -1):
        bar = bars[i]
        out.append(
            AdjustedBar(
                bar.date,
                bar.open * price_f,
                bar.high * price_f,
                bar.low * price_f,
                bar.close * price_f,
                bar.volume * volume_f,
                price_f,
            )
        )
        if i == 0:
            break
        prev = bars[i - 1]
        # Events dated after the previous bar and on or before this one sit between the two
        # sessions: they apply to the previous bar and everything before it.
        while e >= 0 and events[e] > bar.date:
            e -= 1
        while e >= 0 and prev.date < events[e] <= bar.date:
            for s in split_on.get(events[e], []):
                price_f *= s.for_factor / s.to_factor
                volume_f *= s.to_factor / s.for_factor
            for d in div_on.get(events[e], []):
                if prev.close > 0 and d.amount < prev.close:
                    price_f *= 1.0 - d.amount / prev.close
            e -= 1
    out.reverse()
    return out


# Two data defects the stage table exposed on 2026-09-28, both corrected before adjustment:
#
# - Dolt records some splits twice, a week or two apart (APH's 2-for-1 on 2024-05-31 AND 2024-06-12;
#   CNQ's on 2024-06-03 AND 2024-06-11). Applying both halves the older prices again and fakes a
#   jump. Of two same-ratio splits within DUPLICATE_DAYS, only the one the raw prices jump on is kept.
# - A ticker that changed hands carries another security's history (BNY: x13.6 in a day on
#   2026-05-21; SPCX x8.8; HUT x4.6 at the 2023 merger). A one-day move beyond BREAK_RATIO either way,
#   on adjusted closes, starts the series over. Genuine one-day moves this large are rare enough that
#   losing one to this rule costs less than one silently wrong 6-month return.
DUPLICATE_DAYS = 20
BREAK_RATIO = 3.0


def dedupe_splits(bars: list[Bar], splits: list[Split]) -> list[Split]:
    from datetime import date as _date

    closes = {b.date: b.close for b in bars}
    dates = sorted(closes)

    def fit(s: Split) -> float:
        """How far the raw close-to-close ratio across the ex-date is from the split's own ratio
        (0 = a perfect split); infinite when the ex-date is outside the bars."""
        after = next((d for d in dates if d >= s.ex_date), None)
        if after is None or dates.index(after) == 0:
            return float("inf")
        before = dates[dates.index(after) - 1]
        ratio = closes[after] / closes[before]
        return abs(ratio - s.for_factor / s.to_factor)

    kept: list[Split] = []
    # A zero factor is a bad row; `adjust` ignores it too, and it would divide by zero here.
    valid = [s for s in splits if s.to_factor > 0 and s.for_factor > 0]
    for s in sorted(valid, key=lambda x: x.ex_date):

        def near(k: Split, s: Split = s) -> bool:
            days = abs((_date.fromisoformat(s.ex_date) - _date.fromisoformat(k.ex_date)).days)
            return k.to_factor / k.for_factor == s.to_factor / s.for_factor and days <= DUPLICATE_DAYS

        twin = next((k for k in kept if near(k)), None)
        if twin is None:
            kept.append(s)
        elif fit(s) < fit(twin):
            kept[kept.index(twin)] = s
    return kept


def series_break(bars: list[AdjustedBar]) -> int:
    """The index the series starts at: just after the last one-day move beyond BREAK_RATIO."""
    start = 0
    for i in range(1, len(bars)):
        a, b = bars[i - 1].close, bars[i].close
        if a > 0 and b > 0 and not (1 / BREAK_RATIO <= b / a <= BREAK_RATIO):
            start = i
    return start
