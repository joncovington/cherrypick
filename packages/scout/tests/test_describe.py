from datetime import date

import pytest

from cherrypick.scout.analytics import describe as _describe
from cherrypick.scout.analytics.payoff import Leg


def test_annualized_return_matches_the_two_reverse_engineered_reference_pairs():
    """The formula was reverse-engineered from a reference platform's displayed numbers; these two
    observed (credit, max_risk, dte) -> (raw, annualized) pairs are the evidence. If this test
    breaks, the formula no longer reproduces the observations that justified it."""
    assert _describe.raw_return(150, 900) == pytest.approx(0.1667, abs=1e-4)
    assert _describe.annualized_return(150, 900, 25) == pytest.approx(8.4934, abs=0.02)  # 849.34%
    assert _describe.raw_return(113, 987) == pytest.approx(0.1145, abs=1e-4)
    assert _describe.annualized_return(113, 987, 25) == pytest.approx(3.8675, abs=0.02)  # 386.75%


def test_annualized_return_degrades_on_bad_inputs():
    assert _describe.annualized_return(100, 0, 25) is None
    assert _describe.annualized_return(100, 500, 0) is None
    assert _describe.raw_return(None, 500) is None


_SHORT_PUT = [Leg(kind="put", quantity=-1, price=1.5, strike=95.0)]


def test_prob_worthless_short_put_is_prob_above_strike():
    pow_ = _describe.prob_worthless(_SHORT_PUT, spot=100.0, sigma=0.3, t=25 / 365, r=0.05)
    assert pow_ is not None
    assert 0.5 < pow_ < 1.0  # OTM short put: worthless is the likely outcome
    # No short option -> the metric doesn't apply.
    long_call = [Leg(kind="call", quantity=1, price=2.0, strike=105.0)]
    assert _describe.prob_worthless(long_call, 100.0, 0.3, 25 / 365, 0.05) is None


def test_prob_worthless_strangle_is_an_interval_probability():
    legs = [
        Leg(kind="put", quantity=-1, price=1.0, strike=90.0),
        Leg(kind="call", quantity=-1, price=1.0, strike=110.0),
    ]
    pow_ = _describe.prob_worthless(legs, 100.0, 0.3, 25 / 365, 0.05)
    solo_put = _describe.prob_worthless(legs[:1], 100.0, 0.3, 25 / 365, 0.05)
    assert pow_ < solo_put  # adding a short call can only shrink the worthless region


def test_bs_greeks_short_put_signs():
    greeks = _describe.bs_greeks(_SHORT_PUT, spot=100.0, sigma=0.3, t=25 / 365, r=0.05)
    assert greeks["delta"] > 0  # short put is long delta
    assert greeks["gamma"] < 0  # short option is short gamma
    assert greeks["theta"] > 0  # collects decay
    assert greeks["vega"] < 0  # hurt by rising IV


def test_strategy_explanation_short_put():
    text = _describe.strategy_explanation(_SHORT_PUT, 100.0, 0.6, date(2026, 8, 28))
    assert "bullish strategy" in text
    assert "limited risk of $9,350.00" in text  # (95 - 1.5) * 100
    assert "limited potential reward of $150.00" in text
    assert "closes above $93.50 by 2026-08-28" in text
    assert "60.0% model probability" in text


def test_strategy_explanation_iron_condor_is_between():
    legs = [
        Leg(kind="put", quantity=-1, price=2.0, strike=95.0),
        Leg(kind="put", quantity=1, price=1.0, strike=90.0),
        Leg(kind="call", quantity=-1, price=2.0, strike=105.0),
        Leg(kind="call", quantity=1, price=1.0, strike=110.0),
    ]
    text = _describe.strategy_explanation(legs, 100.0, None, None)
    assert "neutral strategy" in text
    assert "between $93.00 and $107.00" in text


def test_greeks_explanation_reads_the_numbers_aloud():
    text = _describe.greeks_explanation("ON", {"delta": 43.82, "theta": 13.79, "vega": -8.41})
    assert "For every $1 ON rises, this position makes about $43.82" in text
    assert "adds about $13.79 per day" in text
    assert "rise costs about $8.41" in text
    assert "Model greeks" in text


