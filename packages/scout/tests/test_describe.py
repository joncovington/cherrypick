from datetime import date

import pytest

from cherrypick.scout.analytics import describe as _describe
from cherrypick.scout.analytics.payoff import Leg


def test_annualized_return_matches_the_three_reverse_engineered_reference_pairs():
    """The formula was reverse-engineered from a reference platform's displayed numbers; these
    observed (credit, max_risk, dte) -> (raw, annualized) pairs are the evidence -- including one
    at a different DTE (46d), which a linear-annualization formula could not reproduce. If this
    test breaks, the formula no longer reproduces the observations that justified it."""
    assert _describe.raw_return(150, 900) == pytest.approx(0.1667, abs=1e-4)
    assert _describe.annualized_return(150, 900, 25) == pytest.approx(8.4934, abs=0.02)  # 849.34%
    assert _describe.raw_return(113, 987) == pytest.approx(0.1145, abs=1e-4)
    assert _describe.annualized_return(113, 987, 25) == pytest.approx(3.8675, abs=0.02)  # 386.75%
    # HPE 2026-08-03 (live): $123.50 credit / $3,476.50 risk / 46 DTE -> displayed 31.91%.
    assert _describe.annualized_return(123.50, 3476.50, 46) == pytest.approx(0.3191, abs=0.002)
    # KWEB covered call 2026-08-03: $73.50 credit / $2,789.50 risk / 46 DTE -> displayed 22.93%.
    assert _describe.annualized_return(73.50, 2789.50, 46) == pytest.approx(0.2293, abs=0.002)
    # USO covered call 2026-08-03: $230.00 credit / $11,850.00 risk / 46 DTE -> displayed 16.48%.
    assert _describe.annualized_return(230.00, 11850.00, 46) == pytest.approx(0.1648, abs=0.002)


def test_annualized_return_degrades_on_bad_inputs():
    assert _describe.annualized_return(100, 0, 25) is None
    assert _describe.annualized_return(100, 500, 0) is None
    assert _describe.raw_return(None, 500) is None


def test_projected_yield_12m_matches_the_kweb_covered_call_card():
    """KWEB covered call, 2026-08-03: 22.93% annualized option return + 7.36% trailing dividend
    yield displayed as a 30.29% "12M Projected Yield" -- simple addition, not compounded."""
    assert _describe.projected_yield_12m(0.2293, 0.0736) == pytest.approx(0.3029, abs=1e-4)
    assert _describe.projected_yield_12m(None, 0.0736) is None
    assert _describe.projected_yield_12m(0.2293, None) is None


def test_projected_yield_12m_equals_annualized_when_dividend_is_zero():
    """USO covered call, 2026-08-03: a 0% trailing dividend yield (commodity ETF) makes the
    12M Projected Yield identical to the annualized option return (16.48% == 16.48%) --
    confirms the formula is simple addition, not something that requires a nonzero dividend."""
    assert _describe.projected_yield_12m(0.1648, 0.0) == pytest.approx(0.1648, abs=1e-6)


def test_score_matches_the_apd_same_underlying_vertical_fit():
    """APD, 2026-08-04: three defined-risk verticals on the same underlying/expiration, varying
    only strike width. `score()` == 100*pop*(reward+risk)/risk, the closed form regressed from
    this set plus a second day/underlying's verticals (module docstring, R^2 = 0.9997)."""
    put_vertical = [
        Leg(kind="put", quantity=-1, price=0.0, strike=290.0),
        Leg(kind="put", quantity=1, price=0.0, strike=280.0),
    ]
    call_vertical_narrow = [
        Leg(kind="call", quantity=-1, price=0.0, strike=290.0),
        Leg(kind="call", quantity=1, price=0.0, strike=300.0),
    ]
    call_vertical_wide = [
        Leg(kind="call", quantity=-1, price=0.0, strike=270.0),
        Leg(kind="call", quantity=1, price=0.0, strike=280.0),
    ]
    max_reward = {"value": 310.0, "unbounded": False}
    max_loss = {"value": -690.0, "unbounded": False}
    assert _describe.score(0.5824, put_vertical, max_reward, max_loss) == pytest.approx(84, abs=1)

    max_reward = {"value": 480.0, "unbounded": False}
    max_loss = {"value": -520.0, "unbounded": False}
    assert _describe.score(0.5303, call_vertical_narrow, max_reward, max_loss) == pytest.approx(102, abs=1)

    max_reward = {"value": 795.0, "unbounded": False}
    max_loss = {"value": -205.0, "unbounded": False}
    assert _describe.score(0.2949, call_vertical_wide, max_reward, max_loss) == pytest.approx(144, abs=1)


