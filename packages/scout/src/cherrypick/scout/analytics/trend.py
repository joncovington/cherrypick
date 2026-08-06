"""Candidate trend classifiers -- four rival implementations of a "triple moving average" trend
model, built to be *fitted against observed labels*, not shipped as truth.

Context: a commercial research platform describes its proprietary 1M/6M trend indicators only as
"derived from a triple moving average method." That phrase has four established readings, and each
leaves a different fingerprint in the output. This module implements all four side by side so the
winner can be chosen empirically -- score each candidate's labels against a hand-collected sample
of the reference indicator's output and keep whichever agrees most. Until that experiment runs,
every parameter default below is a documented guess, not a finding.

Same posture as the rest of `analytics/`: stdlib only, no I/O, closes in -> labels out. All four
models emit the same four-grade scale (``bullish`` / ``mildly_bullish`` / ``mildly_bearish`` /
``bearish``, or ``None`` when there's not enough history), so their outputs are directly
comparable row-for-row.

The four candidates:

1. ``triple_ma_alignment`` -- three EMAs of increasing length; the grade is the *count* of bullish
   pairwise orderings (fast>mid, mid>slow, fast>slow): 3 -> bullish, 2 -> mildly_bullish,
   1 -> mildly_bearish, 0 -> bearish. The most literal reading of "triple moving average method",
   and the only candidate whose four grades fall out of the structure with no thresholds.
2. ``macd_state`` -- MACD (fast/slow EMAs + a signal EMA: three EMAs, hence a legitimate "triple"
   description) graded on (line > signal, line > 0).
3. ``tema_trend`` -- Mulloy's TEMA (3*EMA1 - 3*EMA2 + EMA3), graded on (price > TEMA, TEMA rising).
4. ``trix_trend`` -- TRIX (1-bar rate of change of a triple-smoothed EMA), graded on
   (TRIX > 0, TRIX > its signal EMA).

Horizons: "1M" runs on daily closes with month-scale parameters; "6M" runs either on daily closes
with ~126-bar parameters (alignment, TEMA) or on weekly-resampled closes with the same month-scale
parameters (MACD, TRIX) -- matching how each family is conventionally scaled up.
"""

from __future__ import annotations

BULLISH = "bullish"
MILDLY_BULLISH = "mildly_bullish"
NEUTRAL = "neutral"  # only the 5-grade candidate emits this; observed labels include it
MILDLY_BEARISH = "mildly_bearish"
BEARISH = "bearish"

#: (primary, secondary) booleans -> grade, shared by the three single-line models (MACD/TEMA/TRIX):
#: primary bull + secondary bull -> bullish; primary bull alone -> mildly_bullish; etc.
_TWO_SIGNAL_GRADES = {
    (True, True): BULLISH,
    (True, False): MILDLY_BULLISH,
    (False, True): MILDLY_BEARISH,
    (False, False): BEARISH,
}


def ema_series(closes: list[float], period: int) -> list[float] | None:
    """Standard EMA, seeded with the SMA of the first `period` closes (the conventional seeding --
    matters because these models compare EMAs to each other, so all must seed the same way).
    Returns a series aligned to `closes` from index `period - 1` on, or None if too short."""
    if period <= 0 or len(closes) < period:
        return None
    alpha = 2.0 / (period + 1)
    seed = sum(closes[:period]) / period
    out = [seed]
    for close in closes[period:]:
        out.append(out[-1] + alpha * (close - out[-1]))
    return out


def weekly_closes(bars: list[dict]) -> list[float]:
    """Resample daily bars ({"t": epoch_seconds, "c": close}) to one close per ISO week (the last
    daily close of each week) -- how the MACD/TRIX candidates conventionally reach a 6M horizon."""
    from datetime import date

    out: list[float] = []
    current_week: tuple[int, int] | None = None
    for bar in bars:
        iso = date.fromtimestamp(bar["t"]).isocalendar()
        week = (iso[0], iso[1])
        if week == current_week:
            out[-1] = bar["c"]
        else:
            out.append(bar["c"])
            current_week = week
    return out


