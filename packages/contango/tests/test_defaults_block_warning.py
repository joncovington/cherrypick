"""The loop says so when its `defaults` block is missing (core.config.section).

Driven off this module's own example config, so the guard is checked against what the module
declares rather than a hand-kept list: the example must stay silent, and the same config with the
block removed must warn exactly once however many ticks read it."""

import json
from pathlib import Path

from cherrypick.core import config as _cfg

from cherrypick.contango import paper_loop

EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.json"


def _ticks(config, monkeypatch, n=3):
    said = []
    monkeypatch.setattr(paper_loop, "_log", said.append)
    _cfg._reset_warnings()
    for _ in range(n):
        paper_loop.enabled_arms(config)
    _cfg._reset_warnings()
    return [s for s in said if "`defaults`" in s]


def test_the_example_config_declares_its_defaults_block(monkeypatch):
    config = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert "defaults" in config
    assert _ticks(config, monkeypatch) == []


def test_a_config_without_the_block_warns_once(monkeypatch):
    config = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    del config["defaults"]
    warned = _ticks(config, monkeypatch)
    assert len(warned) == 1 and "contango" in warned[0]
