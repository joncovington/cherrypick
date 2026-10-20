import pytest
from test_evaluate import fired, leg, run

from cherrypick.manage import evaluate as ev
from cherrypick.manage import rules as _rules

NOV = "2026-11-20"


def put_bwb(near_mid, body_mid, far_mid=0.5):
    # The strategy guide's put broken-wing butterfly: +100P / -2 97P / +91P, opened for a small credit.
    return [
        leg(1, "P", 100, NOV, 4.0, near_mid),
        leg(-2, "P", 97, NOV, 2.6, body_mid),
        leg(1, "P", 91, NOV, 1.0, far_mid),
    ]


def test_a_broken_wing_butterflys_long_spread_near_its_width_is_sold():
    t = run(put_bwb(5.4, 2.65), spot=95)
    assert t["shape"] == "broken-wing butterfly"
    assert fired(t)["legs.long_vertical"] == ev.CLOSE_LEGS
    rec = t["recommendation"]
    assert rec["rule"] == "legs.long_vertical"
    rows = t["order"]["legs"]
    assert [(r["side"], r["quantity"], r["leg"]) for r in rows] == [
        ("STC", 1, "+1 2026-11-20 100P"),
        ("BTC", 1, "-1 2026-11-20 97P"),
    ]
    assert t["order"]["net"] == pytest.approx(275) and t["order"]["net_side"] == "cr"
    lv = t["long_vertical"]
    assert lv["width"] == pytest.approx(300) and lv["value"] == pytest.approx(275)
    assert lv["rest"] == ["-1 2026-11-20 97P", "+1 2026-11-20 91P"]
    assert lv["rest_cost"] == pytest.approx(215) and lv["rest_width"] == pytest.approx(600)
    assert "Sell only the long spread" in t["explanation"] and "$600 wide" in t["explanation"]


def test_a_long_spread_short_of_its_width_is_held():
    t = run(put_bwb(4.0, 2.6), spot=99)  # worth $140 of $300
    assert "legs.long_vertical" not in fired(t)
    assert t["long_vertical"] is None


def test_a_broken_heart_butterflys_long_spread_is_its_near_vertical():
    t = run(
        [
            leg(1, "C", 102, NOV, 3.0, 9.0),
            leg(-1, "C", 104, NOV, 2.2, 7.1),
            leg(-1, "C", 110, NOV, 0.6, 2.2),
            leg(1, "C", 114, NOV, 0.2, 0.9),
        ],
        spot=110,
    )
    assert t["shape"] == "broken-heart butterfly"
    assert fired(t)["legs.long_vertical"] == ev.CLOSE_LEGS
    assert t["long_vertical"]["legs"] == ["+1 2026-11-20 102C", "-1 2026-11-20 104C"]
    assert t["long_vertical"]["rest"] == ["-1 2026-11-20 110C", "+1 2026-11-20 114C"]


def test_a_butterfly_of_two_units_sells_two_long_spreads():
    t = run(
        [leg(2, "C", 100, NOV, 3.0, 9.8), leg(-4, "C", 105, NOV, 1.2, 5.2), leg(2, "C", 110, NOV, 0.3, 2.0)],
        spot=106,
    )
    assert t["shape"] == "butterfly"
    assert fired(t)["legs.long_vertical"] == ev.CLOSE_LEGS
    assert t["long_vertical"]["legs"] == ["+2 2026-11-20 100C", "-2 2026-11-20 105C"]
    assert t["long_vertical"]["rest"] == ["-2 2026-11-20 105C", "+2 2026-11-20 110C"]
    assert t["recommendation"]["rule"] == "legs.long_vertical"
    assert t["order"]["net"] == pytest.approx(920) and t["order"]["net_price"] == pytest.approx(4.6)


def test_inside_the_expiry_window_the_whole_butterfly_closes():
    # AA on 2026-10-16, its expiry day: +40C / -2 42C / +44C, the 40/42 spread worth $189 of $200.
    legs = [leg(1, "C", 40, "2026-10-16", 1.23, 3.25), leg(-2, "C", 42, "2026-10-16", 0.47, 1.36),
            leg(1, "C", 44, "2026-10-16", 0.14, 0.24)]  # fmt: skip
    t = run(legs, spot=43.23)
    assert "legs.long_vertical" not in fired(t)
    assert t["recommendation"]["rule"] == "expiry"
    # The same trade a month out sells the spread.
    later = [{**x, "expiration": NOV} for x in legs]
    assert fired(run(later, spot=43.23))["legs.long_vertical"] == ev.CLOSE_LEGS


def test_only_butterflies_have_a_long_spread_to_sell():
    # A debit call spread deep in the money is a whole trade, judged by the debit rules.
    t = run([leg(1, "C", 95, NOV, 3.0, 4.8), leg(-1, "C", 100, NOV, 1.0, 0.1)], spot=110)
    assert "legs.long_vertical" not in fired(t)


def test_the_long_spread_sale_can_be_switched_off():
    rules = _rules.effective({"positions": {"XYZ": {"disable": ["legs.long_vertical"]}}})[0]
    assert "legs.long_vertical" not in fired(run(put_bwb(5.4, 2.65), spot=95, rules=rules))
    rules = _rules.effective({"legs": {"long_vertical": {"enabled": False}}})[0]
    assert "legs.long_vertical" not in fired(run(put_bwb(5.4, 2.65), spot=95, rules=rules))