def test_short_put_suggestion_discount_math():
    text = _describe.short_put_suggestion("U", 31.0, date(2026, 8, 28), 264.0, spot=31.71)
    assert "$31.00 put on U" in text
    assert "net price of $28.36" in text
    assert "10.6% discount" in text  # (31.71 - 28.36) / 31.71


def test_checklist_reproduces_the_observed_reference_gradings():
    """Five observed cards calibrated these thresholds (see the module docstring); each row here is
    one of those observations."""
    # HYG-like: POW 81.39% green, annualized 6.30% green -- but a ~100%-of-mid spread red.
    items = _describe.checklist(pow_value=0.8139, annualized=0.063, earnings_inside=False, spread_pct=1.0)
    assert [i["status"] for i in items] == ["pass", "pass", "pass", "fail"]
    # SAP-like: POW 53.54% red ("very low"), 20% spread red.
    items = _describe.checklist(pow_value=0.5354, annualized=0.7845, earnings_inside=False, spread_pct=0.20)
    assert [i["status"] for i in items] == ["fail", "pass", "pass", "fail"]
    # DIA-like: POW 58.28% yellow ("lower than optimal"), 7.5% spread yellow ("sizable").
    items = _describe.checklist(pow_value=0.5828, annualized=0.161, earnings_inside=False, spread_pct=0.075)
    assert [i["status"] for i in items] == ["warn", "pass", "pass", "warn"]
    # SPY-like covered call: POW 65.66% still yellow, 1% spread green.
    items = _describe.checklist(pow_value=0.6566, annualized=0.0865, earnings_inside=False, spread_pct=0.0103)
    assert [i["status"] for i in items] == ["warn", "pass", "pass", "pass"]


def test_checklist_directional_replays_the_observed_spread_cards():
    """Fixtures from four observed reference credit-spread cards (2026-08-03)."""
    # CSX: bullish put vertical, stock 1M Bullish, SPX 1M bullish, no earnings, combo spread huge.
    items = _describe.checklist_directional("bullish", "bullish", "bullish", False, 2.0)
    assert [i["status"] for i in items] == ["pass", "pass", "pass", "fail"]
    # SHOP: bullish put vertical against a Mildly Bearish 1M stock trend; earnings Aug 5 inside.
    items = _describe.checklist_directional("bullish", "mildly_bearish", "bullish", True, 0.90)
    assert [i["status"] for i in items] == ["fail", "pass", "warn", "fail"]
    # TEL: bearish call vertical against Mildly Bullish stock AND bullish market; no earnings.
    items = _describe.checklist_directional("bearish", "mildly_bullish", "bullish", False, 0.37)
    assert [i["status"] for i in items] == ["fail", "fail", "pass", "fail"]
    # DIS: bearish vertical with Bearish 1M stock trend, against the bullish market.
    items = _describe.checklist_directional("bearish", "bearish", "bullish", None, None)
    assert [i["status"] for i in items] == ["pass", "fail", "warn", "warn"]


def test_direction_classifies_an_otm_put_spread_as_bullish():
    """Regression for the live-caught probe bug: an OTM put credit spread's max-profit plateau
    covers +/-10% of spot, but the position is directionally bullish -- probes must reach the
    tails."""
    legs = [
        Leg(kind="put", quantity=-1, price=2.0, strike=47.0),
        Leg(kind="put", quantity=1, price=1.0, strike=42.0),
    ]
    assert _describe.direction(legs, spot=52.39) == "bullish"
    call_vertical = [
        Leg(kind="call", quantity=-1, price=2.0, strike=97.0),
        Leg(kind="call", quantity=1, price=1.0, strike=104.0),
    ]
    assert _describe.direction(call_vertical, spot=96.19) == "bearish"


def test_checklist_directional_neutral_trend_warns():
    items = _describe.checklist_directional("bullish", "neutral", None, False, 0.02)
    assert [i["status"] for i in items] == ["warn", "warn", "pass", "pass"]


def test_checklist_unknowns_warn_rather_than_pass():
    items = _describe.checklist(pow_value=None, annualized=None, earnings_inside=None, spread_pct=None)
    assert all(i["status"] == "warn" for i in items)
    items = _describe.checklist(pow_value=0.45, annualized=0.01, earnings_inside=True, spread_pct=0.30)
    assert [i["status"] for i in items] == ["fail", "fail", "warn", "fail"]
