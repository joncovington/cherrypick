"""The six scan rules, each pinned at its edge."""

from __future__ import annotations

from cherrypick.technicals.signals import Readings, matches


def R(short=0, long=0, cci5=0.0, cci5_prev=0.0, cci14=0.0, rsi14=50.0):
    return Readings(short, long, cci5, cci5_prev, cci14, rsi14)


def test_counter_trend_needs_both_trends_extreme_and_rsi_stretched():
    assert "BearishCounterTrend" in matches(R(4, 4, rsi14=72))
    assert "BearishCounterTrend" not in matches(R(4, 4, rsi14=71.9))
    assert "BearishCounterTrend" not in matches(R(4, 3, rsi14=80)), "both trends must be extreme"
    assert "BullishCounterTrend" in matches(R(-4, -4, rsi14=32))
    assert "BullishCounterTrend" not in matches(R(-4, -4, rsi14=32.1))


def test_the_cci_rules_use_yesterdays_cci5_and_todays_turn():
    """A dip is CCI(5) below -100 YESTERDAY and back above today, inside a bullish short trend."""
    assert "CciDipInBullishTrend" in matches(R(4, 0, cci5=-50, cci5_prev=-150))
    assert "CciDipInBullishTrend" not in matches(R(4, 0, cci5=-120, cci5_prev=-150)), "not yet back above"
    assert "CciDipInBullishTrend" not in matches(R(2, 0, cci5=-50, cci5_prev=-150)), (
        "trend not bullish enough"
    )
    assert "CciRallyInBearishTrend" in matches(R(-4, 0, cci5=80, cci5_prev=130))
    assert "CciRallyInBearishTrend" not in matches(R(-4, 0, cci5=140, cci5_prev=130)), "still rising"


def test_trend_following_is_a_pullback_against_the_longer_trend():
    assert "BullishTrendFollowing" in matches(R(1, 4, cci14=-40))
    assert "BullishTrendFollowing" not in matches(R(3, 4, cci14=-40)), "no pullback in the short trend"
    assert "BullishTrendFollowing" not in matches(R(1, 1, cci14=-40)), "longer trend not bullish"
    assert "BearishTrendFollowing" in matches(R(-1, -4, cci14=80))
    assert "BearishTrendFollowing" not in matches(R(-1, -4, cci14=40))


def test_quiet_readings_match_nothing():
    assert matches(R()) == []
