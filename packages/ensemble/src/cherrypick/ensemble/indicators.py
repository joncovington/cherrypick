"""Readings from the day's spot trail and the daily closes. Pure functions.

Daily SMA, EMA and RSI are `cherrypick.technicals.indicators`' (the textbook definitions); this module adds
what that one lacks: the trail's N-minute bars, the stochastic, the opening range and the
standard-deviation move.
"""

from __future__ import annotations

import math

from cherrypick.technicals import indicators as ti

OPEN_MINUTE = 570  # 09:30


def bars(trail: list[tuple[int, float]], size: int, *, until: int | None = None) -> list[dict]:
    """The trail as `size`-minute bars from the open: {start, open, high, low, close}. Only bars that
    have COMPLETED by `until` are returned (a bar covering minutes start..start+size-1 completes at
    start+size), so a reading never uses a half-formed bar."""
    out: dict[int, dict] = {}
    for m, s in trail:
        if m < OPEN_MINUTE:
            continue
        start = OPEN_MINUTE + ((m - OPEN_MINUTE) // size) * size
        b = out.get(start)
        if b is None:
            out[start] = {"start": start, "open": s, "high": s, "low": s, "close": s}
        else:
            b["high"] = max(b["high"], s)
            b["low"] = min(b["low"], s)
            b["close"] = s
    done = [out[k] for k in sorted(out)]
    if until is not None:
        done = [b for b in done if b["start"] + size <= until]
    return done


def intraday_rsi(trail, size: int, n: int, *, until: int | None = None) -> float | None:
    """Wilder RSI(n) of the completed `size`-minute closes; None until there are n + 1 of them."""
    closes = [b["close"] for b in bars(trail, size, until=until)]
    out = ti.rsi(closes, n)
    return out[-1] if out else None


def stochastic(
    bar_list: list[dict], k_period: int = 14, k_smooth: int = 3, d_smooth: int = 3
) -> tuple[float, float] | None:
    """Slow stochastic Stoch(k_period, k_smooth, d_smooth) of `bar_list`: raw %K over the last k_period
    bars, smoothed by a simple mean of k_smooth (the line compared with a level), and %D the simple mean
    of d_smooth of those. (K, D), or None before enough bars. A flat window reads 50."""
    need = k_period + k_smooth + d_smooth - 2
    if len(bar_list) < need:
        return None
    raw = []
    for i in range(k_period - 1, len(bar_list)):
        w = bar_list[i - k_period + 1 : i + 1]
        hi, lo = max(b["high"] for b in w), min(b["low"] for b in w)
        raw.append(50.0 if hi == lo else 100.0 * (bar_list[i]["close"] - lo) / (hi - lo))
    ks = [sum(raw[i - k_smooth + 1 : i + 1]) / k_smooth for i in range(k_smooth - 1, len(raw))]
    if len(ks) < d_smooth:
        return None
    return ks[-1], sum(ks[-d_smooth:]) / d_smooth


def opening_range(trail: list[tuple[int, float]], minutes: int) -> dict | None:
    """The first `minutes` of the session (09:30 to 09:30 + minutes): {high, low, done_at}. None until the
    trail has covered the whole range."""
    end = OPEN_MINUTE + minutes
    inside = [s for m, s in trail if OPEN_MINUTE <= m < end]
    if not inside or max(m for m, _ in trail) < end:
        return None
    return {"high": max(inside), "low": min(inside), "done_at": end}


def day_high_after(trail: list[tuple[int, float]], minute: int) -> float | None:
    """The highest spot seen from the open through `minute`."""
    seen = [s for m, s in trail if OPEN_MINUTE <= m <= minute]
    return max(seen) if seen else None


def daily_sigma(prev_close: float, vix_prev_close: float | None) -> float | None:
    """One day's standard deviation in index points, from the previous close's VIX: the quoted
    annualised volatility over sqrt(252). None without a VIX."""
    if vix_prev_close is None or not vix_prev_close > 0:
        return None
    return prev_close * (vix_prev_close / 100.0) / math.sqrt(252.0)


def sd_change(spot: float, prev_close: float, vix_prev_close: float | None) -> float | None:
    s = daily_sigma(prev_close, vix_prev_close)
    return None if not s else (spot - prev_close) / s


def above_average(closes: list[float], spot: float, kind: str, n: int) -> bool | None:
    """Spot above the `n`-day simple or exponential average of the daily closes before today. None
    until there are n closes."""
    fn = {"sma": ti.sma, "ema": ti.ema}[kind]
    out = fn(closes, n)
    if not out or out[-1] is None:
        return None
    return spot > out[-1]