def test_score_matches_the_avgo_iron_condors_and_verticals():
    """AVGO, 2026-08-05: three put verticals of increasing width plus two iron condors of very
    different wingspan (POP 12%-48%), all landing within 0.5 points of the closed form -- the
    same formula generalizes past 2-leg verticals to a 4-leg iron condor."""
    put_vertical_390_300 = [
        Leg(kind="put", quantity=1, price=0.0, strike=390.0),
        Leg(kind="put", quantity=-1, price=0.0, strike=300.0),
    ]
    max_reward = {"value": 6078.0, "unbounded": False}
    max_loss = {"value": -2922.0, "unbounded": False}
    assert _describe.score(0.4243, put_vertical_390_300, max_reward, max_loss) == pytest.approx(131, abs=1)

    iron_condor_narrow = [
        Leg(kind="put", quantity=1, price=0.0, strike=370.0),
        Leg(kind="put", quantity=-1, price=0.0, strike=380.0),
        Leg(kind="call", quantity=-1, price=0.0, strike=390.0),
        Leg(kind="call", quantity=1, price=0.0, strike=400.0),
    ]
    max_reward = {"value": 905.0, "unbounded": False}
    max_loss = {"value": -95.0, "unbounded": False}
    assert _describe.score(0.1223, iron_condor_narrow, max_reward, max_loss) == pytest.approx(129, abs=1)

    iron_condor_wide = [
        Leg(kind="put", quantity=1, price=0.0, strike=340.0),
        Leg(kind="put", quantity=-1, price=0.0, strike=380.0),
        Leg(kind="call", quantity=-1, price=0.0, strike=390.0),
        Leg(kind="call", quantity=1, price=0.0, strike=430.0),
    ]
    max_reward = {"value": 3083.0, "unbounded": False}
    max_loss = {"value": -917.0, "unbounded": False}
    assert _describe.score(0.3062, iron_condor_wide, max_reward, max_loss) == pytest.approx(134, abs=1)


def test_score_is_none_for_a_naked_long_option():
    """The one shape the fit is known to fail on (module docstring): a single long option's
    theoretical max reward (near stock-to-zero) makes reward/risk huge and the formula unusable."""
    long_put = [Leg(kind="put", quantity=1, price=10.5, strike=95.0)]
    max_reward = {"value": 11050.0, "unbounded": False}
    max_loss = {"value": -1050.0, "unbounded": False}
    assert _describe.score(0.3878, long_put, max_reward, max_loss) is None


def test_score_is_none_for_unbounded_risk():
    """Short straddle/strangle scored much higher than reward/risk (collapsing to ~0 when risk is
    "Unlimited") would predict -- the real denominator is a margin figure this package can't see."""
    straddle = [
        Leg(kind="call", quantity=-1, price=13.30, strike=290.0),
        Leg(kind="put", quantity=-1, price=9.25, strike=290.0),
    ]
    max_reward = {"value": 2255.0, "unbounded": False}
    max_loss = {"value": None, "unbounded": True}
    assert _describe.score(0.5809, straddle, max_reward, max_loss) is None


def test_score_uses_probable_risk_as_an_estimated_denominator_for_undefined_risk():
    """When the caller supplies `probable_risk` (intended to be probable_risk_2sd's own output),
    score() extends the SAME formula shape to an undefined-risk basket rather than returning
    None -- scout's own internally-consistent estimate, not a claim to replicate the reference
    platform's (unresolved) undefined-risk number."""
    strangle = [
        Leg(kind="put", quantity=-1, price=15.20, strike=360.0),
        Leg(kind="call", quantity=-1, price=18.95, strike=385.0),
    ]
    max_reward = {"value": 3415.0, "unbounded": False}
    max_loss = {"value": None, "unbounded": True}
    result = _describe.score(0.6364, strangle, max_reward, max_loss, probable_risk=6923.96)
    expected = 100 * 0.6364 * (3415.0 + 6923.96) / 6923.96
    assert result == pytest.approx(expected)

    # probable_risk<=0 or missing still degrades to None rather than dividing by zero/nothing.
    assert _describe.score(0.6364, strangle, max_reward, max_loss, probable_risk=0.0) is None
    assert _describe.score(0.6364, strangle, max_reward, max_loss, probable_risk=None) is None


