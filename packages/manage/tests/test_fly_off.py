import pytest
from test_evaluate import fired, leg, run

from cherrypick.manage import evaluate as ev
from cherrypick.manage import rules as _rules

NOV = "2026-11-20"


def put_bwb(near_mid, body_mid, far_mid, opened=(4.0, 2.6, 1.0)):
    # The strategy guide's put broken-wing butterfly: +100P / -2 97P / +91P, $20 in.
    return [
        leg(1, "P", 100, NOV, opened[0], near_mid),
        leg(-2, "P", 97, NOV, opened[1], body_mid),
        leg(1, "P", 91, NOV, opened[2], far_mid),
    ]


def test_a_credit_bwb_the_stock_has_left_flies_off_its_wide_wing():
    t = run(put_bwb(1.2, 0.7, 0.25), spot=106)
    assert t["shape"] == "broken-wing butterfly"
    assert fired(t)["roll.fly_off"] == ev.REVIEW
    fo = t["fly_off"]
    # The symmetric wing is 3 below the body, as the near wing is 3 above it.
    assert (fo["right"], fo["new_strike"], fo["old_strike"]) == ("P", 94, 91)
    assert 0.25 < fo["new_mid"] < 0.7  # priced between the old wing and the body
    assert fo["debit"] == pytest.approx((fo["new_mid"] - 0.25) * 100)
    assert fo["debit"] <= 20 and fo["kept"] == pytest.approx(20 - fo["debit"])
    assert fo["legs"][0].startswith("BTO 1 2026-11-20 94P") and "(modelled)" in fo["legs"][0]
    assert fo["legs"][1] == "STC 1 2026-11-20 91P @ 0.25"
    assert t["recommendation"]["rule"] == "roll.fly_off"
    assert "plain butterfly" in t["explanation"] and "94 strike is listed" in t["explanation"]


def test_a_bwb_still_near_its_body_costs_more_than_its_credit_to_fly_off():
    t = run(put_bwb(2.2, 1.35, 0.5), spot=103)
    assert "roll.fly_off" not in fired(t) and t["fly_off"] is None


def test_a_call_bwb_flies_off_above():
    # The guide's call BWB: +100C / -2 103C / +109C, after a selloff.
    t = run(
        [
            leg(1, "C", 100, NOV, 4.0, 1.0),
            leg(-2, "C", 103, NOV, 2.6, 0.55),
            leg(1, "C", 109, NOV, 1.0, 0.15),
        ],
        spot=94,
    )
    assert t["shape"] == "broken-wing butterfly"
    fo = t["fly_off"]
    assert fo and (fo["right"], fo["new_strike"], fo["old_strike"]) == ("C", 106, 109)


def test_a_bwb_bought_for_a_debit_has_no_credit_to_fly_off_with():
    # GLD's shape on 2026-10-13: opened for a net debit.
    t = run(put_bwb(1.2, 0.7, 0.25, opened=(4.0, 2.0, 1.0)), spot=106)
    assert t["cost"] > 0 and "roll.fly_off" not in fired(t)


def test_the_share_of_credit_caps_the_debit():
    rules = _rules.effective({"roll": {"fly_off": {"max_share_of_credit": 0.5}}})[0]
    assert "roll.fly_off" not in fired(run(put_bwb(1.2, 0.7, 0.25), spot=106, rules=rules))


def test_the_fly_off_can_be_switched_off():
    rules = _rules.effective({"positions": {"XYZ": {"disable": ["roll.fly_off"]}}})[0]
    assert "roll.fly_off" not in fired(run(put_bwb(1.2, 0.7, 0.25), spot=106, rules=rules))
