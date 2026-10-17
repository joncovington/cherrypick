"""An advised arm in a replay: a config-defined `advised:<name>` (a base's params with an overlay, as
paper builds one) runs in a replay; live paper's roster is untouched by it (packages/backtest)."""

from __future__ import annotations

import pytest

from cherrypick.flies import engine, paper_loop, replay


def _config():
    return {
        "defaults": {"strike_increment": 5},
        "arms": {
            "control": {"enabled": True},
            "range-high": {"enabled": False},
            "advised:wider": {"enabled": True, "wing_width_strikes": 4},
            "advised:off": {"enabled": False, "wing_width_strikes": 2},
            "_note": "a comment entry",
        },
    }


def test_a_replay_runs_config_defined_advised_arms_and_only_enabled_ones():
    arms = paper_loop.replay_arms(_config())
    assert "control" in arms and "advised:wider" in arms
    assert "advised:off" not in arms and "range-high" not in arms


def test_an_advised_arm_resolves_through_the_ordinary_params_path():
    params = engine.merged_params(_config(), "advised:wider")
    assert params["wing_width"] == 20 and params["arm"] == "advised:wider"


def test_replay_config_accepts_a_defined_advised_arm_and_refuses_an_undefined_one():
    cfg = replay.replay_config(_config(), ["control", "advised:wider"])
    assert cfg["arms"]["advised:wider"]["enabled"] is True
    assert cfg["arms"]["range-high"]["enabled"] is False
    with pytest.raises(ValueError, match="unknown arm"):
        replay.replay_config(_config(), ["advised:nowhere"])
    with pytest.raises(ValueError, match="unknown arm"):
        replay.replay_config(_config(), ["not-an-arm"])
