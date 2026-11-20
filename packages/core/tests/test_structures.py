"""cherrypick.core.structures: the shared straddle-based expected move."""

from cherrypick.core import structures


def test_expected_move_applies_the_standard_correction():
    # The docstring's own example: a $14.00 straddle -> $11.90 expected move.
    assert structures.expected_move(7.25, 6.75) == 0.85 * 14.00


def test_expected_move_factor_is_overridable():
    assert structures.expected_move(5.0, 5.0, factor=1.0) == 10.0


def test_default_factor_is_the_module_constant():
    assert structures.STRADDLE_TO_EM_FACTOR == 0.85


def test_tick_rounding_rounds_toward_the_house_in_integer_cents():
    """meic's and flies' copies, now one: a credit floors, a debit ceils, on-tick is a fixed point,
    and the arithmetic is in cents so binary float noise cannot land a limit off-tick."""
    from cherrypick.core.structures import TICK, tick_ceil, tick_floor

    assert TICK == 0.05
    assert tick_floor(1.07) == 1.05 and tick_ceil(1.07) == 1.10
    assert tick_floor(1.05) == 1.05 and tick_ceil(1.05) == 1.05
    assert tick_floor(0.04) == 0.0 and tick_ceil(0.01) == 0.05  # whole cents first, then the tick
    assert tick_floor(4.5 - 0.05) == 4.45  # 4.449999... in binary, still on the tick
    assert tick_floor(1.07, tick=0.10) == 1.0 and tick_ceil(1.07, tick=0.10) == 1.1


def _leg(name, right, side, **strike):
    return {"name": name, "right": right, "side": side, "strike": strike}


def test_a_rule_enters_for_a_credit_when_every_bought_leg_is_a_wing_of_a_sold_one():
    condor = {
        "legs": [
            _leg("sp", "P", -1, rule="delta", target=0.16),
            _leg("lp", "P", 1, rule="from_leg", leg="sp", points=-10),
            _leg("sc", "C", -1, rule="delta", target=0.16),
            _leg("lc", "C", 1, rule="from_leg", leg="sc", points=10),
        ]
    }
    assert structures.entry_kind(condor) == "credit"
    assert structures.entry_kind({"legs": [_leg("sp", "P", -1, rule="delta", target=0.3)]}) == "credit"
    # A "wing" placed inward is not a wing: the rule no longer says which way the money goes.
    inward = {
        "legs": [
            _leg("sp", "P", -1, rule="delta", target=0.3),
            _leg("lp", "P", 1, rule="from_leg", leg="sp", points=10),
        ]
    }
    assert structures.entry_kind(inward) is None


def test_a_rule_enters_for_a_debit_when_every_sold_leg_is_a_wing_of_a_bought_one():
    debit_call = {
        "legs": [
            _leg("lc", "C", 1, rule="spot", points=0),
            _leg("sc", "C", -1, rule="from_leg", leg="lc", points=10),
        ]
    }
    assert structures.entry_kind(debit_call) == "debit"
    assert structures.entry_kind({"legs": [_leg("lp", "P", 2, rule="delta", target=0.3)]}) == "debit"


def test_a_rule_that_does_not_say_is_neither():
    calendar = {
        "legs": [
            _leg("near", "C", -1, rule="spot", points=0),
            {**_leg("far", "C", 1, rule="same_as", leg="near"), "expiry": "far"},
        ]
    }
    assert structures.entry_kind(calendar) is None
    assert structures.entry_kind({"legs": []}) is None