# --------------------------------------------------------------------- candidate 1: alignment
def triple_ma_alignment(closes: list[float], fast: int, mid: int, slow: int) -> str | None:
    """Grade = how many of the three pairwise orderings are bullish. No thresholds anywhere --
    the four grades are structural."""
    emas = [ema_series(closes, p) for p in (fast, mid, slow)]
    if any(e is None for e in emas):
        return None
    f, m, s = (e[-1] for e in emas)
    score = int(f > m) + int(m > s) + int(f > s)
    return {3: BULLISH, 2: MILDLY_BULLISH, 1: MILDLY_BEARISH, 0: BEARISH}[score]


def triple_ma_price_alignment(closes: list[float], fast: int, mid: int, slow: int) -> str | None:
    """The 5-grade variant, added after label collection showed the reference indicator emits
    *five* states (Bullish / Mildly Bullish / Neutral / Mildly Bearish / Bearish) -- a four-state
    model can never say Neutral. Same triple-MA machinery plus one more structural boolean,
    price > fast EMA: the 0-4 bullish count maps onto the five grades with no thresholds."""
    emas = [ema_series(closes, p) for p in (fast, mid, slow)]
    if any(e is None for e in emas):
        return None
    f, m, s = (e[-1] for e in emas)
    score = int(closes[-1] > f) + int(f > m) + int(m > s) + int(f > s)
    return {4: BULLISH, 3: MILDLY_BULLISH, 2: NEUTRAL, 1: MILDLY_BEARISH, 0: BEARISH}[score]


def sma_last(closes: list[float], period: int) -> float | None:
    if period <= 0 or len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def price_ma_count(closes: list[float], fast: int, mid: int, slow: int) -> str | None:
    """The current best-fitting candidate (17/25 = 68% exact at BOTH horizons against the observed
    labels, vs <=36% for every other family): price vs each of three SMAs plus one ordering check
    (fast > slow), 0-4 bullish count -> the five grades. Fitted on 25 hand-collected rows dated
    2026-08-02/03 with every 1M miss exactly one grade adjacent -- promising, but a 25-row fit is a
    hypothesis, not a finding; re-validate against a fresh same-day label batch before this ever
    drives a user-facing surface."""
    vals = [sma_last(closes, p) for p in (fast, mid, slow)]
    if any(v is None for v in vals):
        return None
    f, m, s = vals
    price = closes[-1]
    score = int(price > f) + int(price > m) + int(price > s) + int(f > s)
    return {4: BULLISH, 3: MILDLY_BULLISH, 2: NEUTRAL, 1: MILDLY_BEARISH, 0: BEARISH}[score]


