"""The DCF (cherrypick.core.valuation, version 2): the arithmetic to the cent, the identities, the
refusals and the reverse DCF."""

from __future__ import annotations

import dataclasses

import pytest

from cherrypick.core import valuation as v

# A company whose history is flat, so every median is the year itself and the arithmetic can be
# checked by hand: revenue 1,000, EBIT 100, D&A 50, capex 50, working capital absorbing 10, tax 25%.
# Dollars throughout; 100 shares, so the answer is dollars a share.


def _year(end: str, revenue: float = 1000.0, ebit: float = 100.0, **over) -> v.FiscalYear:
    fields = dict(
        end=end, revenue=revenue, ebit=ebit, da=50.0, capex=50.0, nwc_change=-10.0, debt=200.0, cash=100.0,
        shares=100.0, pretax=90.0, taxes=22.5, interest=10.0,
    )  # fmt: skip
    fields.update(over)
    return v.FiscalYear(**fields)


FLAT = [_year(f"20{y}-12-31") for y in (21, 22, 23, 24, 25)]
# Damodaran's large-firm bands, three of them: enough to read a weak, a middling and a strong borrower.
RATINGS = [
    v.Rating(-100000.0, 1.249999, "Caa/CCC", 0.0885),
    v.Rating(1.25, 4.249999, "A3/A-", 0.0089),
    v.Rating(4.25, 100000.0, "Aaa/AAA", 0.004),
]
RF, ERP, PRICE = 0.04, 0.05, 8.0  # market cap 800 against 200 of debt


def _value(years=FLAT, price=PRICE, beta_raw=1.0, **kw):
    return v.value(years, price, beta_raw, RF, ERP, RATINGS, **kw)


def test_drivers_from_a_flat_history_are_the_history():
    d = v.drivers_from_history(FLAT)
    assert (d.ebit_margin, d.da_pct, d.capex_pct, d.tax_rate) == pytest.approx((0.10, 0.05, 0.05, 0.25))
    assert d.wc_pct == pytest.approx(0.01) and not d.wc_is_level  # no balance-sheet level: the flow
    # No growth in the history: year one is 0% and fades in a straight line to 2.5% by year ten.
    assert len(d.growth) == v.FORECAST_YEARS == 10
    assert d.growth[0] == 0.0 and d.growth[-1] == pytest.approx(v.TERMINAL_GROWTH)
    assert d.growth[4] == pytest.approx(v.TERMINAL_GROWTH * 4 / 9)
    assert d.growth_sources == ("history",) + ("fade",) * 9


def test_consensus_leads_the_path_and_the_fade_starts_from_its_last_year():
    """Year one is the current fiscal year's consensus revenue, year two the next; then a straight line
    from year two's growth to the long-run rate in year ten (BMY's patent cliff would ride the line)."""
    d = v.drivers_from_history(FLAT, consensus=[1100.0, 990.0])
    assert d.growth[:2] == pytest.approx((0.10, -0.10))
    assert d.growth_sources[:2] == ("consensus", "consensus")
    assert d.growth[2] == pytest.approx(-0.10 + (0.025 + 0.10) / 8)
    assert d.growth[-1] == pytest.approx(0.025)
    # History growth is clamped; consensus only inside its wider sanity band.
    assert v.drivers_from_history(FLAT, consensus=[1800.0]).growth[0] == pytest.approx(0.80)
    years = [
        _year(f"20{y}-12-31", revenue=r, ebit=r / 10) for y, r in ((21, 100.0), (22, 200.0), (23, 400.0))
    ]
    assert v.drivers_from_history(years).growth[0] == pytest.approx(v.HISTORY_GROWTH_BOUNDS[1])


def test_a_working_capital_level_is_used_when_the_balance_sheet_states_it_and_floored_at_zero():
    levels = [dataclasses.replace(y, wc_level=50.0) for y in FLAT]
    d = v.drivers_from_history(levels)
    assert d.wc_is_level and d.wc_pct == pytest.approx(0.05) and not d.wc_floored
    neg = [dataclasses.replace(y, wc_level=-250.0) for y in FLAT]  # DELL's shape: -25% of revenue
    d = v.drivers_from_history(neg)
    assert d.wc_pct == 0.0 and d.wc_floored
    assert "wc_floored" in _value(neg).flags


def test_the_medians_ignore_one_bad_year_and_a_loss_year_s_tax():
    years = FLAT[:-1] + [_year("2025-12-31", ebit=-400.0, pretax=-410.0, taxes=0.0)]
    d = v.drivers_from_history(years)
    assert d.ebit_margin == pytest.approx(0.10) and d.tax_rate == pytest.approx(0.25)


