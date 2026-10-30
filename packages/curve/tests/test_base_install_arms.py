"""A base install runs only the control arm. From 2026-10-30 an arm the config does not mention
stays off (control excepted, `engine.DEFAULT_ENABLED`); before then it ran on `defaults`, so the
shipped example still switches each one off explicitly."""

from __future__ import annotations

import json
from pathlib import Path

from cherrypick.curve import paper_loop

EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.json"


def _example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _registry(config: dict) -> dict:
    return next(config[k] for k in ("arms", "books", "profiles") if k in config)


def test_the_shipped_example_runs_only_control():
    arms, advised = paper_loop.session_books(_example(), "2026-10-05")
    assert arms == ["control"] and advised == {}


def test_an_arm_the_config_predates_stays_off():
    """A config kept from before `near` (2026-10-06) has no block for it. Run on `defaults` it was
    control's trades under another name; now it does not enter."""
    config = _example()
    registry = _registry(config)
    for arm in ("noflip", "hook", "near"):
        registry[arm]["enabled"] = True
    del registry["near"]
    arms, _ = paper_loop.session_books(config, "2026-10-30")
    assert arms == ["control", "noflip", "hook"]


def test_control_with_no_block_still_runs():
    config = _example()
    del _registry(config)["control"]
    arms, _ = paper_loop.session_books(config, "2026-10-30")
    assert arms == ["control"]
