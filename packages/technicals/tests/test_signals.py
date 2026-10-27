"""The six scan rules, each pinned at its edge: the current ones, and the rules of 2026-09-29 kept
for re-scoring."""

from __future__ import annotations

import json

from cherrypick.technicals import chart_score, paths, signals
from cherrypick.technicals.signals import FIT_2026_09_29, Readings, matches


def R(
    short=0,
    long=0,
    cci5=0.0,
    cci5_prev=0.0,
    cci14=0.0,
    rsi14=55.0,
    cci50=0.0,
    rsi10=50.0,
    rsi40=50.0,
    close=100.0,
    ema26=100.0,
    sentiment="Neutral",
):
    return Readings(short, long, cci5, cci5_prev, cci14, rsi14, cci50, rsi10, rsi40, close, ema26, sentiment)


# --------------------------------------------------------------------------- the current rules


def test_counter_trend_is_the_mean_of_rsi_10_and_40_stretched():
    assert "BullishCounterTrend" in matches(R(rsi10=30, rsi40=40))  # mean 35
    assert "BullishCounterTrend" not in matches(R(rsi10=30, rsi40=40.2))
    assert "BearishCounterTrend" in matches(R(rsi10=80, rsi40=60))  # mean 70
    assert "BearishCounterTrend" not in matches(R(rsi10=80, rsi40=59.8))
    assert "BullishCounterTrend" in matches(R(short=4, long=4, rsi10=20, rsi40=40)), (
        "the trend scores are not read"
    )


def test_counter_trend_needs_liquid_options_and_nothing_else_does():
    stretched = R(rsi10=20, rsi40=40, cci50=10, close=99, rsi14=40)
    assert "BullishCounterTrend" not in matches(stretched, liquid=False)
    assert "BullishTrendFollowing" in matches(stretched, liquid=False)


def test_trend_following_is_a_pullback_through_the_26_ema_in_the_cci50_trend():
    assert "BullishTrendFollowing" in matches(R(cci50=10, close=99, ema26=100, rsi14=50.5))
    assert "BullishTrendFollowing" not in matches(R(cci50=10, close=99, ema26=100, rsi14=50.6)), (
        "RSI too high"
    )
    assert "BullishTrendFollowing" not in matches(R(cci50=10, close=101, ema26=100, rsi14=45)), "no pullback"
    assert "BullishTrendFollowing" not in matches(R(cci50=-10, close=99, ema26=100, rsi14=45)), "no uptrend"
    assert "BearishTrendFollowing" in matches(R(cci50=-10, close=101, ema26=100, rsi14=50.5))
    assert "BearishTrendFollowing" not in matches(R(cci50=-10, close=101, ema26=100, rsi14=50.4))
    assert "BearishTrendFollowing" not in matches(R(cci50=-10, close=99, ema26=100, rsi14=60))


def test_a_rally_crosses_down_through_100_in_a_bearish_trend_and_sentiment():
    rally = {"short": -2, "sentiment": "Bearish", "cci5_prev": 130}
    assert "CciRallyInBearishTrend" in matches(R(**rally, cci5=100))
    assert "CciRallyInBearishTrend" not in matches(R(**rally, cci5=101)), "still above 100"
    assert "CciRallyInBearishTrend" not in matches(R(**{**rally, "sentiment": "Neutral"}, cci5=80))
    assert "CciRallyInBearishTrend" not in matches(R(**{**rally, "short": -1}, cci5=80))


def test_a_dip_is_yesterdays_cci5_below_minus_100_and_back_above_in_a_bullish_trend():
    assert "CciDipInBullishTrend" in matches(R(3, 0, cci5=-50, cci5_prev=-150))
    assert "CciDipInBullishTrend" not in matches(R(4, 0, cci5=-120, cci5_prev=-150)), "not yet back above"
    assert "CciDipInBullishTrend" not in matches(R(2, 0, cci5=-50, cci5_prev=-150)), (
        "trend not bullish enough"
    )


def test_quiet_readings_match_nothing():
    assert matches(R()) == []
    assert matches(R(), rules=FIT_2026_09_29) == []


def test_liquidity_reads_the_lists_contract_count_and_never_hides_an_unrated_name():
    rows = [
        {"symbol": "BRK/B", "hasOptions": True, "option_contracts": 5000.0},
        {"symbol": "THIN", "hasOptions": True, "option_contracts": 199.0},
        {"symbol": "NONE", "hasOptions": False, "option_contracts": 0.0},
        {"symbol": "VEND", "hasOptions": True, "liquidityRank": 3},  # the vendor's: no count
    ]
    c = signals.option_contracts(rows)
    assert (
        signals.is_liquid("BRK.B", c)
        and not signals.is_liquid("THIN", c)
        and not signals.is_liquid("NONE", c)
    )
    assert signals.is_liquid("VEND", c) and signals.is_liquid("ANY", {}), "unrated is liquid"


# --------------------------------------------------------------------------- the rules of 2026-09-29


def old(r):
    return matches(r, rules=FIT_2026_09_29)


def test_old_counter_trend_needs_the_trends_labelled_strong_and_rsi_stretched():
    assert "BearishCounterTrend" in old(R(3, 3, rsi14=72))
    assert "BearishCounterTrend" not in old(R(4, 4, rsi14=71.9))
    assert "BullishCounterTrend" in old(R(-3, 0, rsi14=32))
    assert "BullishCounterTrend" not in old(R(-4, 1, rsi14=20)), "long trend not bearish"


def test_old_trend_following_is_a_pullback_against_the_longer_trend():
    assert "BullishTrendFollowing" in old(R(1, 4, rsi14=45))
    assert "BullishTrendFollowing" not in old(R(1, 4, rsi14=50.1)), "RSI above the band"
    assert "BearishTrendFollowing" in old(R(-1, -4, cci14=80))
    assert "CciRallyInBearishTrend" in old(R(-2, 0, cci5=80, cci5_prev=130))
    assert "CciRallyInBearishTrend" not in old(R(-4, 0, cci5=140, cci5_prev=130)), "still rising"


# --------------------------------------------------------------------------- the scorer's dating


def _ideas(folder: str, ideas: list[tuple[str, str, str]]) -> None:
    path = paths.market_report_dir() / "vendor-charts" / folder / "trade-ideas.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"symbol": f"{s}.XNYS", "dateOfScan": f"{d}T00:00:00", "rules": [{"ruleMatch": r}]}
        for s, d, r in ideas
    ]
    path.write_text(json.dumps({"tradeIdeas": rows}), encoding="utf-8")


def test_scan_lists_date_each_idea_by_its_scan_not_its_folder():
    _ideas("2026-10-09", [("AAA", "2026-10-09", "BullishCounterTrend")])
    _ideas(
        "2026-10-12",
        [
            ("BBB", "2026-10-12", "BearishTrendFollowing"),
            ("CCC", "2026-10-09", "BullishTrendFollowing"),  # saved a session late
            ("DDD", "2026-10-08", "BullishTrendFollowing"),  # a session no list was saved for
        ],
    )
    got = chart_score.scan_lists()
    assert got == {
        "2026-10-09": {"AAA": {"BullishCounterTrend"}, "CCC": {"BullishTrendFollowing"}},
        "2026-10-12": {"BBB": {"BearishTrendFollowing"}},
    }
