"""The `range-high` arm: control plus no entry at the top of a narrow session range (2026-10-09)."""

import pytest
from test_engine import _machine_config, params, snapshot

from cherrypick.flies import db as dbmod
from cherrypick.flies import engine, paper_loop

ON = {"range_high_pct": 0.9, "range_high_max_points": 30.0}


def at(spot, low, high):
    return snapshot(underlying_price=spot, session={"day_open": low, "day_high": high, "day_low": low})


def test_a_new_high_of_a_quiet_session_is_refused():
    assert engine.range_high_refusal(at(6000.0, 5980.0, 6001.0), params(**ON)) == "near_session_high"


def test_the_boundaries_are_inclusive_on_position_and_exclusive_on_range():
    assert engine.range_high_refusal(at(5998.0, 5978.0, 6000.0), params(**ON)) == "near_session_high"  # 0.909
    assert engine.range_high_refusal(at(5998.0, 5980.0, 6000.0), params(**ON)) == "near_session_high"  # 0.9
    assert engine.range_high_refusal(at(5997.0, 5980.0, 6000.0), params(**ON)) is None  # 0.85
    assert engine.range_high_refusal(at(6000.0, 5970.0, 6000.0), params(**ON)) is None  # range 30: travelled


def test_a_wide_range_and_the_low_end_are_admitted():
    assert engine.range_high_refusal(at(6000.0, 5940.0, 6001.0), params(**ON)) is None
    assert engine.range_high_refusal(at(5981.0, 5980.0, 6001.0), params(**ON)) is None


def test_the_gate_is_off_unless_an_arm_sets_it():
    assert engine.range_high_refusal(at(6000.0, 5980.0, 6001.0), params()) is None
    assert engine.range_high_refusal(at(6000.0, 5980.0, 6001.0), params(range_high_pct=None)) is None


def test_no_range_to_read_fails_open():
    assert engine.range_high_refusal(snapshot(), params(**ON)) is None  # no session row
    assert engine.range_high_refusal(at(6000.0, 6000.0, 6000.0), params(**ON)) is None  # first tick
    snap = snapshot(session={"day_open": 5990.0, "day_high": 6001.0})  # low missing
    assert engine.range_high_refusal(snap, params(**ON)) is None


def test_the_entry_is_refused_with_the_gates_reason_and_control_is_untouched():
    """Shown to fail without the gate: the same market enters for control and is refused for an
    arm that sets it, with a reason the attempts ledger records."""
    snap = at(5998.0, 5980.0, 5999.0)
    enter, _, _ = engine.evaluate_credit_spread_entry(snap, params(), [])
    assert enter
    enter, reason, plan = engine.evaluate_credit_spread_entry(snap, params(**ON), [])
    assert not enter and reason == "near_session_high" and plan is None


def test_range_high_is_registered():
    assert "range-high" in engine.ARMS


def test_this_machines_range_high_differs_from_control_in_one_variable():
    """One variable vs control (the gate's two declared settings), or the comparison measures two
    things at once."""
    arms = _machine_config()["arms"]
    if "range-high" not in arms:
        pytest.skip("this machine does not run the range-high arm")

    def live_keys(d):
        return {k: v for k, v in d.items() if not k.startswith("_")}

    rh, ctl = live_keys(arms["range-high"]), live_keys(arms["control"])
    assert rh.pop("range_high_pct") == pytest.approx(0.9)
    assert rh.pop("range_high_max_points") == pytest.approx(30.0)
    assert rh == ctl


def test_the_arm_is_journaled_once_when_configured(tmp_path):
    conn = dbmod.connect(str(tmp_path / "paper_trades.db"))
    cfg = {"arms": {"range-high": {"enabled": True, **ON}}}
    paper_loop._note_range_high_arm(conn, cfg)
    paper_loop._note_range_high_arm(conn, cfg)
    rows = [r for r in dbmod.measurement_breaks(conn) if r["scope"] == "range-high"]
    assert len(rows) == 1 and rows[0]["kind"] == "arm_added" and rows[0]["break_date"] == "2026-10-12"
    other = dbmod.connect(str(tmp_path / "other.db"))
    paper_loop._note_range_high_arm(other, {"arms": {"control": {}}})
    assert not [r for r in dbmod.measurement_breaks(other) if r["scope"] == "range-high"]
