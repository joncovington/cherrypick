"""Item 48's arm registry (scripts/meic_stop_grid_item48.py): every arm is the live rule with its stop
changed and nothing else. Invented registries only."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location("meic_stop_grid_item48", SCRIPTS / "meic_stop_grid_item48.py")
st = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(st)

PM200 = {
    "_note": "dropped",
    "symbols": ["SPX"],
    "enabled": False,
    "stop_trigger_ratio": 2.0,
    "per_side_stop_management": True,
    "short_delta_target": 0.16,
}
STOP_KEYS = {"stop_trigger_ratio", "stop_resting", "stop_resting_fill", "per_side_stop_management"}


def registry():
    return {"profiles": {"pm-200": dict(PM200), "control": {"enabled": True}}}


def test_every_grid_arm_is_the_live_rule_with_only_its_stop_changed():
    profiles = st.study_registry(registry())["profiles"]
    live = profiles[st.LIVE]
    assert live["enabled"] is True and live["paper_entry_window_start"] == "14:00"
    assert live["entry_window_end"] == "14:30" and "_note" not in live
    arms = st.grid_arms()
    assert len(arms) == 12 and "loop-200" not in arms and st.HOLD in arms
    for name in arms:
        arm = profiles[name]
        changed = {k for k in set(arm) | set(live) if arm.get(k) != live.get(k)}
        assert changed <= STOP_KEYS, (name, changed)
    assert profiles["loop-190"]["stop_trigger_ratio"] == 1.9 and not profiles["loop-190"].get("stop_resting")
    assert (
        profiles["rest-190"]["stop_resting"] is True and profiles["rest-190"]["stop_resting_fill"] == "tick"
    )
    assert profiles["restbest-220"]["stop_resting_fill"] == "trigger"
    assert profiles["rest-200"]["stop_trigger_ratio"] == 2.0
    assert profiles[st.HOLD]["per_side_stop_management"] is False
    assert profiles["control"] == {"enabled": True}, "the rest of the registry is left alone"


def test_the_grid_refuses_a_registry_whose_live_arm_moved():
    reg = registry()
    reg["profiles"]["pm-200"]["stop_trigger_ratio"] = 0.95
    with pytest.raises(SystemExit):
        st.study_registry(reg)
