from datetime import date

from cherrypick.scout.analytics import narrative as _narrative


def _bars(closes, *, volumes=None, spread=1.0, start_t=1_700_000_000):
    out = []
    for i, c in enumerate(closes):
        out.append(
            {
                "t": start_t + i * 86400,
                "o": c,
                "h": c + spread,
                "l": c - spread,
                "c": c,
                "v": (volumes[i] if volumes else 100.0),
            }
        )
    return out


def test_sma_cross_today_detects_an_upward_cross():
    closes = [100.0] * 60 + [99.0, 103.0]  # yesterday below the ~100 SMA, today above
    assert _narrative._sma_cross_today(closes, 50) == ("above", _pytest_approx_sma(closes, 50))


def _pytest_approx_sma(closes, period):
    return sum(closes[-period:]) / period


def test_price_action_prefers_the_200_day_cross_over_everything():
    closes = [100.0] * 220 + [99.0, 106.0]  # crosses both the 200- and 50-day SMAs today
    bars = _bars(closes)
    text = _narrative.price_action("TEST", bars, [], "bullish", None, today=date(2027, 1, 5))
    assert "200-day moving average" in text
    assert "crossed above" in text


def test_price_action_reports_a_big_three_session_move():
    closes = [100.0] * 60 + [100.0, 103.0, 106.5]  # +6.5% in 3 sessions, no MA cross today
    bars = _bars(closes)
    text = _narrative.price_action("TEST", bars, [], None, None, today=date(2027, 1, 5))
    assert "6.50% higher" in text


def test_price_action_falls_back_to_trend_plus_levels():
    closes = [100.0] * 60
    bars = _bars(closes, spread=0.0)  # perfectly flat: no crosses, gaps, breaks, moves, bounces
    levels = [
        {"price": 90.0, "kind": "support", "touches": 2},
        {"price": 110.0, "kind": "resistance", "touches": 3},
    ]
    text = _narrative.price_action("TEST", bars, levels, "neutral", None, today=date(2027, 1, 5))
    assert text == "TEST is in a neutral trend with support at 90.00 and resistance at 110.00."


def test_price_action_appends_the_earnings_suffix(monkeypatch):
    closes = [100.0] * 60
    bars = _bars(closes, spread=0.0)
    earnings = {"expected_report_date": "2027-01-06", "time_of_day": "BTO"}
    text = _narrative.price_action("TEST", bars, [], "bullish", earnings, today=date(2027, 1, 5))
    assert "reports earnings tomorrow before the open" in text


def test_level_break_reports_role_reversal():
    closes = [100.0] * 30 + [99.0, 104.0]
    bars = _bars(closes, spread=0.5)
    levels = [{"price": 102.0, "kind": "resistance", "touches": 3}]
    text = _narrative.price_action("TEST", bars, levels, None, None, today=date(2027, 1, 5))
    assert "broke above" in text
    assert "now becomes support" in text


def test_scan_headline_bullish_trend_following():
    result = _narrative.scan_headline("TEST", "mildly_bearish", "bullish")
    assert result is not None
    assert result["scan"] == "Bullish Trend Following"
    assert "pulled back" in result["text"]


def test_scan_headline_bearish_trend_following():
    result = _narrative.scan_headline("TEST", "mildly_bullish", "bearish")
    assert result is not None
    assert result["scan"] == "Bearish Trend Following"


def test_scan_headline_absent_when_no_setup():
    assert _narrative.scan_headline("TEST", "bullish", "bullish") is None
    assert _narrative.scan_headline("TEST", None, None) is None
