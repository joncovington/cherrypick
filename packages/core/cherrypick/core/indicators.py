"""The standard indicators, as pure functions over a list of closes (or highs, lows and closes).

Each returns a list the same length as its input, with None where the window is not yet full, so a
reading is never taken from a partial window. ATR, ADX and Supertrend are the textbook (Wilder)
definitions, not fitted to any vendor; the Vortex and the RSI divergence are in their authors'
definitions. The stochastic and MACD series serve `core.signals`.

The suite's one copy: `cherrypick.technicals.indicators` re-exports these names, and `core.signals`
reads them, so a chart, a scan rule and an entry signal can never disagree about what RSI(14) is.
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


def wma_at(values: list[float], n: int, i: int) -> float | None:
    """The WMA ending at index `i` -- the one formula `wma` applies at every index, so a caller that
    needs only the last value gets it bit-for-bit without the O(len x n) pass."""
    if i < n - 1:
        return None
    return sum(values[i - n + 1 + k] * (k + 1) for k in range(n)) / (n * (n + 1) / 2)


def wma(values: list[float], n: int) -> list[float | None]:
    """Linearly weighted: the newest value weighs n, the oldest 1."""
    return [wma_at(values, n, i) for i in range(len(values))]


def stdev_at(values: list[float], n: int, i: int) -> float | None:
    """The population standard deviation of the `n` values ending at `i` (the one formula `stdev` uses)."""
    if i < n - 1:
        return None
    window = values[i - n + 1 : i + 1]
    m = sum(window) / n
    return (sum((v - m) ** 2 for v in window) / n) ** 0.5


def stdev(values: list[float], n: int) -> list[float | None]:
    """The population standard deviation of the last `n` values (divide by n, as Bollinger bands do)."""
    return [stdev_at(values, n, i) for i in range(len(values))]


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


def bollinger(
    closes: list[float], n: int = 20, width: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """(upper, middle, lower): the SMA `n` +- `width` population standard deviations -- the band the
    trend scores' -4 is measured against."""
    mid, sd = sma(closes, n), stdev(closes, n)
    upper = [None if m is None else m + width * s for m, s in zip(mid, sd, strict=True)]
    lower = [None if m is None else m - width * s for m, s in zip(mid, sd, strict=True)]
    return upper, mid, lower


def true_range(highs: list[float], lows: list[float], closes: list[float]) -> list[float | None]:
    """The larger of the bar's range and its gap from the prior close; None on the first bar."""
    out: list[float | None] = [None]
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        out.append(max(highs[i] - lows[i], abs(highs[i] - prev), abs(lows[i] - prev)))
    return out[: len(closes)]


def _wilder(values: list[float | None], n: int, first: int) -> list[float | None]:
    """Wilder's smoothing of `values` from index `first`: seeded with the mean of the `n` values
    ending at `first + n - 1`, then avg = (avg x (n - 1) + v) / n."""
    out: list[float | None] = [None] * len(values)
    seed = first + n - 1
    if seed >= len(values):
        return out
    avg = sum(values[first : seed + 1]) / n
    out[seed] = avg
    for i in range(seed + 1, len(values)):
        avg = (avg * (n - 1) + values[i]) / n
        out[i] = avg
    return out


def atr(highs: list[float], lows: list[float], closes: list[float], n: int = 14) -> list[float | None]:
    """Wilder's average true range: the true ranges from the second bar, seeded with a simple mean
    (so, like RSI, the first value is at index `n`)."""
    return _wilder(true_range(highs, lows, closes), n, 1)