# --------------------------------------------------------------------- candidate 2: MACD state
def macd_state(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> str | None:
    """MACD line vs zero (primary -- which side of the trend you're on) and vs its signal line
    (secondary -- whether momentum currently confirms it). Zero-line-primary matters for a *trend*
    label: a decelerating decline has the line below zero but rising, which should read
    mildly_bearish (improving, still a downtrend), not mildly_bullish -- caught by the synthetic
    downtrend test when this was ordered the other way."""
    fast_ema = ema_series(closes, fast)
    slow_ema = ema_series(closes, slow)
    if fast_ema is None or slow_ema is None:
        return None
    # Align the two EMA series on their common (trailing) span before differencing.
    n = min(len(fast_ema), len(slow_ema))
    macd_line = [f - s for f, s in zip(fast_ema[-n:], slow_ema[-n:], strict=True)]
    signal_line = ema_series(macd_line, signal)
    if signal_line is None:
        return None
    return _TWO_SIGNAL_GRADES[(macd_line[-1] > 0, macd_line[-1] > signal_line[-1])]


# --------------------------------------------------------------------- candidate 3: TEMA
def tema_series(closes: list[float], period: int) -> list[float] | None:
    """Mulloy's Triple Exponential Moving Average: 3*EMA1 - 3*EMA2 + EMA3, where each EMA smooths
    the previous one."""
    e1 = ema_series(closes, period)
    if e1 is None:
        return None
    e2 = ema_series(e1, period)
    if e2 is None:
        return None
    e3 = ema_series(e2, period)
    if e3 is None:
        return None
    n = min(len(e1), len(e2), len(e3))
    return [3 * a - 3 * b + c for a, b, c in zip(e1[-n:], e2[-n:], e3[-n:], strict=True)]


def tema_trend(closes: list[float], period: int) -> str | None:
    """Price above/below TEMA (primary), TEMA rising/falling over the last bar (secondary).

    Requires ``4 * period`` bars of history, not the bare ``3 * period`` that makes the arithmetic
    possible: each smoothing stage seeds from an SMA of the previous stage, so the innermost EMA of
    a barely-long-enough series is still dominated by its seed -- a synthetic 400-bar monotonic
    *downtrend* classified as "bullish" under TEMA(126) before this floor existed, because the
    seed-biased third stage dragged the whole TEMA below even a collapsed price."""
    if len(closes) < 4 * period:
        return None
    tema = tema_series(closes, period)
    if tema is None or len(tema) < 2:
        return None
    return _TWO_SIGNAL_GRADES[(closes[-1] > tema[-1], tema[-1] > tema[-2])]


# --------------------------------------------------------------------- candidate 4: TRIX
def trix_trend(closes: list[float], period: int = 15, signal: int = 9) -> str | None:
    """TRIX above/below zero (primary), TRIX above/below its signal EMA (secondary)."""
    e1 = ema_series(closes, period)
    if e1 is None:
        return None
    e2 = ema_series(e1, period)
    if e2 is None:
        return None
    e3 = ema_series(e2, period)
    if e3 is None or len(e3) < 2:
        return None
    trix = [(b - a) / a * 100 if a != 0 else 0.0 for a, b in zip(e3, e3[1:], strict=False)]
    signal_line = ema_series(trix, signal)
    if signal_line is None:
        return None
    return _TWO_SIGNAL_GRADES[(trix[-1] > 0, trix[-1] > signal_line[-1])]


# --------------------------------------------------------------------- the comparison harness
#: Documented parameter guesses per horizon -- the things the label-fitting experiment will tune.
#: "1M" targets a ~21-trading-day horizon, "6M" ~126 days. MACD/TRIX reach 6M by running their
#: standard month-scale parameters on weekly closes instead of stretching the EMA lengths.
DEFAULT_PARAMS = {
    "alignment": {"1m": (5, 10, 21), "6m": (21, 63, 126)},
    # The reference platform's Price Action commentary references the 50-day MA by name, so the
    # 5-grade candidate's guesses lean on conventional 10/20/50 (1M) and 20/50/200 (6M) sets.
    "alignment_px": {"1m": (10, 20, 50), "6m": (20, 50, 200)},
    # Sweep winners on the 25-row label set (2026-08-03) -- provisional until re-validated.
    "price_ma_count": {"1m": (20, 26, 30), "6m": (15, 21, 50)},
    "tema": {"1m": 21, "6m": 126},
    "macd": {"1m": (12, 26, 9), "6m": (12, 26, 9)},  # 6m runs on weekly closes
    "trix": {"1m": (15, 9), "6m": (15, 9)},  # 6m runs on weekly closes
}


def classify_all(bars: list[dict]) -> dict[str, dict[str, str | None]]:
    """Every candidate's 1M and 6M label for one symbol's daily bars -- the row the label-fitting
    experiment scores against an observed (1M, 6M) pair. `{model: {"1m": label, "6m": label}}`."""
    closes = [b["c"] for b in bars]
    weekly = weekly_closes(bars)
    p = DEFAULT_PARAMS
    return {
        "alignment": {
            "1m": triple_ma_alignment(closes, *p["alignment"]["1m"]),
            "6m": triple_ma_alignment(closes, *p["alignment"]["6m"]),
        },
        "alignment_px": {
            "1m": triple_ma_price_alignment(closes, *p["alignment_px"]["1m"]),
            "6m": triple_ma_price_alignment(closes, *p["alignment_px"]["6m"]),
        },
        "price_ma_count": {
            "1m": price_ma_count(closes, *p["price_ma_count"]["1m"]),
            "6m": price_ma_count(closes, *p["price_ma_count"]["6m"]),
        },
        "macd": {
            "1m": macd_state(closes, *p["macd"]["1m"]),
            "6m": macd_state(weekly, *p["macd"]["6m"]),
        },
        "tema": {
            "1m": tema_trend(closes, p["tema"]["1m"]),
            "6m": tema_trend(closes, p["tema"]["6m"]),
        },
        "trix": {
            "1m": trix_trend(closes, *p["trix"]["1m"]),
            "6m": trix_trend(weekly, *p["trix"]["6m"]),
        },
    }
