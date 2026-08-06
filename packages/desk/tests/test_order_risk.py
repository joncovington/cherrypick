"""Worst-case risk from the expiry payoff — the number every risk gate is measured against.

The structures here are the real ones this desk has been asked to place (a BKNG iron condor, an XYZ
butterfly) plus the shapes that break naive implementations: ratios, broken wings, naked shorts, and
closing orders. If `max_loss` is wrong, every cap above it is decorative.
"""

import pytest

from cherrypick.desk.order import OrderError, analyze, parse_occ

pytestmark = pytest.mark.unit


def _leg(symbol, action, qty=1, itype="Equity Option"):
    return {"instrument_type": itype, "symbol": symbol, "action": action, "quantity": qty}


def _order(legs, price, effect):
    return {"legs": legs, "price": price, "price_effect": effect}


# --------------------------------------------------------------------------- OCC parsing
def test_occ_parsing_recovers_every_field():
    assert parse_occ("XYZ   260807C00085000") == ("XYZ", __import__("datetime").date(2026, 8, 7), "C", 85.0)
    assert parse_occ("BKNG  260807P00175000") == ("BKNG", __import__("datetime").date(2026, 8, 7), "P", 175.0)


def test_fractional_strike_survives_the_x1000_encoding():
    _, _, _, strike = parse_occ("SPY   260807C00457500")
    assert strike == 457.5


def test_undecodable_symbol_is_an_error_not_a_guess():
    """An unparseable symbol is a position whose risk we cannot compute — it must refuse, because
    the alternative is a gate silently measuring against a wrong (or zero) worst case."""
    with pytest.raises(OrderError):
        parse_occ("NOT-AN-OCC-SYMBOL")


# --------------------------------------------------------------------------- real structures
def test_long_butterfly_max_loss_is_the_debit():
    """The XYZ 85/91/97 call fly actually placed: 1.10 debit, max loss the debit, max gain at the
    body, breakevens one wing-width in from each end."""
    legs = [
        _leg("XYZ   260807C00085000", "buy to open"),
        _leg("XYZ   260807C00091000", "sell to open", 2),
        _leg("XYZ   260807C00097000", "buy to open"),
    ]
    _, risk = analyze(_order(legs, 1.10, "debit"))
    assert risk.defined is True
    assert risk.max_loss == pytest.approx(110.0)
    assert risk.max_gain == pytest.approx(490.0)  # (91-85)*100 - 110
    assert risk.breakevens == pytest.approx((86.10, 95.90))
    assert risk.classification == "opening"
    assert risk.spreads == 1


def test_short_iron_condor_max_loss_is_width_minus_credit():
    """The BKNG 175/180/210/215 condor: 5-wide wings, 1.50 credit -> 350 worst case."""
    legs = [
        _leg("BKNG  260807P00175000", "buy to open"),
        _leg("BKNG  260807P00180000", "sell to open"),
        _leg("BKNG  260807C00210000", "sell to open"),
        _leg("BKNG  260807C00215000", "buy to open"),
    ]
    _, risk = analyze(_order(legs, 1.50, "credit"))
    assert risk.defined is True
    assert risk.max_loss == pytest.approx(350.0)
    assert risk.max_gain == pytest.approx(150.0)


def test_multi_lot_scales_the_premium_by_the_spread_count():
    """A 2/-4/2 butterfly is TWO spreads at the quoted per-spread debit — reading the price as a
    single spread's would understate cost and risk by half."""
    legs = [
        _leg("XYZ   260807C00085000", "buy to open", 2),
        _leg("XYZ   260807C00091000", "sell to open", 4),
        _leg("XYZ   260807C00097000", "buy to open", 2),
    ]
    _, risk = analyze(_order(legs, 1.10, "debit"))
    assert risk.spreads == 2
    assert risk.max_loss == pytest.approx(220.0)


# --------------------------------------------------------------------------- undefined risk
def test_naked_short_call_is_unbounded():
    """The one genuinely unbounded direction. `max_loss` is None, and callers must read that as
    'worse than any cap' — never as 'no risk'."""
    _, risk = analyze(_order([_leg("XYZ   260807C00091000", "sell to open")], 1.44, "credit"))
    assert risk.defined is False
    assert risk.max_loss is None
    assert risk.unbounded is True


