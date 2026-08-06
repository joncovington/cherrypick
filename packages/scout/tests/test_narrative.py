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


# --------------------------------------------------------------------------- secondary detectors


def test_cci_flags_a_sharp_dip_and_headline_prefers_it():
    closes = [100.0 + i * 0.5 for i in range(40)] + [112.0, 108.0, 104.0]
    bars = _bars(closes, spread=0.5)
    value = _narrative.cci(bars)
    assert value is not None and value < -100
    headline = _narrative.scan_headline("TEST", "mildly_bearish", "bullish", bars)
    assert headline["scan"] == "CCI Dip in Bullish Trend"


def test_golden_cross_detected_only_on_the_crossing_day():
    # 200 low bars, then a strong rally long enough to drag the 50-day over the 200-day: assert the
    # cross fires on exactly one day in the window.
    closes = [100.0] * 210 + [100.0 + i for i in range(1, 80)]
    fired = []
    for end in range(201, len(closes) + 1):
        if _narrative._golden_death_cross_today(closes[:end]) == "golden":
            fired.append(end)
    assert len(fired) == 1


def test_week52_new_high_and_proximity():
    closes = [100.0] * 200 + [120.0]
    assert "new 52-week closing high" in _narrative._week52(closes)
    closes = [100.0] * 100 + [120.0] + [100.0] * 50 + [118.0]
    assert "of its 52-week high" in _narrative._week52(closes)


def test_streak_detection():
    closes = [100.0] * 30 + [101, 102, 103, 104, 105, 106]
    assert "closed higher 6 sessions in a row" in _narrative._streak([float(c) for c in closes])
    assert _narrative._streak([100.0, 101.0, 100.5, 101.5, 100.8, 102.0, 101.0]) is None


def test_options_bullet_prefers_iv_vs_realized():
    info = {"iv_30d": 0.60, "hv_30d": 0.40, "iv_rank": "0.9"}
    text = _narrative.options_bullet("TEST", info)
    assert "1.5x realized" in text
    # without HV, falls to IV rank
    text = _narrative.options_bullet("TEST", {"iv_30d": 0.60, "iv_rank": "0.9"})
    assert "IV rank is 90/100" in text
    assert _narrative.options_bullet("TEST", {}) is None


def test_relative_strength_bullet_needs_a_real_gap():
    sym = [100.0] * 64 + [130.0]  # +30%
    bench = [100.0] * 64 + [105.0]  # +5%
    text = _narrative.relative_strength_bullet("TEST", sym * 1, bench)
    assert "outperformed the S&P 500 by 25%" in text
    assert _narrative.relative_strength_bullet("TEST", bench, bench) is None
    assert _narrative.relative_strength_bullet("TEST", sym, None) is None


def test_event_warnings_earnings_and_ex_div_inside_expiration():
    exp = date(2027, 2, 19)
    today = date(2027, 1, 5)
    earnings = {"expected_report_date": "2027-02-01"}
    info = {"dividend_ex_date": "2027-02-10", "dividend_rate_per_share": 0.85}
    warnings = _narrative.event_warnings(exp, earnings, info, today=today)
    assert len(warnings) == 2
    assert "earnings report (2027-02-01)" in warnings[0]
    assert "ex-dividend 2027-02-10 ($0.85/share)" in warnings[1]


def test_event_warnings_outside_expiration_are_silent():
    exp = date(2027, 1, 15)
    today = date(2027, 1, 5)
    earnings = {"expected_report_date": "2027-02-01"}  # after expiration
    info = {"dividend_ex_date": "2026-12-10"}  # in the past
    assert _narrative.event_warnings(exp, earnings, info, today=today) == []
