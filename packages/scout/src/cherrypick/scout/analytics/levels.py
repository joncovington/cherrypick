"""SMA overlays and swing-extrema support/resistance clustering, computed purely from a list of daily
bars (``{"t","o","h","l","c","v"}``, oldest first) -- no I/O. Import-clean (stdlib only) so a future
promotion to ``cherrypick.core.levels`` is a file move once stable, same posture as ``payoff``/``pop``
(see the package README).
"""

from __future__ import annotations

SMA_WINDOWS = (20, 50, 200)


def sma(closes: list[float], window: int) -> list[float | None]:
    """Simple moving average, same length as `closes`. The first `window - 1` entries are `None`
    (not enough history yet) rather than an average over a short window pretending to be the real
    thing."""
    out: list[float | None] = [None] * len(closes)
    running = 0.0
    for i, c in enumerate(closes):
        running += c
        if i >= window:
            running -= closes[i - window]
        if i >= window - 1:
            out[i] = running / window
    return out


def moving_averages(bars: list[dict]) -> dict[str, list[float | None]]:
    """`{"sma20": [...], "sma50": [...], "sma200": [...]}`, each aligned to `bars` by index."""
    closes = [b["c"] for b in bars]
    return {f"sma{window}": sma(closes, window) for window in SMA_WINDOWS}


def _swing_extrema(bars: list[dict], *, lookback: int) -> tuple[list[float], list[float]]:
    """A bar is a swing high/low when its high/low is the *unique* extremum of the `lookback` bars on
    both sides of it. Uniqueness matters: a plain max/min check fires on every bar of a flat run (each
    one ties the window extremum), which would report a swing at every point of a sideways market --
    not the turn a swing is supposed to mark."""
    highs = [b["h"] for b in bars]
    lows = [b["l"] for b in bars]
    n = len(bars)
    swing_highs, swing_lows = [], []
    for i in range(lookback, n - lookback):
        window = slice(i - lookback, i + lookback + 1)
        h_window, l_window = highs[window], lows[window]
        if highs[i] == max(h_window) and h_window.count(highs[i]) == 1:
            swing_highs.append(highs[i])
        if lows[i] == min(l_window) and l_window.count(lows[i]) == 1:
            swing_lows.append(lows[i])
    return swing_highs, swing_lows


def _cluster(prices: list[float], tolerance_pct: float) -> list[tuple[float, int]]:
    """Merge swing prices within `tolerance_pct` of their neighbor into one level (mean price, touch
    count) -- several swings pinning the same zone should read as one level, not a cluttered many."""
    if not prices:
        return []
    ordered = sorted(prices)
    groups: list[list[float]] = [[ordered[0]]]
    for price in ordered[1:]:
        if abs(price - groups[-1][-1]) / groups[-1][-1] <= tolerance_pct:
            groups[-1].append(price)
        else:
            groups.append([price])
    return [(sum(g) / len(g), len(g)) for g in groups]


def support_resistance(bars: list[dict], *, lookback: int = 3, tolerance_pct: float = 0.005) -> list[dict]:
    """`[{"price", "kind": "support"|"resistance", "touches"}, ...]`, sorted by price."""
    swing_highs, swing_lows = _swing_extrema(bars, lookback=lookback)
    levels = [
        {"price": price, "kind": "resistance", "touches": touches}
        for price, touches in _cluster(swing_highs, tolerance_pct)
    ] + [
        {"price": price, "kind": "support", "touches": touches}
        for price, touches in _cluster(swing_lows, tolerance_pct)
    ]
    levels.sort(key=lambda level: level["price"])
    return levels