def test_the_cost_of_capital_by_hand():
    """beta 1.0 adjusts to 1.0; ke = 4% + 5% = 9%. Coverage 100 / 10 = 10x reads AAA: kd = 4% + 0.4% =
    4.4% pre-tax, 3.3% after the 25% marginal rate. 800 of equity against 200 of debt: 7.86%."""
    cap = _value().capital
    assert (cap.beta, cap.ke, cap.coverage, cap.rating) == (
        pytest.approx(1.0),
        pytest.approx(0.09),
        10.0,
        "Aaa/AAA",
    )
    assert (cap.kd_pre_tax, cap.kd_after_tax) == pytest.approx((0.044, 0.033))
    assert (cap.equity_weight, cap.wacc) == pytest.approx((0.8, 0.0786))


def test_a_weak_borrower_pays_a_lender_s_rate_not_its_coupon():
    """AAL's shape: EBIT covers interest 0.85 times. The coupon is irrelevant; the rating sets the rate."""
    weak = [dataclasses.replace(y, interest=117.65) for y in FLAT]
    cap = _value(weak).capital
    assert cap.rating == "Caa/CCC" and cap.kd_pre_tax == pytest.approx(RF + 0.0885)
    assert v.rate_for(None, RATINGS).name == "Aaa/AAA"  # no interest expense: the top band
    assert v.rate_for(1.2499995, RATINGS).name == "A3/A-"  # between rounded edges: the next band up


def test_blume_pulls_beta_toward_one_and_a_wild_one_is_bounded_and_flagged():
    assert v.blume(1.6) == pytest.approx(0.67 * 1.6 + 0.33)
    out = _value(beta_raw=6.0)
    assert out.capital.beta == v.BETA_SANITY[1] and "beta_bounded" in out.flags
    assert "beta_bounded" not in _value().flags


def _by_hand(d: v.Drivers, base: v.FiscalYear, rate: float) -> tuple[float, float, float]:
    """(EV, the discounted terminal value, the terminal value) recomputed from the definitions."""
    revenue, pv, wc_prev = base.revenue, 0.0, d.wc_pct * base.revenue
    for t, g in enumerate(d.growth, 1):
        revenue *= 1 + g
        wc = revenue * d.wc_pct if not d.wc_is_level else d.wc_pct * revenue - wc_prev
        wc_prev = d.wc_pct * revenue
        fcf = revenue * d.ebit_margin * (1 - d.tax_rate) + revenue * (d.da_pct - d.capex_pct) - wc
        pv += fcf / (1 + rate) ** (t - 0.5)
    nopat11 = revenue * (1 + d.terminal_growth) * d.ebit_margin * (1 - d.tax_rate)
    tv = nopat11 * (1 - d.terminal_growth / rate) / (rate - d.terminal_growth)
    return pv + tv / (1 + rate) ** 10, tv / (1 + rate) ** 10, tv


def test_the_value_to_the_cent_against_a_hand_computation():
    out = _value()
    ev, pv_tv, tv = _by_hand(out.drivers, FLAT[-1], out.wacc)
    assert out.ev == pytest.approx(ev) and out.terminal_value == pytest.approx(tv)
    assert out.fair_value == pytest.approx((ev + 100 - 200) / 100, abs=0.005)
    assert out.terminal_share == pytest.approx(pv_tv / ev)
    assert out.bridge.pv_forecast + out.bridge.pv_terminal == pytest.approx(out.bridge.ev)
    assert out.implied_ev_ebitda == pytest.approx(ev / 150)
    assert out.low <= out.fair_value <= out.high
    assert out.projections[0].discount == pytest.approx(1 / (1 + out.wacc) ** 0.5)  # mid-year


def test_growth_after_year_ten_is_paid_for():
    """With the return on capital at the WACC, the perpetuity is worth NOPAT_11 / WACC whatever the
    growth: growth neither adds value nor removes it. A higher return on capital lets it add."""
    out = _value()
    d, rate = out.drivers, out.wacc
    nopat11 = out.projections[-1].revenue * (1 + d.terminal_growth) * d.ebit_margin * (1 - d.tax_rate)
    assert out.terminal_value == pytest.approx(nopat11 / rate)
    assert out.reinvestment_rate == pytest.approx(d.terminal_growth / rate)
    assert _value(roic=0.20).fair_value > out.fair_value


