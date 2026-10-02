"""The `vol-floor` arm: control plus no entry while the ATM straddle is under a fraction of spot."""

import pytest
from test_engine import _machine_config, params, q, snapshot

from cherrypick.flies import db as dbmod
from cherrypick.flies import engine, paper_loop

# The fixture's ATM pair: 6000 put and call both 5.00/5.40, mid 5.20 -> straddle 10.40, 0.00173 of spot.
RATIO = 10.40 / 6000.0


def test_the_floor_refuses_below_and_admits_at_or_above():
    snap = snapshot()
    assert engine.low_vol_refusal(snap, params(min_entry_straddle_pct=0.0022)) == "straddle_below_floor"
    assert engine.low_vol_refusal(snap, params(min_entry_straddle_pct=0.0015)) is None
    assert engine.low_vol_refusal(snap, params(min_entry_straddle_pct=round(RATIO, 6) - 1e-6)) is None


def test_the_floor_is_off_unless_an_arm_sets_it():
    assert engine.low_vol_refusal(snapshot(), params()) is None
    assert engine.low_vol_refusal(snapshot(), params(min_entry_straddle_pct=None)) is None


def test_an_unquoted_atm_pair_fails_open():
    """No ATM pair means no straddle to read; the entry's own legs refuse it if they are missing too."""
    snap = snapshot(calls={6005: q(2.6, 3.0)})
    assert engine.low_vol_refusal(snap, params(min_entry_straddle_pct=0.0022)) is None


def test_the_entry_is_refused_with_the_floors_reason_and_control_is_untouched():
    """Shown to fail without the gate: the same market enters for control and is refused for an
    arm that sets the floor, with a reason the attempts ledger records."""
    snap = snapshot(underlying_price=5998.0)
    enter, _, _ = engine.evaluate_credit_spread_entry(snap, params(), [])
    assert enter
    enter, reason, plan = engine.evaluate_credit_spread_entry(snap, params(min_entry_straddle_pct=0.0022), [])
    assert not enter and reason == "straddle_below_floor" and plan is None


def test_vol_floor_is_registered():
    assert "vol-floor" in engine.ARMS


def test_this_machines_vol_floor_differs_from_control_in_one_variable():
    """One variable vs control, or the comparison measures two things at once."""
    arms = _machine_config()["arms"]
    if "vol-floor" not in arms:
        pytest.skip("this machine does not run the vol-floor arm")

    def live_keys(d):
        return {k: v for k, v in d.items() if not k.startswith("_")}

    vf, ctl = live_keys(arms["vol-floor"]), live_keys(arms["control"])
    assert vf.pop("min_entry_straddle_pct") == pytest.approx(0.0022)
    assert vf == ctl


def test_the_arm_is_journaled_once_when_configured(tmp_path):
    conn = dbmod.connect(str(tmp_path / "paper_trades.db"))
    cfg = {"arms": {"vol-floor": {"enabled": True, "min_entry_straddle_pct": 0.0022}}}
    paper_loop._note_vol_floor_arm(conn, cfg)
    paper_loop._note_vol_floor_arm(conn, cfg)
    rows = [r for r in dbmod.measurement_breaks(conn) if r["scope"] == "vol-floor"]
    assert len(rows) == 1 and rows[0]["kind"] == "arm_added" and rows[0]["break_date"] == "2026-10-05"
    other = dbmod.connect(str(tmp_path / "other.db"))
    paper_loop._note_vol_floor_arm(other, {"arms": {"control": {}}})
    assert not [r for r in dbmod.measurement_breaks(other) if r["scope"] == "vol-floor"]
