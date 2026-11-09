"""A base install runs only the control arm. The engine's built-in arms default to on when a
config does not mention them, so the shipped example must switch each one off explicitly."""

from __future__ import annotations

import json
from pathlib import Path

from cherrypick.bwb import paper_loop

EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.json"


def test_the_shipped_example_runs_no_arm_because_the_module_is_retired():
    """bwb is retired (2026-11-08): every arm ships off, each with a `_retired` note or a dated one."""
    config = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    arms, advised = paper_loop.session_books(config, "2026-10-05")
    assert arms == [] and advised == {}
    assert config["books"]["control"]["_retired"] == "2026-11-08"


def test_reinstating_control_alone_runs_only_control():
    config = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    config["books"]["control"]["enabled"] = True
    arms, advised = paper_loop.session_books(config, "2026-10-05")
    assert arms == ["control"] and advised == {}
