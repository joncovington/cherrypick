"""cherrypick.core.structures: the shared straddle-based expected move."""

from cherrypick.core import structures


def test_expected_move_applies_the_standard_correction():
    # The docstring's own example: a $14.00 straddle -> $11.90 expected move.
    assert structures.expected_move(7.25, 6.75) == 0.85 * 14.00


def test_expected_move_factor_is_overridable():
    assert structures.expected_move(5.0, 5.0, factor=1.0) == 10.0


def test_default_factor_is_the_module_constant():
    assert structures.STRADDLE_TO_EM_FACTOR == 0.85
