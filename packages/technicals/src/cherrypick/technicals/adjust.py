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
