"""calendars is retired (2026-11-21, research item 38): the shipped example runs no arm. The
engine's built-in arms default to on when a config does not mention them, so the example switches
each one off explicitly."""

from __future__ import annotations

import json
from pathlib import Path

from cherrypick.calendars import paper_loop

EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.json"


def _example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_the_shipped_example_runs_no_arm_because_the_module_is_retired():
    config = _example()
    arms, advised = paper_loop.session_books(config, "2026-10-05")
    assert arms == [] and advised == {}
    assert config["books"]["control"]["_retired"] == "2026-11-21"
    assert paper_loop.friday_books(config) == [] and not config["friday_entry"]["enabled"]


def test_reinstating_control_alone_runs_only_control():
    config = _example()
    config["books"]["control"]["enabled"] = True
    arms, advised = paper_loop.session_books(config, "2026-10-05")
    assert arms == ["control"] and advised == {}
