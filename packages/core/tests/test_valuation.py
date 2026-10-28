"""The DCF (cherrypick.core.valuation): the arithmetic to the cent, the identities, the refusals."""

from __future__ import annotations

import pytest

from cherrypick.core import valuation as v

# A company whose history is flat, so every median is the year itself and the arithmetic can be
# checked by hand: revenue 1,000, EBIT 100, D&A 50, capex 50, working capital absorbing 10, tax 25%.
# Money in millions, shares in millions, so the answer is dollars a share.


def _year(end: str, revenue: float = 1000.0, ebit: float = 100.0, **over) -> v.FiscalYear:
    fields = dict(
        end=end,
        revenue=revenue,
        ebit=ebit,
        da=50.0,
        capex=50.0,
        nwc_change=-10.0,
        debt=200.0,
        cash=100.0,
        shares=100.0,
        pretax=90.0,
        taxes=22.5,
        interest=10.0,
    )
    fields.update(over)
    return v.FiscalYear(**fields)


FLAT = [_year(f"20{y}-12-31") for y in (21, 22, 23, 24, 25)]


def test_drivers_from_a_flat_history_are_the_history():
    d = v.drivers_from_history(FLAT)
    assert isinstance(d, v.Drivers)
    assert d.ebit_margin == pytest.approx(0.10)
    assert d.da_pct == pytest.approx(0.05)
    assert d.capex_pct == pytest.approx(0.05)
    assert d.nwc_pct == pytest.approx(0.01)
    assert d.tax_rate == pytest.approx(0.25)
    # No growth in the history: year one is 0% and fades up to the terminal 2% by year five.
    assert d.growth == pytest.approx((0.0, 0.005, 0.01, 0.015, 0.02))


def test_consensus_growth_leads_the_fade_and_is_clamped():
    d = v.drivers_from_history(FLAT, consensus_growth=0.60)
    assert d.growth[0] == pytest.approx(v.GROWTH_BOUNDS[1])
    assert d.growth[-1] == pytest.approx(v.TERMINAL_GROWTH)
    d = v.drivers_from_history(FLAT, consensus_growth=0.10)
    assert d.growth == pytest.approx((0.10, 0.08, 0.06, 0.04, 0.02))


def test_the_medians_ignore_one_bad_year():
    years = FLAT[:-1] + [_year("2025-12-31", ebit=-400.0, pretax=-410.0, taxes=0.0)]
    d = v.drivers_from_history(years)
    assert d.ebit_margin == pytest.approx(0.10)
    assert d.tax_rate == pytest.approx(0.25)


def test_the_margin_window_is_the_newest_three_years_and_the_growth_window_all_five():
    # Two fat years, then three at the going margin: the forecast runs on the three. Revenue doubles
    # over the five years, so the CAGR is the full window's.
    years = [
        _year("2021-12-31", revenue=500.0, ebit=150.0),
        _year("2022-12-31", revenue=600.0, ebit=180.0),
        _year("2023-12-31", revenue=800.0, ebit=80.0),
        _year("2024-12-31", revenue=900.0, ebit=90.0),
        _year("2025-12-31", revenue=1000.0, ebit=100.0),
    ]
    d = v.drivers_from_history(years)
    assert d.ebit_margin == pytest.approx(0.10)
    assert d.growth[0] == pytest.approx(v._clamp((1000.0 / 500.0) ** 0.25 - 1, v.GROWTH_BOUNDS))
    assert len(years[-v.MARGIN_YEARS :]) == 3


def test_working_capital_reads_five_years_so_two_one_offs_in_three_do_not_set_it():
    # KO's shape: three ordinary years, then two that absorbed 15% of revenue (a tax deposit, a
    # settlement). The three-year median would charge 15% forever; the five-year one charges 1%.
    years = FLAT[:3] + [_year("2024-12-31", nwc_change=-150.0), _year("2025-12-31", nwc_change=-150.0)]
    d = v.drivers_from_history(years)
    assert v.NWC_YEARS == v.FORECAST_YEARS > v.MARGIN_YEARS
    assert d.nwc_pct == pytest.approx(0.01)


def test_a_cost_of_capital_under_the_floor_is_floored_and_flagged():
    # A floored beta at a 4% rate: ke 6.5%, kd 3.75% after tax, 80/20 -> 5.95%, under the 7% floor.
    out = v.value(FLAT, market_cap=800.0, beta_raw=0.1)
    assert out.wacc == pytest.approx(v.MIN_WACC)
    assert "wacc_floored" in out.flags and "beta_bounded" in out.flags
    assert "wacc_floored" not in v.value(FLAT, market_cap=800.0, beta_raw=1.0).flags