def test_the_bridge_takes_every_claim_and_counts_the_dilution():
    claims = [dataclasses.replace(y, finance_lease=20.0, preferred=10.0, nci=5.0, rsu=4.0) for y in FLAT]
    out, plain = _value(claims), _value()
    b = out.bridge
    assert b.equity == pytest.approx(b.ev + 100 - 200 - 20 - 10 - 5)
    assert b.shares == pytest.approx(104.0) and out.fair_value == pytest.approx(b.equity / 104.0)
    assert out.capital.debt_weight > plain.capital.debt_weight  # a finance lease is debt in the weights
    # Options in the money at the modelled value add shares by the treasury-stock method.
    opts = [dataclasses.replace(y, options=10.0, options_strike=1.0) for y in FLAT]
    ob = _value(opts).bridge
    ps = ob.equity / ob.shares
    assert ob.option_shares == pytest.approx(10.0 * (ps - 1.0) / ps, rel=1e-3)
    out_of = [dataclasses.replace(y, options=10.0, options_strike=1e6) for y in FLAT]
    assert _value(out_of).bridge.option_shares == 0.0


def test_the_grid_is_monotone_in_the_rate_and_in_growth():
    out = _value()
    for dg in v.GROWTH_STEPS:
        column = [out.grid[(dw, dg)] for dw in v.WACC_STEPS]
        assert column == sorted(column, reverse=True)
    assert out.grid[(0.0, 0.0)] == pytest.approx(out.fair_value)


def test_a_consensus_year_below_long_run_growth_is_flagged():
    assert "consensus_dip" in _value(consensus=[1010.0, 990.0]).flags
    assert "consensus_dip" not in _value(consensus=[1100.0, 1200.0]).flags


@pytest.mark.parametrize(
    "years, reason",
    [
        (FLAT[:2], "too_few_years"),
        ([*FLAT[:-1], _year("2025-12-31", revenue=0.0)], "no_revenue"),
        ([_year(y.end, ebit=-10.0) for y in FLAT], "no_operating_profit"),
    ],
)
def test_a_history_the_model_cannot_read_is_refused(years, reason):
    assert v.drivers_from_history(years) == v.Refusal(reason)
    assert _value(years) == v.Refusal(reason)


def test_the_other_refusals():
    assert _value(beta_raw=None) == v.Refusal("no_beta")
    assert _value(beta_raw=float("nan")) == v.Refusal("no_beta")
    assert v.value(FLAT, PRICE, 1.0, RF, ERP, []) == v.Refusal("no_rating_table")
    assert _value([_year(y.end, shares=0.0) for y in FLAT]) == v.Refusal("no_shares")
    # A claim ahead of the shareholders larger than the business (preferred stock, outside the weights).
    assert _value([_year(y.end, preferred=1e6) for y in FLAT]) == v.Refusal("negative_equity")
    assert v.value(FLAT, PRICE, 1.0, 0.0, 0.0, RATINGS, terminal_growth=0.03) == v.Refusal("wacc_near_growth")


def test_the_reverse_dcf_reprices_the_model_to_the_price():
    out = _value()
    for price in (out.fair_value * 0.6, out.fair_value * 1.8):
        imp = v.implied(FLAT, out, price)
        d = dataclasses.replace(out.drivers, ebit_margin=imp.margin)
        assert v._price(FLAT[-1], d, out.wacc)[0] == pytest.approx(price, rel=1e-3)
        path, src = v.growth_path(1000.0, [], imp.growth, out.drivers.terminal_growth, clamp=False)
        at_growth = v._price(
            FLAT[-1], dataclasses.replace(out.drivers, growth=path, growth_sources=src), out.wacc
        )[0]
        assert at_growth == pytest.approx(price, rel=1e-3)
        assert v._price(FLAT[-1], out.drivers, imp.wacc)[0] == pytest.approx(price, rel=1e-3)
    assert v.implied(FLAT, out, 0.0) is None


def test_a_price_no_plausible_path_reaches_says_so():
    imp = v.implied(FLAT, _value(), 1e6)
    assert imp.growth_edge == "above" and imp.margin is None and imp.wacc is None


def test_the_best_figures_are_the_window_highs():
    years = [
        _year(f"20{y}-12-31", revenue=r, ebit=e)
        for y, r, e in ((21, 800, 60), (22, 1000, 150), (23, 900, 90))
    ]
    margin, growth = v.best_figures(years)
    assert margin == pytest.approx(0.15) and growth == pytest.approx(0.25)


def test_effective_tax_defaults_when_nothing_reads():
    assert v.effective_tax([_year("2025-12-31", pretax=None, taxes=None)]) == v.DEFAULT_TAX
