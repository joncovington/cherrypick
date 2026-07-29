import pytest

import costs

CONFIG = {
    "tastytrade_costs": {
        "commission_open_per_contract": 1.00,
        "commission_close_per_contract": 0.00,
        "commission_cap_per_leg": 10.00,
        "clearing_fee_per_contract": 0.10,
        "regulatory_fee_per_contract": 0.04,
        "slippage_frac_of_spread": 0.25,
    }
}

TWO_LEG_ORDER = {"order": {"legs": [{"symbol": "A"}, {"symbol": "B"}]}}
TWO_LEG_QUOTES = [{"bid": 3.00, "ask": 3.35}, {"bid": 1.00, "ask": 1.35}]


def test_entry_costs_match_tastytrade_schedule_worked_example():
    result = costs.apply_entry_costs(TWO_LEG_ORDER, TWO_LEG_QUOTES, quantity=5, config=CONFIG)
    assert result["commission"] == pytest.approx(10.00)
    assert result["pass_through_fees"] == pytest.approx(1.40)
    assert result["slippage"] == pytest.approx(87.5)
    assert result["total_cost"] == pytest.approx(98.9)


def test_exit_costs_have_zero_commission_by_default():
    result = costs.apply_exit_costs(TWO_LEG_ORDER, TWO_LEG_QUOTES, quantity=5, config=CONFIG)
    assert result["commission"] == pytest.approx(0.0)
    assert result["pass_through_fees"] == pytest.approx(1.40)
    assert result["slippage"] == pytest.approx(87.5)


def test_commission_cap_binds_per_leg():
    order = {"order": {"legs": [{"symbol": "A"}]}}
    quotes = [{"bid": 1.00, "ask": 1.10}]
    assert costs.apply_entry_costs(order, quotes, quantity=5, config=CONFIG)["commission"] == pytest.approx(
        5.0
    )
    assert costs.apply_entry_costs(order, quotes, quantity=10, config=CONFIG)["commission"] == pytest.approx(
        10.0
    )
    assert costs.apply_entry_costs(order, quotes, quantity=15, config=CONFIG)["commission"] == pytest.approx(
        10.0
    )
    assert costs.apply_entry_costs(order, quotes, quantity=100, config=CONFIG)["commission"] == pytest.approx(
        10.0
    )


def test_cap_applies_per_leg_not_per_order():
    order = {"order": {"legs": [{"symbol": "A"}, {"symbol": "B"}, {"symbol": "C"}, {"symbol": "D"}]}}
    quotes = [{"bid": 1.0, "ask": 1.05}] * 4
    result = costs.apply_entry_costs(order, quotes, quantity=15, config=CONFIG)
    assert result["commission"] == pytest.approx(4 * 10.0)


def test_defaults_used_when_config_missing_tastytrade_costs_block():
    order = {"order": {"legs": [{"symbol": "A"}]}}
    quotes = [{"bid": 1.00, "ask": 1.20}]
    result = costs.apply_entry_costs(order, quotes, quantity=2, config={})
    assert result["commission"] == pytest.approx(2.0)
    assert result["pass_through_fees"] == pytest.approx(2 * (0.10 + 0.04))
    # Assert against the shipped default fraction (source of truth) so a future recalibration
    # of the slippage default doesn't silently rot this test — the spread here (0.20) is well
    # under the mid cap, so the plain fraction applies.
    assert result["slippage"] == pytest.approx(
        0.20 * costs.DEFAULT_COSTS["slippage_frac_of_spread"] * 100 * 2
    )


def test_zero_width_quote_produces_zero_slippage():
    order = {"order": {"legs": [{"symbol": "A"}]}}
    quotes = [{"bid": 1.00, "ask": 1.00}]
    result = costs.apply_entry_costs(order, quotes, quantity=1, config=CONFIG)
    assert result["slippage"] == pytest.approx(0.0)


def test_negative_spread_treated_as_zero():
    order = {"order": {"legs": [{"symbol": "A"}]}}
    quotes = [{"bid": 1.10, "ask": 1.00}]  # crossed/bad quote
    result = costs.apply_entry_costs(order, quotes, quantity=1, config=CONFIG)
    assert result["slippage"] == pytest.approx(0.0)


# --- property-style sweeps (parametrized, no extra dependency) -------------------
# These pin the algebraic facts downstream code RELIES on, across a grid of shapes,
# rather than single worked examples.

_SHAPES = [
    [{"symbol": "A"}],  # single
    [{"symbol": "A"}, {"symbol": "B"}],  # vertical
    [{"symbol": "A"}, {"symbol": "B"}, {"symbol": "C"}, {"symbol": "D"}],  # condor
    [
        {"symbol": "A", "quantity": 1},
        {"symbol": "B", "quantity": 2},
        {"symbol": "C", "quantity": 1},
    ],  # 1-2-1 fly
]
_QUOTES = {
    "A": {"bid": 3.00, "ask": 3.30},
    "B": {"bid": 1.00, "ask": 1.10},
    "C": {"bid": 0.50, "ask": 0.58},
    "D": {"bid": 0.10, "ask": 0.16},
}


def _cfg(frac):
    return {"tastytrade_costs": {**CONFIG["tastytrade_costs"], "slippage_frac_of_spread": frac}}


@pytest.mark.parametrize("legs", _SHAPES)
@pytest.mark.parametrize("qty", [1, 2, 7])
def test_doubling_the_slippage_fraction_costs_exactly_the_slippage_again(legs, qty):
    """The identity the suite's cost-sensitivity column depends on: slippage is linear
    in the fraction, so total@2f == total@f + slippage@f, for every shape and size."""
    order = {"order": {"legs": legs}}
    quotes = [_QUOTES[leg["symbol"]] for leg in legs]
    at_f = costs.apply_entry_costs(order, quotes, qty, _cfg(0.125))
    at_2f = costs.apply_entry_costs(order, quotes, qty, _cfg(0.25))
    assert at_2f["total_cost"] == pytest.approx(at_f["total_cost"] + at_f["slippage"], abs=0.02)
    assert at_2f["slippage"] == pytest.approx(2 * at_f["slippage"], abs=0.02)


@pytest.mark.parametrize("legs", _SHAPES)
def test_costs_scale_linearly_in_quantity_below_the_commission_cap(legs):
    order = {"order": {"legs": legs}}
    quotes = [_QUOTES[leg["symbol"]] for leg in legs]
    q1 = costs.apply_entry_costs(order, quotes, 1, CONFIG)
    q3 = costs.apply_entry_costs(order, quotes, 3, CONFIG)
    assert q3["total_cost"] == pytest.approx(q1["total_cost"] * 3, abs=0.03)


@pytest.mark.parametrize("legs", _SHAPES)
@pytest.mark.parametrize("widen", [0.0, 0.05, 0.20, 1.0])
def test_slippage_never_decreases_as_spreads_widen(legs, widen):
    order = {"order": {"legs": legs}}
    base = [_QUOTES[leg["symbol"]] for leg in legs]
    wider = [{"bid": q["bid"], "ask": q["ask"] + widen} for q in base]
    s0 = costs.apply_entry_costs(order, base, 1, CONFIG)["slippage"]
    s1 = costs.apply_entry_costs(order, wider, 1, CONFIG)["slippage"]
    assert s1 >= s0 - 0.01