def test_the_value_to_the_cent_against_a_hand_computation():
    # beta 1.0 at a 4% rate and 5% premium: ke 9%; kd 10/200 = 5% pre-tax, 3.75% after; market cap 800
    # against debt 200: WACC = 0.09 x 0.8 + 0.0375 x 0.2 = 0.0795.
    out = v.value(FLAT, market_cap=800.0, beta_raw=1.0)
    assert isinstance(out, v.Valuation)
    assert out.wacc == pytest.approx(0.0795)
    growth = (0.0, 0.005, 0.01, 0.015, 0.02)
    revenue, flows = 1000.0, []
    for g in growth:
        revenue *= 1 + g
        flows.append(revenue * 0.10 * 0.75 + revenue * 0.05 - revenue * 0.05 - revenue * 0.01)
    pv = sum(f / 1.0795**t for t, f in enumerate(flows, 1))
    tv = flows[-1] * 1.02 / (0.0795 - 0.02)
    ev = pv + tv / 1.0795**5
    assert out.ev == pytest.approx(ev)
    assert out.fair_value == pytest.approx((ev - 200 + 100) / 100, abs=0.005)
    assert out.terminal_share == pytest.approx(tv / 1.0795**5 / ev)
    assert out.implied_ev_ebitda == pytest.approx(ev / 150)
    assert out.low <= out.fair_value <= out.high


def test_constant_growth_reduces_to_gordon():
    g, rate = 0.02, 0.08
    d = v.Drivers((g,) * 5, 0.10, 0.05, 0.05, 0.0, 0.25, g)
    proj = v.project(FLAT[-1], d)
    fcf1 = 1000 * (1 + g) * 0.10 * 0.75
    tv = v.terminal_value(proj[-1].fcf, rate, g)
    ev = v.present_value([p.fcf for p in proj], rate) + tv / (1 + rate) ** 5
    assert ev == pytest.approx(fcf1 / (rate - g))


def test_the_grid_is_monotone_in_the_rate_and_in_growth():
    d = v.drivers_from_history(FLAT)
    proj = v.project(FLAT[-1], d)
    grid = v.sensitivity(proj, 0.0795, 0.02, FLAT[-1])
    for dg in v.GROWTH_STEPS:
        column = [grid[(dw, dg)] for dw in v.WACC_STEPS]
        assert column == sorted(column, reverse=True)
    for dw in v.WACC_STEPS:
        row = [grid[(dw, dg)] for dg in v.GROWTH_STEPS]
        assert row == sorted(row)
    assert grid[(0.0, 0.0)] == pytest.approx(v.value(FLAT, 800.0, 1.0).fair_value)


@pytest.mark.parametrize(
    "years, reason",
    [
        (FLAT[:2], "too_few_years"),
        ([_year(y.end, ebit=-5.0) for y in FLAT], "no_operating_profit"),
        ([_year(y.end, revenue=0.0) for y in FLAT], "no_revenue"),
    ],
)
def test_a_history_the_model_cannot_read_is_refused(years, reason):
    assert v.drivers_from_history(years) == v.Refusal(reason)
    assert v.value(years, 800.0, 1.0) == v.Refusal(reason)


def test_no_beta_and_no_shares_are_refused():
    assert v.value(FLAT, 800.0, None) == v.Refusal("no_beta")
    assert v.value(FLAT, 800.0, float("nan")) == v.Refusal("no_beta")
    assert v.value([_year(y.end, shares=0.0) for y in FLAT], 800.0, 1.0) == v.Refusal("no_shares")


def test_a_cash_flow_that_never_turns_positive_is_refused():
    heavy = [_year(y.end, capex=200.0) for y in FLAT]  # capex 20% of revenue against a 10% margin
    assert v.value(heavy, 800.0, 1.0) == v.Refusal("negative_terminal_fcf")


def test_a_rate_too_close_to_growth_is_refused_and_the_guard_is_what_refuses(monkeypatch):
    # The rate floors at 7%; a terminal growth of 6.5% leaves half a point, under the one-point spread.
    out = v.value(FLAT, market_cap=800.0, beta_raw=0.5, rf=0.02, erp=0.0, terminal_growth=0.065)
    assert out == v.Refusal("wacc_near_growth")
    # Break the guard on purpose: with no minimum spread the same inputs produce a number.
    monkeypatch.setattr(v, "MIN_SPREAD", -1.0)
    broken = v.value(FLAT, market_cap=800.0, beta_raw=0.5, rf=0.02, erp=0.0, terminal_growth=0.065)
    assert not isinstance(broken, v.Refusal) or broken.reason != "wacc_near_growth"


