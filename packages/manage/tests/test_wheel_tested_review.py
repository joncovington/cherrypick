"""wheel.tested on a wheel put with time to run.

A tested put on a name the operator wheels, with more than `review_beyond_days` (60) left and at least
`review_min_extrinsic` (0.10) a share of extrinsic value, is a REVIEW that says why, not a roll or a close:
the snapshot reads roll-out chains inside the horizon only, so there is no roll to price, and the
assignment is unlikely while time value remains. Inside the horizon, or under the floor, it is the roll,
close or assignment it always was. A put not marked a wheel keeps its stop. Synthetic histories, as in
test_wheel_rules.py.
"""

from test_wheel import occ, opt_leg, tx
from test_wheel_put import WHEEL
from test_wheel_rules import INCOME, evaluate, fired, trade_of

from cherrypick.manage import evaluate as ev

SPOT = 48.0  # two through the 50 strike
# The session is 2026-10-13: 2026-12-12 is 60 days out, 2026-12-13 is 61.
INSIDE, EDGE, BEYOND = "2026-12-12", "2026-12-12", "2026-12-13"


def put_at(expiry, mid, overrides=WHEEL, spot=SPOT):
    sym = occ(expiry, "P", 50)
    history = [tx(1, "2026-10-01T15:00:00Z", "Sell to Open", sym, 1, 1.00)]
    return trade_of(
        evaluate(history, [opt_leg(sym, -1, mid, 1.00)], spot, income=INCOME, overrides=overrides), sym
    )


# intrinsic 2.00 at the 48 spot: a mid of 2.60 is 0.60 of extrinsic value.
def test_a_tested_wheel_put_far_from_expiry_with_time_value_is_a_review_that_says_why():
    t = put_at("2027-02-19", 2.60)
    rec = t["recommendation"]
    assert t["wheel_put"] is True and rec["rule"] == "wheel.tested" and rec["action"] == ev.REVIEW
    assert rec["evidence"] == "practice" and t["order"] is None
    assert not {"credit.stop", "credit.breached"} & set(fired(t))
    reason = rec["reason"]
    assert "is tested (48 at or through the strike, 2.00 a share or 4.0% of it)" in reason
    assert "129 days left and 0.60 a share of extrinsic value ($60)" in reason
    assert "assignment is unlikely while time value remains, and no roll is priced beyond 60 days" in reason
    # Each figure is named for its basis: this trade's net credits, then the wheel's breakeven.
    assert (
        "make -$100 on this trade's order credits of $100 (before fees), "
        "after the $200 its puts would then owe" in reason
    )
    assert (
        "Assigned on the 50, you would hold 100 shares at 50, -$101 against the wheel breakeven of 49.01 "
        "(put strike less the cycle's net cash) at 48" in reason
    )
    assert "roll, close or assignment inside 60 days, or under 0.10 of extrinsic value" in reason


def test_no_roll_is_priced_for_a_put_that_is_only_watched():
    t = put_at("2027-02-19", 2.60)
    assert t["wheel"][0]["rolls"] == []


def test_inside_the_horizon_it_is_the_roll_close_or_assignment_it_was():
    t = put_at(INSIDE, 2.60)
    assert t["recommendation"]["action"] in (ev.ROLL_OR_CLOSE, ev.ROLL_FOR_CREDIT)
    assert "take the assignment" in fired(t)["wheel.tested"]["reason"]
    assert t["wheel"][0]["rolls"]


def test_the_horizon_is_more_than_sixty_days_not_sixty():
    assert put_at(EDGE, 2.60)["recommendation"]["action"] != ev.REVIEW  # 60 days: inside
    assert put_at(BEYOND, 2.60)["recommendation"]["action"] == ev.REVIEW  # 61 days: beyond


def test_under_the_extrinsic_floor_it_is_rolled_or_closed_however_far_out():
    # 2.05 is 0.05 of extrinsic value: early exercise is likely, so a deep put is never left unwatched.
    t = put_at("2027-02-19", 2.05)
    assert t["recommendation"]["action"] != ev.REVIEW
    assert "take the assignment" in fired(t)["wheel.tested"]["reason"]


def test_at_the_floor_exactly_it_is_still_a_review():
    assert put_at("2027-02-19", 2.10)["recommendation"]["action"] == ev.REVIEW


def test_the_horizon_and_the_floor_are_the_rulesets():
    wider = {**WHEEL, "wheel": {"tested": {"review_beyond_days": 200}}}
    assert put_at("2027-02-19", 2.60, wider)["recommendation"]["action"] != ev.REVIEW
    firmer = {**WHEEL, "wheel": {"tested": {"review_min_extrinsic": 0.80}}}
    assert put_at("2027-02-19", 2.60, firmer)["recommendation"]["action"] != ev.REVIEW


def test_a_put_not_marked_a_wheel_keeps_its_stop_and_breached_close():
    t = put_at("2027-02-19", 2.60, overrides=None)
    assert t["wheel_put"] is False and "wheel.tested" not in {
        r for r, f in fired(t).items() if f["action"] == ev.REVIEW
    }
    assert {"credit.stop", "credit.breached"} & set(fired(t))


