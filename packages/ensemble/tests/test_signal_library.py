"""Arms that point at a library signal: the config resolves the reference to that version's spec before
validating, the run card's list names the versions, and a reference the library cannot answer stops the
load."""

from __future__ import annotations

import copy

import pytest
from cherrypick.core import signallib

from cherrypick.ensemble import arms
from cherrypick.ensemble import config as config_mod

CROSS = {"kind": "cross", "a": {"name": "close"}, "b": 6001.0, "direction": "above", "seed": False}


def arm_with(signal):
    arm = copy.deepcopy(arms.ARMS["iron-fly-1000"])
    arm["name"] = "crossing"
    arm["gates"] = []
    arm["entry"] = {
        "kind": "trigger",
        "trigger": {"signal": signal, "window": {"open": "09:45", "close": "11:00"}, "within": 10},
        "bar_minutes": 1,
    }
    return arm


def test_a_reference_loads_as_its_version_and_the_run_card_names_it():
    lib = signallib.empty()
    v1, _ = signallib.add(lib, "close-over", CROSS)
    v2, _ = signallib.add(lib, "close-over", {**CROSS, "b": 6005.0})
    cfg = {"arms": {"crossing": arm_with({"library": "close-over", "version": v1})}}
    loaded = config_mod.load_arms(cfg, library=lib)
    assert loaded["crossing"]["entry"]["trigger"]["signal"] == CROSS  # v1, not the newer v2
    assert config_mod.signal_versions(cfg, ["crossing", "iron-fly-1000"]) == {
        "crossing": [{"name": "close-over", "version": v1}]
    }
    cfg["arms"]["crossing"]["entry"]["trigger"]["signal"]["version"] = v2
    assert config_mod.load_arms(cfg, library=lib)["crossing"]["entry"]["trigger"]["signal"]["b"] == 6005.0


def test_the_library_file_is_read_only_when_an_arm_holds_a_reference(managed_home):
    lib_path = signallib.path()
    lib_path.parent.mkdir(parents=True, exist_ok=True)
    lib_path.write_text("not json", encoding="utf-8")
    assert "crossing" in config_mod.load_arms({"arms": {"crossing": arm_with(CROSS)}})
    with pytest.raises(ValueError):
        config_mod.load_arms({"arms": {"crossing": arm_with({"library": "x", "version": "0" * 12})}})
    lib = signallib.empty()
    v1, _ = signallib.add(lib, "close-over", CROSS)
    signallib.save(lib)
    cfg = {"arms": {"crossing": arm_with({"library": "close-over", "version": v1})}}
    assert config_mod.load_arms(cfg)["crossing"]["entry"]["trigger"]["signal"] == CROSS


def test_an_unknown_or_unpinned_reference_stops_the_load():
    lib = signallib.empty()
    signallib.add(lib, "close-over", CROSS)
    with pytest.raises(ValueError, match="names its version"):
        config_mod.load_arms({"arms": {"crossing": arm_with({"library": "close-over"})}}, library=lib)
    with pytest.raises(ValueError, match="no signal"):
        config_mod.load_arms(
            {"arms": {"crossing": arm_with({"library": "gone", "version": "a" * 12})}}, library=lib
        )
    # An arm handed straight to the engine's loader never reads a reference as a spec.
    with pytest.raises(ValueError):
        arms.load_arms({"crossing": arm_with({"library": "close-over", "version": "a" * 12})})
