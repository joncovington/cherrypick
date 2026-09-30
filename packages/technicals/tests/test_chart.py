"""The indicators, the trend baseline and its scorer against a capture."""

from __future__ import annotations

import json
import math

from cherrypick.technicals import chart_score, indicators, paths, store, trend


def test_sma_and_ema_are_undefined_until_the_window_is_full():
    assert indicators.sma([1, 2, 3, 4], 3) == [None, None, 2.0, 3.0]
    e = indicators.ema([1.0, 2.0, 3.0, 4.0], 3)
    assert e[:2] == [None, None] and e[2] == 2.0 and math.isclose(e[3], 0.5 * 4 + 0.5 * 2.0)


def test_rsi_is_100_on_only_gains_and_50_on_equal_gains_and_losses():
    assert indicators.rsi([float(i) for i in range(20)], 14)[-1] == 100.0
    alt = [10.0 + (i % 2) for i in range(40)]
    last_two = indicators.rsi(alt, 14)[-2:]
    # Equal gains and losses oscillate around 50: just below after a down day, just above after an up.
    assert last_two[0] < 50.0 < last_two[1] and math.isclose(sum(last_two) / 2, 50.0, abs_tol=0.5)
    assert indicators.rsi([1.0] * 10, 14) == [None] * 10


def test_cci_is_zero_on_a_flat_series_and_positive_above_its_mean():
    flat = [10.0] * 20
    assert indicators.cci(flat, flat, flat, 14)[-1] == 0.0
    rising = [10.0] * 19 + [12.0]
    assert indicators.cci(rising, rising, rising, 14)[-1] > 100


def test_the_trend_score_is_the_vendors_and_the_label_is_five_steps():
    closes = [100.0] * 260 + [150.0]
    assert trend.scores(closes, trend.SHORT_TERM)[-1] == 4
    assert trend.scores(closes, trend.LONG_TERM)[-1] == 4
    assert [trend.label(v) for v in (4, 3, 2, 1, 0, -1, -2, -3, -4)] == [
        "Bullish",
        "Bullish",
        "Mildly Bullish",
        "Mildly Bullish",
        "Neutral",
        "Mildly Bearish",
        "Mildly Bearish",
        "Bearish",
        "Bearish",
    ]


def test_the_scores_start_where_the_vendors_do():
    """The vendor's short history starts on the 50th bar and its long on the 200th, on every name."""
    short = trend.scores([100.0] * 60, trend.SHORT_TERM)
    assert short[48] is None and short[49] is not None
    long_ = trend.scores([100.0] * 210, trend.LONG_TERM)
    assert long_[198] is None and long_[199] is not None


SMALL = trend.TrendSpec(2, 4)  # short and long small enough to work by hand; the band stays 20


def test_an_odd_score_which_a_sum_of_signs_could_never_give():
    """Last four 100, 100, 90, 110: SMA2 100, SMA4 100, WMA4 (100+200+270+440)/10 = 101. Close 110 is
    above all three and SMA2 is not above SMA4: 2 + 2 + 0 + 2 - 3 = 3."""
    assert trend.scores([100.0] * 21 + [90.0, 110.0], SMALL)[-1] == 3


def test_the_weighted_average_alone_moves_a_mixed_score_by_two():
    """Last four 100, 120, 90, X. X = 101: SMA2 95.5, SMA4 102.75, WMA4 101.4 -> above the short,
    below the long, below the WMA: -1. X = 102: WMA4 101.8, now above it, nothing else changes: +1."""
    assert trend.scores([100.0] * 21 + [120.0, 90.0, 101.0], SMALL)[-1] == -1
    assert trend.scores([100.0] * 21 + [120.0, 90.0, 102.0], SMALL)[-1] == 1


def test_minus_four_is_a_close_below_both_averages_and_the_lower_band():
    """Twelve 90/110 pairs, then the last close. Its last four are 110, 90, 110, X. X = 85: SMA2 97.5,
    SMA4 98.75, WMA4 96 -- below all three, SMA2 below SMA4, so the terms give -3. The 20-bar band
    (ten 110s, nine 90s and 85) has mean 99.75 and population sd 10.3, lower 79.1: 85 is above it,
    so -3 stands. X = 75 is under the band: -4."""
    swinging = [90.0, 110.0] * 12
    assert trend.scores(swinging + [85.0], SMALL)[-1] == -3
    assert trend.scores(swinging + [75.0], SMALL)[-1] == -4


def test_score_trends_compares_day_by_day_against_the_capture():
    conn = store.connect()
    days = [f"2026-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 29)][:300]
    store.upsert_bars(conn, [("TEST", day, 1, 1, 1, 100.0 + i, 1) for i, day in enumerate(days)])
    conn.commit()
    conn.close()
    capture = {
        "why": {
            "syrahSentimentShortTerm": [
                {"date": days[-1], "value": 4.0},  # ours is 4: exact
                {"date": days[-2], "value": 3.0},  # ours is 4: same label, within one
                {"date": days[-3], "value": -4.0},  # ours is 4: a miss
            ],
            "syrahSentimentLongTerm": [],
        }
    }
    folder = paths.market_report_dir() / "vendor-charts" / "2026-09-25"
    folder.mkdir(parents=True)
    (folder / "TEST.json").write_text(json.dumps(capture), encoding="utf-8")
    result = chart_score.score_trends()
    assert result["by_symbol"]["TEST"]["short"] == {"days": 3, "exact": 1, "label": 2, "within1": 2}


def test_rsi_matches_wilders_smoothing_worked_by_hand():
    """n=2 over 1,2,1,2,3: gains 1,0,1,1 and losses 0,1,0,0. Seed 0.5/0.5 -> 50; then Wilder's
    (prev x (n-1) + today) / n: 0.75/0.25 -> 75; 0.875/0.125 -> 87.5."""
    assert indicators.rsi([1.0, 2.0, 1.0, 2.0, 3.0], 2) == [None, None, 50.0, 75.0, 87.5]


def test_sentiment_is_the_close_against_the_sma50_and_the_wma200():
    """150 sessions at 120, 50 at 100, then the last close. At 105: SMA 50 = (49 x 100 + 105) / 50 =
    100.1 and the WMA 200 still leans on the 120s (111.17) -- above one, below the other: Neutral.
    At 125 above both: Bullish. At 95 below both: Bearish."""
    base = [120.0] * 150 + [100.0] * 50
    assert trend.sentiment(base + [105.0]) == "Neutral"
    assert trend.sentiment(base + [125.0]) == "Bullish"
    assert trend.sentiment(base + [95.0]) == "Bearish"
    # It is the WEIGHTED 200: at 113 the close is above the WMA 200 (111.25) but below the plain
    # SMA 200 (114.97), so an SMA would call it Neutral; the vendor's rule says Bullish.
    assert trend.sentiment(base + [113.0]) == "Bullish"
    assert trend.sentiment([100.0] * 199) is None  # the WMA 200 is not defined yet
