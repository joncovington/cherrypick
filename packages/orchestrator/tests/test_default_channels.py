"""Push notifications are off on a base install: every channel list the shipped template and the code
fallbacks give is log-only. Desktop, Slack and Discord are each a person's deliberate choice; the
console's own on-screen toasts need no setting at all."""

from __future__ import annotations

import json
from pathlib import Path

from cherrypick.orchestrator import config as cfgmod

TEMPLATE = Path(__file__).resolve().parents[1] / "config.example.json"


def _channel_lists(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            if key.endswith("channels") and isinstance(value, list):
                yield f"{path}/{key}", value
            yield from _channel_lists(value, f"{path}/{key}")


def test_the_template_ships_every_channel_list_log_only():
    lists = dict(_channel_lists(json.loads(TEMPLATE.read_text(encoding="utf-8"))))
    assert lists, "no channel lists found -- the walk is broken, not the template clean"
    assert {path: value for path, value in lists.items() if value != ["log"]} == {}


def test_the_code_fallbacks_are_log_only_too():
    assert cfgmod.desk_notify_settings({})["channels"] == ["log"]
    assert cfgmod.status_digest_settings({})["channels"] == ["log"]
