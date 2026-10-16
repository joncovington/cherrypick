"""The `window-1230` arm (declared 2026-10-16, from 2026-10-19): control with one variable, its entry
window, which starts at 12:30 instead of 10:00 and ends with control's at 14:30."""

import pytest
from test_engine import _machine_config

from cherrypick.flies import analytics, engine, paper_loop
from cherrypick.flies import db as dbmod

WINDOW = [["12:30", "14:30"]]


def test_the_window_admits_12_30_through_14_30():
    assert engine.in_entry_window(750, WINDOW)[0]
    assert engine.in_entry_window(870, WINDOW)[0]
    assert not engine.in_entry_window(749, WINDOW)[0]
    assert not engine.in_entry_window(871, WINDOW)[0]


def test_it_is_registered_and_read_as_a_gate_arm():
    assert "window-1230" in engine.ARMS
    assert analytics.GATE_ARMS["window-1230"]["refusals"] == ("outside_entry_window",)


def _live_keys(d):
    return {k: v for k, v in d.items() if not k.startswith("_")}


def test_this_machines_window_1230_differs_from_control_in_one_variable():
    arms = _machine_config()["arms"]
    if "window-1230" not in arms:
        pytest.skip("this machine does not run the window-1230 arm yet (from 2026-10-19)")
    w, ctl = _live_keys(arms["window-1230"]), _live_keys(arms["control"])
    assert w.pop("entry_windows") == WINDOW
    # the window's end is control's: only the start moved
    assert ctl.pop("entry_windows", None)[-1][1] == WINDOW[-1][1]
    assert w == ctl


def test_it_is_journaled_once_when_configured(tmp_path):
    cfg = {"enabled": True, "entry_windows": WINDOW}
    conn = dbmod.connect(str(tmp_path / "paper_trades.db"))
    paper_loop._note_window_1230_arm(conn, {"arms": {"window-1230": cfg}})
    paper_loop._note_window_1230_arm(conn, {"arms": {"window-1230": cfg}})
    rows = [r for r in dbmod.measurement_breaks(conn) if r["scope"] == "window-1230"]
    assert len(rows) == 1 and rows[0]["kind"] == "arm_added" and rows[0]["break_date"] == "2026-10-19"
    other = dbmod.connect(str(tmp_path / "other.db"))
    paper_loop._note_window_1230_arm(other, {"arms": {"control": {}}})
    paper_loop._note_window_1230_arm(other, {"arms": {"window-1230": {**cfg, "enabled": False}}})
    assert not [r for r in dbmod.measurement_breaks(other) if r["scope"] == "window-1230"]