def test_naked_short_put_is_bounded_but_large():
    """Downside stops at a zero underlying, so a short put is *defined* — just badly. The cap, not
    the defined-risk flag, is what should stop this one."""
    _, risk = analyze(_order([_leg("XYZ   260807P00085000", "sell to open")], 3.0, "credit"))
    assert risk.defined is True
    assert risk.max_loss == pytest.approx(8500.0 - 300.0)


def test_ratio_spread_that_goes_net_short_calls_is_unbounded():
    """A 1x2 ratio: covered up to the short strike, then unbounded. Pattern-matching on 'is it a
    vertical?' would call this defined; the slope test gets it right."""
    legs = [
        _leg("XYZ   260807C00085000", "buy to open", 1),
        _leg("XYZ   260807C00091000", "sell to open", 2),
    ]
    _, risk = analyze(_order(legs, 0.5, "credit"))
    assert risk.defined is False


def test_broken_wing_butterfly_is_defined_but_asymmetric():
    """The bwb the flies module trades: wings of unequal width, still fully covered, and the worst
    case sits on the wide side rather than at a debit-shaped floor."""
    legs = [
        _leg("XYZ   260807C00085000", "buy to open"),
        _leg("XYZ   260807C00091000", "sell to open", 2),
        _leg("XYZ   260807C00101000", "buy to open"),
    ]
    _, risk = analyze(_order(legs, 0.40, "credit"))
    assert risk.defined is True
    # Wide side tail: (101-91) - (91-85) = 4 wide, less the 0.40 credit taken in.
    assert risk.max_loss == pytest.approx(400.0 - 40.0)


# --------------------------------------------------------------------------- classification
def test_all_close_legs_classify_as_closing():
    """The case that motivated the package: a risk-REDUCING order must be recognizable as such so a
    cap built for new exposure cannot block someone flattening a position."""
    legs = [
        _leg("BKNG  260807P00180000", "buy to close"),
        _leg("BKNG  260807C00210000", "buy to close"),
        _leg("BKNG  260807P00175000", "sell to close"),
        _leg("BKNG  260807C00215000", "sell to close"),
    ]
    _, risk = analyze(_order(legs, 1.47, "debit"))
    assert risk.classification == "closing"


def test_mixed_open_and_close_classifies_as_mixed():
    """A roll. Policy treats it as opening — it establishes new legs, so it clears the same bar."""
    legs = [
        _leg("XYZ   260807C00091000", "buy to close"),
        _leg("XYZ   260814C00091000", "sell to open"),
    ]
    _, risk = analyze(_order(legs, 0.30, "credit"))
    assert risk.classification == "mixed"


# --------------------------------------------------------------------------- malformed input
def test_market_order_is_refused_outright():
    """No price means unbounded cost. There is no safe default to substitute, so it refuses."""
    with pytest.raises(OrderError, match="no price"):
        analyze({"legs": [_leg("XYZ   260807C00085000", "buy to open")], "price_effect": "debit"})


def test_missing_price_effect_is_refused():
    """debit vs credit flips the sign of every P&L number below it — it is never inferred."""
    with pytest.raises(OrderError, match="price_effect"):
        analyze({"legs": [_leg("XYZ   260807C00085000", "buy to open")], "price": 1.0})


def test_negative_quantity_is_refused_as_ambiguous():
    """Direction lives in `action`. Allowing a negative quantity too makes 'sell to open -2' a
    double negative, which is a plausible way to fat-finger a side."""
    with pytest.raises(OrderError, match="positive"):
        analyze(_order([_leg("XYZ   260807C00085000", "buy to open", -1)], 1.0, "debit"))


def test_unknown_action_is_refused():
    with pytest.raises(OrderError, match="unknown leg action"):
        analyze(_order([_leg("XYZ   260807C00085000", "yolo")], 1.0, "debit"))


def test_empty_order_is_refused():
    with pytest.raises(OrderError, match="no legs"):
        analyze(_order([], 1.0, "debit"))