def adx(highs: list[float], lows: list[float], closes: list[float], n: int = 14) -> list[float | None]:
    """Wilder's ADX: +DM and -DM against the true range, each Wilder-smoothed over `n`, give +DI and
    -DI; DX is their spread over their sum, and ADX is DX Wilder-smoothed over `n` again. The first
    value is at index 2n - 1."""
    size = len(closes)
    plus: list[float | None] = [None] * size
    minus: list[float | None] = [None] * size
    for i in range(1, size):
        up, down = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        plus[i] = up if up > down and up > 0 else 0.0
        minus[i] = down if down > up and down > 0 else 0.0
    tr = _wilder(true_range(highs, lows, closes), n, 1)
    sp, sm = _wilder(plus, n, 1), _wilder(minus, n, 1)
    dx: list[float | None] = [None] * size
    for i in range(size):
        if tr[i] is None:
            continue
        if tr[i] == 0:  # no range at all: no direction either, and a gap here would stop the smoothing
            dx[i] = 0.0
            continue
        di_p, di_m = 100.0 * sp[i] / tr[i], 100.0 * sm[i] / tr[i]
        dx[i] = 0.0 if di_p + di_m == 0 else 100.0 * abs(di_p - di_m) / (di_p + di_m)
    first = next((i for i, v in enumerate(dx) if v is not None), size)
    return _wilder(dx, n, first)


def supertrend(
    highs: list[float], lows: list[float], closes: list[float], n: int = 10, mult: float = 3.0
) -> tuple[list[float | None], list[bool | None]]:
    """(line, up): the ATR band around the bar's midpoint that only ever ratchets toward price. While
    up, the line is the lower band and a close under it turns the trend down; while down, the line is
    the upper band and a close over it turns it up. Starts up on the first bar ATR is defined."""
    a = atr(highs, lows, closes, n)
    size = len(closes)
    line: list[float | None] = [None] * size
    up: list[bool | None] = [None] * size
    fu = fl = None  # the final upper and lower bands
    trend_up = True
    for i in range(size):
        if a[i] is None:
            continue
        mid = (highs[i] + lows[i]) / 2.0
        bu, bl = mid + mult * a[i], mid - mult * a[i]
        if fu is None:
            fu, fl = bu, bl
        else:
            prev = closes[i - 1]
            fu = bu if bu < fu or prev > fu else fu
            fl = bl if bl > fl or prev < fl else fl
            if trend_up and closes[i] < fl:
                trend_up = False
            elif not trend_up and closes[i] > fu:
                trend_up = True
        up[i] = trend_up
        line[i] = fl if trend_up else fu
    return line, up


def vortex(
    highs: list[float], lows: list[float], closes: list[float], n: int = 14
) -> tuple[list[float | None], list[float | None]]:
    """(VI+, VI-): Botes & Siepman's Vortex Indicator (TASC, January 2010). VM+ is |high - prior low|
    and VM- is |low - prior high|; each is summed over the last `n` bars and divided by the true range
    summed over the same bars -- plain sums, not Wilder averages. The first value is at index `n`;
    None where the summed range is zero (no movement says nothing about direction)."""
    size = len(closes)
    plus: list[float | None] = [None] * size
    minus: list[float | None] = [None] * size
    tr = true_range(highs, lows, closes)
    vm_p = [None] + [abs(highs[i] - lows[i - 1]) for i in range(1, size)]
    vm_m = [None] + [abs(lows[i] - highs[i - 1]) for i in range(1, size)]
    for i in range(n, size):
        span = sum(tr[i - n + 1 : i + 1])
        if span > 0:
            plus[i] = sum(vm_p[i - n + 1 : i + 1]) / span
            minus[i] = sum(vm_m[i - n + 1 : i + 1]) / span
    return plus, minus


def _pivots(values: list[float | None], k: int, high: bool) -> list[int]:
    """Indices whose value is beyond each of the `k` before it and not passed by any of the `k`
    after it (above for highs, below for lows) -- `swings.py`'s rule, on any series. Every value in
    the window must be defined."""
    out = []
    for p in range(k, len(values) - k):
        window = values[p - k : p + k + 1]
        if None in window:
            continue
        v, before, after = values[p], values[p - k : p], values[p + 1 : p + k + 1]
        if high and v > max(before) and v >= max(after):
            out.append(p)
        elif not high and v < min(before) and v <= min(after):
            out.append(p)
    return out