def test_the_rule_can_be_switched_off():
    off = {**WHEEL, "positions": {"XYZ": {"intent": "wheel", "disable": ["wheel.tested"]}}}
    assert "wheel.tested" not in fired(put_at("2027-02-19", 2.60, off))


def test_the_oversize_review_still_fires_beside_it():
    # Delta and size are the exposure whatever the days left: the budget rule stays.
    t = put_at("2027-02-19", 2.60, {**WHEEL, "risk": {"oversize_multiple": 0.5}})
    names = fired(t)
    assert {"risk.oversize", "wheel.tested"} <= set(names)
    assert names["wheel.tested"]["action"] == ev.REVIEW


def two_puts(first, second, mid=2.60):
    a, b = occ(first, "P", 50), occ(second, "P", 50)
    history = [
        tx(1, "2026-10-01T15:00:00Z", "Sell to Open", a, 1, 1.00),
        tx(1, "2026-10-01T15:00:00Z", "Sell to Open", b, 1, 1.00),
    ]
    legs = [opt_leg(a, -1, mid, 1.00), opt_leg(b, -1, mid, 1.00)]
    return trade_of(evaluate(history, legs, SPOT, income=INCOME, overrides=WHEEL), a)


def test_one_put_that_cannot_wait_rolls_the_whole_trade_and_none_goes_unmentioned():
    t = two_puts("2026-12-01", "2027-02-19")
    assert len(t["legs"]) == 2 and t["recommendation"]["action"] in (ev.ROLL_OR_CLOSE, ev.ROLL_FOR_CREDIT)
    reason = fired(t)["wheel.tested"]["reason"]
    assert "2026-12-01 50P is tested" in reason and "2027-02-19 50P is tested" in reason
    assert len(t["wheel"][0]["rolls"]) == 2 and "watching" not in t["wheel"][0]


def test_two_puts_that_can_both_wait_are_one_review_with_the_trades_own_figure():
    t = two_puts("2027-01-15", "2027-02-19")
    # The two contracts also trip the sizing review, which ranks first by rule order: this is the wheel's own.
    rec = fired(t)["wheel.tested"]
    assert rec["action"] == ev.REVIEW and not t["order"]
    assert "2027-01-15 50P is tested" in rec["reason"] and "2027-02-19 50P is tested" in rec["reason"]
    # Both puts owe $200 at expiry against the $200 they took in: the figure is the trade's, said once.
    assert rec["reason"].count("Held to expiry") == 1
    assert "make -$200 on this trade's order credits of $200 (before fees), after the $400" in rec["reason"]
    assert [w["leg"] for w in t["wheel"][0]["watching"]] == ["2027-01-15 50P", "2027-02-19 50P"]


def watch_inputs(basis, cost=-100.0):
    leg = {"quantity": -1, "strike": 50.0, "right": "P", "label": "x"}
    row = {"leg": "-1 2027-02-19 50P", "spot": 48.0, "dte": 129, "extrinsic": 0.60}
    trade = {"cost": cost, "legs": [leg]}
    cycle = {"breakeven": {"price": 49.0, "basis": basis}}
    cfg = {"review_beyond_days": 60, "review_min_extrinsic": 0.10}
    return trade, cycle, [(leg, row)], cfg


def test_the_assignment_is_said_only_against_a_breakeven_of_the_puts_own_shares():
    text = ev._watch_text(*watch_inputs("put strike less net cash"))
    assert "Assigned on the 50" in text and "put strike less the cycle's net cash" in text
    # With shares already held the breakeven is their basis: the put's new shares are not measured against it.
    assert "Assigned" not in ev._watch_text(*watch_inputs("wheel basis"))


def test_a_loss_or_gain_that_rounds_to_nothing_is_not_signed():
    # The put owes $200 at expiry: credits of $200.40 or $199.60 leave +-$0.40, which reads as $0 either way.
    for cost in (-200.40, -199.60):
        text = ev._watch_text(*watch_inputs("wheel basis", cost=cost))
        assert "would make $0 on this trade's order credits" in text and "-$0" not in text


def test_only_a_wheel_put_with_known_time_value_can_wait():
    cfg = {"review_beyond_days": 60, "review_min_extrinsic": 0.10}
    row = {"right": "P", "dte": 129, "extrinsic": 0.60}
    wheel = {"wheel_put": True}
    assert ev._tested_put_can_wait(wheel, row, cfg) is True
    assert ev._tested_put_can_wait({"wheel_put": False}, row, cfg) is False
    assert ev._tested_put_can_wait(wheel, {**row, "right": "C"}, cfg) is False
    assert ev._tested_put_can_wait(wheel, {**row, "extrinsic": None}, cfg) is False
    assert ev._tested_put_can_wait(wheel, {**row, "dte": None}, cfg) is False
    assert ev._tested_put_can_wait(wheel, {**row, "dte": 60}, cfg) is False
    assert ev._tested_put_can_wait(wheel, {**row, "extrinsic": 0.099}, cfg) is False
