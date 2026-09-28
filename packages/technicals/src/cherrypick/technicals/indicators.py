"""The standard indicators, as pure functions over a list of closes (or highs, lows and closes).

Each returns a list the same length as its input, with None where the window is not yet full, so a
reading is never taken from a partial window.
"""

from __future__ import annotations


def sma(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def ema(values: list[float], n: int) -> list[float | None]:
    """Seeded with the SMA of the first `n` values, then the usual 2 / (n + 1) smoothing."""
    out: list[float | None] = [None] * len(values)
    if len(values) < n:
        return out
    a = 2.0 / (n + 1)
    prev = sum(values[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(values)):
        prev = a * values[i] + (1 - a) * prev
        out[i] = prev
    return out


def rsi(closes: list[float], n: int = 14) -> list[float | None]:
    """Wilder's RSI: average gain and loss seeded with a simple mean, then Wilder-smoothed."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    avg_g, avg_l = sum(gains[:n]) / n, sum(losses[:n]) / n

    def value(g, lo):
        return 100.0 if lo == 0 else 100.0 - 100.0 / (1.0 + g / lo)

    out[n] = value(avg_g, avg_l)
    for i in range(n, len(gains)):
        avg_g = (avg_g * (n - 1) + gains[i]) / n
        avg_l = (avg_l * (n - 1) + losses[i]) / n
        out[i + 1] = value(avg_g, avg_l)
    return out


def cci(highs: list[float], lows: list[float], closes: list[float], n: int = 14) -> list[float | None]:
    """Lambert's CCI: (typical price - its SMA) / (0.015 x mean absolute deviation). Period 14 is the
    vendor's scanner period (its trade-ideas list names it)."""
    tp = [(h + lo + c) / 3.0 for h, lo, c in zip(highs, lows, closes, strict=True)]
    mean = sma(tp, n)
    out: list[float | None] = [None] * len(tp)
    for i in range(n - 1, len(tp)):
        m = mean[i]
        dev = sum(abs(x - m) for x in tp[i - n + 1 : i + 1]) / n
        out[i] = 0.0 if dev == 0 else (tp[i] - m) / (0.015 * dev)
    return out
