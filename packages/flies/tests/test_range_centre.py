"""The `range-centre` and `window-1300` arms (declared 2026-10-14, from 2026-10-15): control plus one
variable each -- a placement gate, and a 13:00-13:59 entry window."""

import pytest
from test_engine import _machine_config, params, snapshot

from cherrypick.flies import analytics, engine, paper_loop
from cherrypick.flies import db as dbmod

ON = {"range_pos_max": 0.8, "center_above_spot_max": 1.25}


def at(spot, low, high):
    return snapshot(underlying_price=spot, session={"day_open": low, "day_high": high, "day_low": low})


# --------------------------------------------------------------------------- the placement gate
def test_spot_high_in_the_range_is_refused_on_any_range_width():
    assert engine.placement_refusal(at(6000.0, 5980.0, 6001.0), params(**ON), 6000.0) == "range_position_high"
    # range-high would admit this one (range 60 >= 30); this gate reads the position on any range
    assert engine.placement_refusal(at(6000.0, 5940.0, 6001.0), params(**ON), 6000.0) == "range_position_high"


def test_the_range_boundary_is_inclusive():
    assert (
        engine.placement_refusal(at(5996.0, 5980.0, 6000.0), params(**ON), 5995.0) == "range_position_high"
    )  # 0.8
    assert engine.placement_refusal(at(5995.0, 5980.0, 6000.0), params(**ON), 5995.0) is None  # 0.75


def test_a_centre_more_than_the_limit_above_spot_is_refused():
    low = at(5990.0, 5980.0, 6020.0)  # position 0.25: the range check passes
    assert engine.placement_refusal(low, params(**ON), 5991.5) == "center_above_spot"
    assert engine.placement_refusal(low, params(**ON), 5991.25) is None  # exactly the limit is admitted
    assert engine.placement_refusal(low, params(**ON), 5987.5) is None  # centre below spot


def test_the_gate_is_off_unless_an_arm_sets_it():
    snap = at(6000.0, 5980.0, 6001.0)
    assert engine.placement_refusal(snap, params(), 6002.5) is None
    assert (
        engine.placement_refusal(snap, params(range_pos_max=None, center_above_spot_max=None), 6002.5) is None
    )


def test_each_check_runs_alone():
    snap = at(6000.0, 5980.0, 6001.0)
    assert engine.placement_refusal(snap, params(range_pos_max=0.8), 6002.5) == "range_position_high"
    low = at(5990.0, 5980.0, 6020.0)
    assert engine.placement_refusal(low, params(center_above_spot_max=1.25), 5992.5) == "center_above_spot"


def test_no_range_to_read_fails_open_for_the_range_check_only():
    assert engine.placement_refusal(snapshot(underlying_price=6000.0), params(**ON), 6000.0) is None
    assert engine.placement_refusal(at(6000.0, 6000.0, 6000.0), params(**ON), 6000.0) is None  # first tick
    # the centre check still reads with no range
    assert (
        engine.placement_refusal(snapshot(underlying_price=6000.0), params(**ON), 6002.5)
        == "center_above_spot"
    )


def test_the_entry_is_refused_with_the_gates_reason_and_control_is_untouched():
    """Shown to fail without the gate: the same market enters for control and is refused for an arm
    that sets it, with a reason the attempts ledger records."""
    snap = at(6000.5, 5980.0, 6001.0)  # centre 6000 sits below spot: only the range check can refuse
    enter, _, _ = engine.evaluate_credit_spread_entry(snap, params(), [])
    assert enter
    enter, reason, plan = engine.evaluate_credit_spread_entry(snap, params(**ON), [])
    assert not enter and reason == "range_position_high" and plan is None


def test_an_itm_put_centre_is_refused_at_entry():
    snap = at(5998.0, 5980.0, 6020.0)  # ATM centre 6000 is 2.0 above spot: the put spread
    enter, _, _ = engine.evaluate_credit_spread_entry(snap, params(), [])
    assert enter
    enter, reason, _ = engine.evaluate_credit_spread_entry(snap, params(**ON), [])
    assert not enter and reason == "center_above_spot"


def test_the_gate_sessions_read_names_only_reasons_the_engine_returns():
    assert set(analytics.GATE_ARMS["range-centre"]["refusals"]) == {
        "range_position_high",
        "center_above_spot",
    }
    assert analytics.GATE_ARMS["window-1300"]["refusals"] == ("outside_entry_window",)


# --------------------------------------------------------------------------- the 13:00 window
def test_the_window_admits_only_the_13_00_hour():
    windows = [["13:00", "13:59"]]
    assert engine.in_entry_window(780, windows)[0]
    assert engine.in_entry_window(839, windows)[0]
    assert not engine.in_entry_window(779, windows)[0]
    assert not engine.in_entry_window(840, windows)[0]


# --------------------------------------------------------------------------- registration and journal
def test_both_arms_are_registered():
    assert "range-centre" in engine.ARMS and "window-1300" in engine.ARMS


def _live_keys(d):
    return {k: v for k, v in d.items() if not k.startswith("_")}


def test_this_machines_range_centre_differs_from_control_in_one_variable():
    arms = _machine_config()["arms"]
    if "range-centre" not in arms:
        pytest.skip("this machine does not run the range-centre arm yet (added at the 2026-10-15 boundary)")
    rc, ctl = _live_keys(arms["range-centre"]), _live_keys(arms["control"])
    assert rc.pop("range_pos_max") == pytest.approx(0.8)
    assert rc.pop("center_above_spot_max") == pytest.approx(1.25)
    assert rc == ctl


def test_this_machines_window_1300_differs_from_control_in_one_variable():
    arms = _machine_config()["arms"]
    if "window-1300" not in arms:
        pytest.skip("this machine does not run the window-1300 arm yet (added at the 2026-10-15 boundary)")
    w, ctl = _live_keys(arms["window-1300"]), _live_keys(arms["control"])
    assert w.pop("entry_windows") == [["13:00", "13:59"]]
    ctl.pop("entry_windows", None)
    assert w == ctl


@pytest.mark.parametrize(
    "name,note,cfg",
    [
        ("range-centre", "_note_range_centre_arm", {"enabled": True, **ON}),
        ("window-1300", "_note_window_1300_arm", {"enabled": True, "entry_windows": [["13:00", "13:59"]]}),
    ],
)
def test_each_arm_is_journaled_once_when_configured(tmp_path, name, note, cfg):
    conn = dbmod.connect(str(tmp_path / "paper_trades.db"))
    getattr(paper_loop, note)(conn, {"arms": {name: cfg}})
    getattr(paper_loop, note)(conn, {"arms": {name: cfg}})
    rows = [r for r in dbmod.measurement_breaks(conn) if r["scope"] == name]
    assert len(rows) == 1 and rows[0]["kind"] == "arm_added" and rows[0]["break_date"] == "2026-10-15"
    other = dbmod.connect(str(tmp_path / "other.db"))
    getattr(paper_loop, note)(other, {"arms": {"control": {}}})
    getattr(paper_loop, note)(other, {"arms": {name: {**cfg, "enabled": False}}})
    assert not [r for r in dbmod.measurement_breaks(other) if r["scope"] == name]
