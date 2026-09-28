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


def test_the_trend_score_is_the_sum_of_signs_and_the_label_is_five_steps():
    closes = [100.0] * 260 + [150.0]
    assert trend.scores(closes, trend.SHORT_TERM)[-1] == 4
    assert trend.scores(closes, trend.LONG_TERM)[-1] == 4
    assert trend.scores([100.0] * 10, trend.SHORT_TERM) == [None] * 10
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
