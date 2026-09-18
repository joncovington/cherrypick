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
