import pytest

from cherrypick.contango import regime

CONTROL = {"enter_below": 0.97, "exit_at_or_above": 0.97}
FLIPEXIT = {"enter_below": 0.97, "exit_at_or_above": 1.0}


@pytest.mark.parametrize(
    ("ratio", "current", "want"),
    [(0.90, None, "risk"), (0.97, "risk", "cash"), (0.99, "risk", "cash"), (0.96, "cash", "risk")],
)
def test_control_is_a_plain_switch(ratio, current, want):
    assert regime.target_state(ratio, current, CONTROL) == want


def test_flipexit_holds_through_the_band_either_way():
    assert regime.target_state(0.98, "risk", FLIPEXIT) == "risk"
    assert regime.target_state(0.98, "cash", FLIPEXIT) == "cash"
    assert regime.target_state(1.0, "risk", FLIPEXIT) == "cash"


def test_an_arm_born_in_the_band_waits_in_cash():
    assert regime.target_state(0.98, None, FLIPEXIT) == "cash"


def test_an_inverted_pair_is_refused():
    with pytest.raises(ValueError):
        regime.target_state(0.9, None, {"enter_below": 1.0, "exit_at_or_above": 0.97})


def test_a_stale_print_refuses_rather_than_reads():
    fresh, stale = {"value": 15.0, "age_seconds": 5}, {"value": 18.0, "age_seconds": 900}
    assert regime.reading(fresh, stale)["reason"] == "stale_vix3m"
    assert regime.reading(None, fresh)["reason"] == "no_vix_quote"
    got = regime.reading(fresh, {"value": 18.0, "age_seconds": 5})
    assert got["ratio"] == pytest.approx(15 / 18, abs=1e-6)
