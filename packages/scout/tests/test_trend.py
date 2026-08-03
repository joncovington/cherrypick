from datetime import date, timedelta

import pytest

from cherrypick.scout.analytics import trend as _trend

BULLISH_SIDE = {_trend.BULLISH, _trend.MILDLY_BULLISH}
BEARISH_SIDE = {_trend.BEARISH, _trend.MILDLY_BEARISH}


def _accel_up(n=300, base=100.0):
    """Accelerating uptrend -- unambiguous 'bullish' for every model family (a merely-linear rise
    produces knife-edge ties in the MACD/TRIX secondary signals, which is exactly the fragility
    these tests should avoid encoding)."""
    return [base * (1.01**i) for i in range(n)]


def _accel_down(n=300, base=100.0):
    return [base * (0.99**i) for i in range(n)]


def _bars_portable(closes, start=date(2026, 1, 5)):
    """Daily bars with real weekday timestamps (weekends skipped) so weekly resampling is honest."""
    from datetime import datetime, time

    out = []
    day = start
    for close in closes:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        epoch = int(datetime.combine(day, time(hour=16)).timestamp())
        out.append({"t": epoch, "c": close})
        day += timedelta(days=1)
    return out


# --------------------------------------------------------------------------- primitives


def test_ema_of_a_constant_series_is_the_constant():
    series = _trend.ema_series([5.0] * 40, 10)
    assert series is not None
    assert all(v == pytest.approx(5.0) for v in series)


def test_ema_returns_none_when_too_short():
    assert _trend.ema_series([1.0, 2.0], 10) is None
    assert _trend.ema_series([], 1) is None


def test_weekly_closes_takes_the_last_close_of_each_week():
    # 2026-01-05 is a Monday; ten weekday closes span exactly two ISO weeks.
    bars = _bars_portable(list(range(10)), start=date(2026, 1, 5))
    weekly = _trend.weekly_closes(bars)
    assert weekly == [4, 9]  # Friday of week 1, Friday of week 2


# --------------------------------------------------------------------------- the four candidates


def test_alignment_full_ordering_is_bullish_and_reverse_is_bearish():
    assert _trend.triple_ma_alignment(_accel_up(), 5, 10, 21) == _trend.BULLISH
    assert _trend.triple_ma_alignment(_accel_down(), 5, 10, 21) == _trend.BEARISH
    assert _trend.triple_ma_alignment([100.0] * 10, 5, 10, 21) is None


def test_alignment_grades_a_recent_reversal_as_mildly():
    # A long climb with a brief sharp drop: the fast EMA flips below the mid while the longer
    # ordering still remembers the climb -- an extreme grade must not fire.
    up = _accel_up(250)
    closes = up + [up[-1] * (0.98**i) for i in range(1, 5)]
    assert _trend.triple_ma_alignment(closes, 5, 10, 21) == _trend.MILDLY_BULLISH


def _hard_down(n=200):
    """A decline that *accelerates* in absolute terms -- the shape that makes MACD/TRIX read
    unambiguously bearish. (An exponential-decay series like 0.99^i decelerates, so its momentum
    legs genuinely improve and the grade is legitimately only mildly_bearish.)"""
    return [800.0 - 100.0 * (1.01**i) for i in range(n)]


def test_macd_state_extremes_and_insufficient_data():
    assert _trend.macd_state(_accel_up()) == _trend.BULLISH
    assert _trend.macd_state(_hard_down()) == _trend.BEARISH
    assert _trend.macd_state([100.0] * 20) is None


def test_macd_zero_line_is_primary_a_decelerating_decline_stays_bearish_side():
    """The modeling decision the first draft got wrong: a 0.99^i decay has MACD below zero but
    rising (momentum improving). For a *trend* label that must read mildly_bearish -- still a
    downtrend -- never mildly_bullish."""
    assert _trend.macd_state(_accel_down()) == _trend.MILDLY_BEARISH


def test_tema_of_a_constant_series_is_the_constant():
    tema = _trend.tema_series([7.0] * 100, 10)
    assert tema is not None
    assert tema[-1] == pytest.approx(7.0)


def test_tema_trend_extremes():
    assert _trend.tema_trend(_accel_up(), 21) == _trend.BULLISH
    assert _trend.tema_trend(_accel_down(), 21) == _trend.BEARISH
    assert _trend.tema_trend([100.0] * 10, 21) is None


def test_trix_trend_extremes():
    # Accelerating percentage growth so TRIX is both positive and rising (see _accel_up's note).
    accelerating = [100.0 * (1.0 + 0.0002 * i) ** i for i in range(1, 200)]
    assert _trend.trix_trend(accelerating) == _trend.BULLISH
    assert _trend.trix_trend(_accel_down()) == _trend.BEARISH
    assert _trend.trix_trend([100.0] * 20) is None


# --------------------------------------------------------------------------- the harness


def test_classify_all_labels_every_model_at_both_horizons():
    bars = _bars_portable(_accel_up(700))
    result = _trend.classify_all(bars)
    assert set(result) == {"alignment", "macd", "tema", "trix"}
    for model, horizons in result.items():
        assert set(horizons) == {"1m", "6m"}
        for horizon, label in horizons.items():
            assert label in BULLISH_SIDE, f"{model}/{horizon} gave {label} on a strong uptrend"


def test_classify_all_on_a_downtrend_lands_every_label_on_the_bearish_side():
    bars = _bars_portable(_accel_down(700))
    result = _trend.classify_all(bars)
    for model, horizons in result.items():
        for horizon, label in horizons.items():
            assert label in BEARISH_SIDE, f"{model}/{horizon} gave {label} on a strong downtrend"


def test_classify_all_with_thin_history_degrades_to_none_not_an_error():
    bars = _bars_portable(_accel_up(30))  # enough for some 1m models, nowhere near 6m
    result = _trend.classify_all(bars)
    assert result["alignment"]["6m"] is None
    assert result["tema"]["6m"] is None