def swing_points(values: list[float | None], k: int, high: bool) -> list[int]:
    """The swing highs (or lows) of a series, oldest first: `_pivots`' rule, which the divergence
    reads, under a public name for `core.signals`. A point is known only `k` values after it."""
    return _pivots(values, k, high)


def divergence(
    highs: list[float],
    lows: list[float],
    osc: list[float | None],
    k: int = 5,
    span: tuple[int, int] = (5, 60),
) -> tuple[list[bool], list[bool]]:
    """(bearish, bullish): at each bar, whether the latest two oscillator pivots confirmed by then
    form a regular divergence. A pivot is confirmed `k` bars after it. Bearish: the later pivot high
    of `osc` is lower than the one before while the price HIGH on its bar is higher (price is read
    at the oscillator's pivot bars, as TradingView's Divergence Indicator does). Bullish: the later
    pivot low is higher while the price LOW on its bar is lower. The two pivots must be between
    `span` bars apart."""
    lo, hi = span
    size = len(osc)

    def state(pivots: list[int], price: list[float], bearish: bool) -> list[bool]:
        out = [False] * size
        j = -1  # the latest pivot confirmed by bar i
        for i in range(size):
            while j + 1 < len(pivots) and pivots[j + 1] + k <= i:
                j += 1
            if j < 1:
                continue
            p1, p2 = pivots[j - 1], pivots[j]
            if not lo <= p2 - p1 <= hi:
                continue
            if bearish:
                out[i] = osc[p2] < osc[p1] and price[p2] > price[p1]
            else:
                out[i] = osc[p2] > osc[p1] and price[p2] < price[p1]
        return out

    return state(_pivots(osc, k, True), highs, True), state(_pivots(osc, k, False), lows, False)


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


def _on_tail(fn, values: list[float | None], n: int) -> list[float | None]:
    """`fn` (sma or ema) applied from the first defined value on, so a series that starts undefined
    (a %K, a MACD line) is averaged without its leading Nones. A None after that point is a defect in
    the input and raises."""
    first = next((i for i, v in enumerate(values) if v is not None), len(values))
    tail = values[first:]
    if any(v is None for v in tail):
        raise ValueError("a series may be undefined only at its start")
    return [None] * first + fn(tail, n)


def stochastic(
    highs: list[float], lows: list[float], closes: list[float], k_period: int, k_smooth: int, d_smooth: int
) -> tuple[list[float | None], list[float | None]]:
    """(%K, %D): the slow stochastic. Raw %K is where the close sits in the last `k_period` bars' range
    (a flat range reads 50); %K is its simple mean over `k_smooth` and %D the simple mean of %K over
    `d_smooth`. Ensemble's last-value `stochastic` is this series' final pair."""
    size = len(closes)
    raw: list[float | None] = [None] * size
    for i in range(k_period - 1, size):
        hi, lo = max(highs[i - k_period + 1 : i + 1]), min(lows[i - k_period + 1 : i + 1])
        raw[i] = 50.0 if hi == lo else 100.0 * (closes[i] - lo) / (hi - lo)
    k = _on_tail(sma, raw, k_smooth)
    return k, _on_tail(sma, k, d_smooth)


def macd(
    closes: list[float], fast: int, slow: int, signal: int
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """(line, signal, histogram): Appel's MACD. The line is EMA(`fast`) minus EMA(`slow`) of the
    closes, the signal line the EMA(`signal`) of the line, the histogram their difference. Each EMA
    is `ema`'s (seeded with a simple mean)."""
    f, s = ema(closes, fast), ema(closes, slow)
    line = [None if a is None or b is None else a - b for a, b in zip(f, s, strict=True)]
    sig = _on_tail(ema, line, signal)
    hist = [None if a is None or b is None else a - b for a, b in zip(line, sig, strict=True)]
    return line, sig, hist
