"""The evaluation prices every leg at the name's dividend yield (2026-11-07)."""

import pytest
from cherrypick.core import bs
from test_evaluate import SESSION, leg

from cherrypick.manage import evaluate as ev
from cherrypick.manage import portfolio, pricing
from cherrypick.manage import rules as _rules

RULES = _rules.effective(None)[0]
FAR = "2028-12-15"


def _run(legs, div_yield=None, spot=100.0):
    return ev.evaluate_trade("XYZ", legs, {"last": spot}, SESSION, 1e5, RULES, div_yield=div_yield)


def test_a_long_dated_call_under_the_no_dividend_floor_is_priced_at_the_names_yield():
    # Two years out at a 4% rate and no dividend, a 95 call on a $100 stock is worth at least $12.92.
    # An $11 mid sits under that: no vol prices it, and the leg was left unmodelled.
    call = [leg(1, "C", 95, FAR, 11.0, 11.0)]
    bare = _run(call)["legs"][0]
    assert bare["iv"] is None and bare["iv_source"] == "unpriceable"
    assert bare["div_yield"] == 0.0 and bare["div_source"] == "none"
    paid = _run(call, div_yield=0.045)["legs"][0]
    assert paid["iv_source"] == "mid" and paid["div_source"] == "history" and paid["div_yield"] == 0.045
    # The vol it solves to prices the mid back at the same yield, and the delta carries it too.
    years = paid["years"]
    assert pricing.price(100.0, 95, years, paid["iv"], "C", 0.04, 0.045) == pytest.approx(11.0, abs=1e-6)
    assert paid["delta"] == pytest.approx(bs.delta(100.0, 95, years, paid["iv"], "C", 0.04, 0.045))
    # A trade with it outliving a nearer short is modelled again: a payoff profile, where there was none.
    diagonal = [*call, leg(-1, "C", 104, "2026-11-20", 2.5, 2.5)]
    assert _run(diagonal)["profile"] is None
    assert _run(diagonal, div_yield=0.045)["profile"] is not None


def test_a_name_with_no_dividend_prices_as_before():
    legs = [leg(1, "C", 100, "2026-12-18", 3.0, 3.0), leg(-1, "C", 105, "2026-11-20", 1.0, 1.0)]
    before, after = _run(legs), _run(legs, div_yield=0.0)
    assert [x["iv"] for x in before["legs"]] == [x["iv"] for x in after["legs"]]
    assert before["profile"] == after["profile"]
    assert after["legs"][0]["div_source"] == "history"  # a record of no dividends is still a record


@pytest.mark.parametrize("right", ["C", "P"])
def test_greeks_carry_the_yield(right):
    s, k, t, v, r, q = 100.0, 95.0, 1.5, 0.22, 0.04, 0.05
    g = pricing.greeks(s, k, t, v, right, r, q)
    h, day = 0.01, 1 / 365.0
    up, dn = pricing.price(s + h, k, t, v, right, r, q), pricing.price(s - h, k, t, v, right, r, q)
    assert g["delta"] == pytest.approx((up - dn) / (2 * h), abs=1e-5)
    assert g["delta"] == pytest.approx(bs.delta(s, k, t, v, right, r, q), abs=1e-9)
    mid = pricing.price(s, k, t, v, right, r, q)
    assert g["gamma"] == pytest.approx((up - 2 * mid + dn) / (h * h), rel=1e-3)
    later = pricing.price(s, k, t - day, v, right, r, q)
    assert g["theta"] == pytest.approx(later - mid, abs=2e-4)
    bumped = pricing.price(s, k, t, v + 0.01, right, r, q)
    assert g["vega"] == pytest.approx(bumped - mid, rel=2e-2)
    # No yield: the greeks are what they were.
    assert pricing.greeks(s, k, t, v, right, r) == pricing.greeks(s, k, t, v, right, r, 0.0)


def test_a_deep_call_is_never_modelled_under_its_intrinsic():
    # With a yield a deep European call prices under intrinsic; an American one is worth at least that.
    euro = pricing.price(150.0, 80.0, 2.0, 0.15, "C", 0.04, 0.08)
    assert euro < 70.0
    assert pricing.model_price(150.0, 80.0, 2.0, 0.15, "C", 0.04, 0.08) == pytest.approx(70.0)
    held = {"quantity": 1.0, "right": "C", "strike": 80.0, "iv": 0.15, "div_yield": 0.08}
    assert pricing.leg_value_at(held, 150.0, 2.0, 0.04) == pytest.approx(7000.0)


def test_the_book_greeks_read_each_legs_yield():
    t = _run([leg(1, "C", 95, FAR, 11.0, 11.0)], div_yield=0.045)
    held = t["legs"][0]
    g = portfolio.leg_greeks(held, 100.0, 0.04)
    want = pricing.greeks(100.0, 95, held["years"], held["iv"], "C", 0.04, 0.045)
    assert g["delta"] == pytest.approx(100 * want["delta"])
