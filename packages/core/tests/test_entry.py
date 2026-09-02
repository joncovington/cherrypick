"""Tests for cherrypick.core.entry — the shared cadence + leg-sign primitives. Pure functions."""

from datetime import datetime

from cherrypick.core import entry


def _t(hh, mm, ss=0):
    return datetime(2026, 8, 11, hh, mm, ss)


# --------------------------------------------------------------------------- cadence


def test_first_entry_of_the_day_is_always_allowed():
    allowed, remaining = entry.entry_allowed(None, _t(10, 0), 360)
    assert allowed is True
    assert remaining == 0


def test_spacing_of_zero_disables_the_gate():
    allowed, remaining = entry.entry_allowed(_t(10, 0), _t(10, 0, 1), 0)
    assert allowed is True
    assert remaining == 0


def test_blocks_inside_the_window_and_reports_whole_seconds_remaining():
    allowed, remaining = entry.entry_allowed(_t(10, 0), _t(10, 2), 360)
    assert allowed is False
    assert remaining == 240


def test_allows_exactly_at_the_boundary():
    # 6 minutes to the second is eligible -- the gate is "at least this far apart", not "more than".
    allowed, remaining = entry.entry_allowed(_t(10, 0), _t(10, 6), 360)
    assert allowed is True
    assert remaining == 0


def test_remaining_seconds_round_up_so_sleeping_that_long_wakes_eligible():
    allowed, remaining = entry.entry_allowed(_t(10, 0), _t(10, 5, 59), 360)
    assert allowed is False
    # 1.0s left exactly; a caller sleeping `remaining` must land at or past the boundary.
    assert remaining == 1


def test_future_last_fill_blocks_rather_than_allows():
    # Clock skew fails closed: a refused entry is recoverable next tick, an admitted one is not.
    allowed, remaining = entry.entry_allowed(_t(10, 30), _t(10, 0), 360)
    assert allowed is False
    assert remaining > 0


def test_next_eligible_is_the_wall_clock_instant():
    assert entry.next_eligible(_t(10, 0), 360) == _t(10, 6)
    assert entry.next_eligible(None, 360) is None
    assert entry.next_eligible(_t(10, 0), 0) is None


# --------------------------------------------------------------------------- sign rule

E = "2026-08-11"


def test_no_open_legs_never_conflicts():
    assert entry.sign_conflict([], [(E, "P", 7000.0, -1), (E, "P", 7005.0, 1)]) is None


def test_long_against_open_short_at_the_same_strike_is_refused():
    assert entry.sign_conflict([(E, "P", 7000.0, -1)], [(E, "P", 7000.0, 1)]) == (E, "P", 7000.0)


def test_short_against_open_long_at_the_same_strike_is_refused():
    assert entry.sign_conflict([(E, "P", 7000.0, 1)], [(E, "P", 7000.0, -1)]) == (E, "P", 7000.0)


def test_longs_stack_with_longs():
    # The `+2` in `+1 -2 +2 -2 +1`: two flies sharing a wing, not a broken structure.
    assert entry.sign_conflict([(E, "P", 7005.0, 1)], [(E, "P", 7005.0, 1)]) is None


def test_shorts_stack_with_shorts():
    # A fly's completion deliberately doubles the existing short into the -2 centre.
    assert entry.sign_conflict([(E, "P", 7000.0, -1)], [(E, "P", 7000.0, -1)]) is None


def test_two_flies_sharing_a_wing_do_not_conflict():
    fly_a = [(E, "P", 6990.0, 1), (E, "P", 6995.0, -1), (E, "P", 6995.0, -1), (E, "P", 7000.0, 1)]
    fly_b = [(E, "P", 7000.0, 1), (E, "P", 7005.0, -1), (E, "P", 7005.0, -1), (E, "P", 7010.0, 1)]
    assert entry.sign_conflict(fly_a, fly_b) is None


OPEN_IC = [(E, "P", 6890.0, 1), (E, "P", 6900.0, -1), (E, "C", 7100.0, -1), (E, "C", 7110.0, 1)]


def test_a_condor_nested_inside_an_open_one_is_allowed():
    # Shorts 6900/7100, longs 6890/7110 open; a tighter condor inside it touches no contract.
    inner = [(E, "P", 6940.0, 1), (E, "P", 6950.0, -1), (E, "C", 7050.0, -1), (E, "C", 7060.0, 1)]
    assert entry.sign_conflict(OPEN_IC, inner) is None


def test_a_new_short_on_an_open_long_wing_is_the_meic_case_that_must_refuse():
    # The open IC's long put sits at 6890; a wider condor wanting to SELL that put nets it out.
    wider = [(E, "P", 6880.0, 1), (E, "P", 6890.0, -1), (E, "C", 7110.0, -1), (E, "C", 7120.0, 1)]
    assert entry.sign_conflict(OPEN_IC, wider) == (E, "P", 6890.0)


def test_opposite_option_types_at_one_strike_never_net():
    # A short PUT and a long CALL at 6900 are different contracts. Refusing this would block
    # ordinary structures for no reason -- the failure mode that made `right` part of the key.
    assert entry.sign_conflict(OPEN_IC, [(E, "C", 6900.0, 1)]) is None
    assert entry.sign_conflict(OPEN_IC, [(E, "P", 7100.0, 1)]) is None


def test_option_type_spellings_normalize_so_the_rule_still_binds():
    # MEIC says "put"/"call", flies says PUT/CALL, broker payloads say "P"/"C".
    assert entry.sign_conflict([(E, "put", 7000.0, -1)], [(E, "P", 7000.0, 1)]) == (E, "P", 7000.0)
    assert entry.sign_conflict([(E, "CALL", 7000.0, -1)], [(E, "c", 7000.0, 1)]) == (E, "C", 7000.0)


def test_same_strike_different_expiry_does_not_conflict():
    assert entry.sign_conflict([("2026-08-11", "P", 7000.0, -1)], [("2026-08-12", "P", 7000.0, 1)]) is None


def test_zero_sign_legs_are_ignored_on_both_sides():
    assert entry.sign_conflict([(E, "P", 7000.0, 0)], [(E, "P", 7000.0, 1)]) is None
    assert entry.sign_conflict([(E, "P", 7000.0, -1)], [(E, "P", 7000.0, 0)]) is None


def test_strikes_compare_numerically_not_by_string():
    assert entry.sign_conflict([(E, "P", 7000, -1)], [(E, "P", 7000.0, 1)]) == (E, "P", 7000.0)


# --------------------------------------------------------------------------- structure key


def test_structure_key_distinguishes_centre_and_width():
    a = entry.structure_key("SPX", E, "put", 7000, 5)
    assert a == entry.structure_key("spx", E, "put", 7000.0, 5.0)
    assert a != entry.structure_key("SPX", E, "put", 7005, 5)
    assert a != entry.structure_key("SPX", E, "put", 7000, 10)
    assert a != entry.structure_key("SPX", E, "call", 7000, 5)
    assert a != entry.structure_key("SPX", E, "put", 7000, 5, far_width=10)