def test_probable_risk_2sd_matches_the_googl_strangle_ballpark():
    """GOOG short strangle 360/385, 2026-08-05: reference platform's own disclosed methodology
    ("probable risk based on a wide (2 SD) move against you"). Solving IV from the strangle's own
    quoted premium (put $15.20 + call $18.95, spot 370.91, 74 DTE) and evaluating this position's
    P&L at spot -/+ 2 SD lands at ~$6,924 -- in the same neighborhood as the reference platform's
    own contract-sizing behavior for this exact position ($30k/$10k "invest" amounts sized to 3
    and 1 contracts, implying ~$7,500-$10,000/contract; the gap is plausibly real IV skew this
    flat-vol solve doesn't model). This is NOT the same number Score implies for this position
    (~$1,200) -- see module docstring for why those are ruled out as the same calculation."""
    legs = [
        Leg(kind="put", quantity=-1, price=15.20, strike=360.0),
        Leg(kind="call", quantity=-1, price=18.95, strike=385.0),
    ]
    risk = _describe.probable_risk_2sd(legs, spot=370.91, sigma=0.3422, t=74 / 365)
    assert risk == pytest.approx(6923.96, abs=1.0)


def test_probable_risk_2sd_is_zero_when_the_2sd_move_would_still_profit():
    legs = [Leg(kind="put", quantity=-1, price=1.0, strike=50.0)]
    risk = _describe.probable_risk_2sd(legs, spot=100.0, sigma=0.2, t=30 / 365)
    assert risk == 0.0


def test_probable_risk_2sd_degrades_on_missing_inputs():
    legs = [Leg(kind="put", quantity=-1, price=1.0, strike=95.0)]
    assert _describe.probable_risk_2sd(legs, 100.0, None, 30 / 365) is None
    assert _describe.probable_risk_2sd(legs, 100.0, 0.3, 0.0) is None


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
    # HPE (live, 2026-08-03): POW 75.69% green -- pass boundary now bounded in (65.66, 75.69];
    # annualized 31.91% green; earnings Sep 3 inside the Sep 18 expiry warns; 48%-of-mid spread red.
    items = _describe.checklist(pow_value=0.7569, annualized=0.3191, earnings_inside=True, spread_pct=0.48)
    assert [i["status"] for i in items] == ["pass", "pass", "warn", "fail"]


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


def test_has_weekly_cadence_requires_a_real_weekly_gap():
    weeklies = ["2026-08-28", "2026-09-04", "2026-09-18", "2026-10-16"]
    monthlies_only = ["2026-08-21", "2026-09-18", "2026-10-16", "2027-01-15"]
    assert _describe.has_weekly_cadence(weeklies) is True
    assert _describe.has_weekly_cadence(monthlies_only) is False
    assert _describe.has_weekly_cadence([]) is False
    assert _describe.has_weekly_cadence(["garbage"]) is False


def test_tight_spread_without_weeklies_caps_at_warn():
    """User rule: high liquidity must always have weekly expirations available -- a tight spread
    on a monthly-only chain must not grade as high liquidity."""
    tight = _describe.checklist(0.80, 0.10, False, spread_pct=0.02, has_weeklies=False)
    assert {i["name"]: i["status"] for i in tight}["Spread & liquidity"] == "warn"
    tight_weekly = _describe.checklist(0.80, 0.10, False, spread_pct=0.02, has_weeklies=True)
    assert {i["name"]: i["status"] for i in tight_weekly}["Spread & liquidity"] == "pass"
    # Cadence unknown (pure-function default): the spread grade stands alone.
    tight_unknown = _describe.checklist(0.80, 0.10, False, spread_pct=0.02)
    assert {i["name"]: i["status"] for i in tight_unknown}["Spread & liquidity"] == "pass"
    # A wide spread stays failed regardless of cadence.
    wide = _describe.checklist_directional("bullish", None, None, False, 0.30, True)
    assert {i["name"]: i["status"] for i in wide}["Spread & liquidity"] == "fail"


def test_checklist_unknowns_warn_rather_than_pass():
    items = _describe.checklist(pow_value=None, annualized=None, earnings_inside=None, spread_pct=None)
    assert all(i["status"] == "warn" for i in items)
    items = _describe.checklist(pow_value=0.45, annualized=0.01, earnings_inside=True, spread_pct=0.30)
    assert [i["status"] for i in items] == ["fail", "fail", "warn", "fail"]