def test_a_terminal_heavy_answer_is_flagged():
    fast = v.value(FLAT, market_cap=800.0, beta_raw=1.0, consensus_growth=0.25)
    slow = v.value(FLAT, market_cap=800.0, beta_raw=1.0, consensus_growth=-0.10)
    assert fast.terminal_share > slow.terminal_share
    assert ("terminal_heavy" in fast.flags) == (fast.terminal_share > v.TERMINAL_HEAVY)


def test_beta_is_the_regression_slope_and_bounded():
    index = [0.01, -0.02, 0.015, 0.03, -0.01, 0.005]
    assert v.beta(index, index) == pytest.approx(1.0)
    assert v.beta([2 * r for r in index], index) == pytest.approx(2.0)
    assert v.beta([0.5 * r + 0.001 for r in index], index) == pytest.approx(0.5)
    assert v.beta([0.01], [0.02]) is None
    assert v.beta(index, [0.01] * 6) is None
    assert v.bounded_beta(3.0) == v.BETA_BOUNDS[1]
    assert v.bounded_beta(0.1) == v.BETA_BOUNDS[0]
    out = v.value(FLAT, 800.0, 3.0)
    assert out.beta == 2.0 and out.beta_raw == 3.0 and "beta_bounded" in out.flags


def test_the_cost_of_debt_floors_without_debt_and_clamps_an_artefact():
    assert v.cost_of_debt(None, 200.0, 0.25) == pytest.approx(v.KD_BOUNDS[0] * 0.75)
    assert v.cost_of_debt(10.0, 0.0, 0.25) == pytest.approx(v.KD_BOUNDS[0] * 0.75)
    assert v.cost_of_debt(10.0, 1.0, 0.25) == pytest.approx(v.KD_BOUNDS[1] * 0.75)
    assert v.wacc(0.09, 0.0375, 0.0, 0.0) == 0.09


def _reprice(years, out, margin=None, growth=None):
    """The model's value with one lever moved, everything else as the base case."""
    d = out.drivers
    if margin is not None:
        d = v._replace_margin(d, margin)
    if growth is not None:
        d = v._replace_growth(d, v.fade(growth, d.terminal_growth))
    return v._ps(max(years, key=lambda y: y.end), d, out.wacc)


def test_the_reverse_dcf_at_the_fair_value_returns_the_base_case():
    out = v.value(FLAT, market_cap=800.0, beta_raw=1.0, consensus_growth=0.05)
    imp = v.implied(FLAT, out, out.fair_value)
    assert imp.margin == pytest.approx(out.drivers.ebit_margin)
    assert imp.growth == pytest.approx(out.drivers.growth[0], abs=1e-9)
    assert imp.growth_edge is None


def test_the_implied_margin_and_growth_each_reprice_the_model_to_the_price():
    out = v.value(FLAT, market_cap=800.0, beta_raw=1.0)
    price = out.fair_value * 1.8
    imp = v.implied(FLAT, out, price)
    assert imp.margin > out.drivers.ebit_margin and imp.growth > out.drivers.growth[0]
    assert _reprice(FLAT, out, margin=imp.margin) == pytest.approx(price, abs=0.005)
    assert _reprice(FLAT, out, growth=imp.growth) == pytest.approx(price, abs=0.005)
    cheap = v.implied(FLAT, out, out.fair_value * 0.5)
    assert cheap.margin < out.drivers.ebit_margin and cheap.growth < out.drivers.growth[0]


def test_a_price_no_plausible_path_reaches_says_so():
    out = v.value(FLAT, market_cap=800.0, beta_raw=1.0)
    imp = v.implied(FLAT, out, out.fair_value * 1000)
    assert imp.growth_edge == "above" and imp.growth == v.IMPLIED_GROWTH_BOUNDS[1]
    assert imp.margin is None  # a margin over 100% of revenue is not a business
    low = v.implied(FLAT, out, 0.01)
    assert low.growth_edge == "below"
    assert v.implied(FLAT, out, 0.0) is None


def test_the_best_figures_are_the_window_highs():
    years = [
        _year("2021-12-31", revenue=500.0, ebit=150.0),
        _year("2022-12-31", revenue=600.0, ebit=60.0),
        _year("2023-12-31", revenue=900.0, ebit=90.0),
        _year("2024-12-31", revenue=900.0, ebit=90.0),
        _year("2025-12-31", revenue=1000.0, ebit=100.0),
    ]
    margin, growth = v.best_figures(years)
    assert margin == pytest.approx(0.30)
    assert growth == pytest.approx(0.50)
    assert v.best_figures(years[:1])[1] is None


def test_effective_tax_defaults_when_nothing_reads():
    assert v.effective_tax([_year(y.end, pretax=None, taxes=None) for y in FLAT]) == v.DEFAULT_TAX
    assert v.effective_tax([_year(y.end, pretax=100.0, taxes=0.0) for y in FLAT]) == v.TAX_BOUNDS[0]
