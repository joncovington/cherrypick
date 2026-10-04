"""Which names the historical study counts on a session, chosen with no hindsight.

A name qualifies on a session when, from its bars up to the session BEFORE (never the session
itself, never anything later):

- its close is at least MIN_PRICE, and
- the median of its daily dollar volume (close x volume) over the WINDOW sessions is at least
  MIN_DOLLAR_VOLUME.

Raw bars, not adjusted: a price test on split-adjusted history would read a 2012 NVDA at thirty
cents. A missing volume counts as zero, which can only keep a name out. The thresholds are the ones
decided on 2026-10-04 (docs/signal-log-plan.md): names a trader could fill at the open, about 1,050
in 2011 rising to about 2,900 by 2026.

VIEW_DOLLAR_VOLUME is the study's "$300M a day" view: the best stock-only stand-in for options
liquidity found (76% of such names list weeklies), a view on the results, never a filter on them.
"""

from __future__ import annotations

from bisect import bisect_left, insort

MIN_PRICE = 5.0
MIN_DOLLAR_VOLUME = 20e6
WINDOW = 50
VIEW_DOLLAR_VOLUME = 300e6


def rolling_median(values: list[float], n: int = WINDOW) -> list[float | None]:
    """The median of the `n` values ending at each index; None until `n` are held. A sorted window
    kept with bisect, so each step is a C-level insert and delete, not a sort."""
    out: list[float | None] = [None] * len(values)
    window: list[float] = []
    for i, v in enumerate(values):
        insort(window, v)
        if i >= n:
            del window[bisect_left(window, values[i - n])]
        if i >= n - 1:
            k = n // 2
            out[i] = window[k] if n % 2 else (window[k - 1] + window[k]) / 2.0
    return out


def membership(closes: list[float], volumes: list[float]) -> tuple[list[bool], list[float | None]]:
    """(qualifies on each session, the median dollar volume that decided it), both from the session
    before. The first WINDOW sessions never qualify: there is not yet a full window behind them."""
    size = len(closes)
    dollar = [c * (v or 0.0) for c, v in zip(closes, volumes, strict=True)]
    if not dollar or max(dollar) < MIN_DOLLAR_VOLUME:
        return [False] * size, [None] * size  # a median can never exceed the largest day
    med = rolling_median(dollar)
    ok = [False] * size
    basis: list[float | None] = [None] * size
    for i in range(1, size):
        m = med[i - 1]
        basis[i] = m
        ok[i] = m is not None and closes[i - 1] >= MIN_PRICE and m >= MIN_DOLLAR_VOLUME
    return ok, basis
