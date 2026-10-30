"""The loop says so when its `defaults` block is missing (core.config.section).

Driven off this module's own example config, so the guard is checked against what the module
declares rather than a hand-kept list: the example must stay silent, and the same config with the
block removed must warn exactly once however many ticks read it."""

import json
from pathlib import Path

from cherrypick.core import config as _cfg

from cherrypick.curve import paper_loop

EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.json"


def _ticks(config, monkeypatch, n=3):
    said = []
    monkeypatch.setattr(paper_loop, "_log", said.append)
    _cfg._reset_warnings()
    for _ in range(n):
        paper_loop.session_books(config, "2026-10-26")
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
    assert len(warned) == 1 and "curve" in warned[0]


def _undeclared_warnings(config, monkeypatch, n=3):
    said = []
    monkeypatch.setattr(paper_loop, "_log", said.append)
    _cfg._reset_warnings()
    for _ in range(n):
        paper_loop.session_books(config, "2026-10-26")
    _cfg._reset_warnings()
    return [s for s in said if "declares no block" in s]


def test_the_example_config_declares_every_arm(monkeypatch):
    config = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert _undeclared_warnings(config, monkeypatch) == []


def test_a_config_that_predates_an_arm_warns_once_naming_it(monkeypatch):
    """The 2026-10-29 case: a config kept from before `near` existed ran it on `defaults` with nothing said."""
    config = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    key = next(k for k in _cfg.ARM_REGISTRY_KEYS if k in config)
    del config[key]["near"]
    warned = _undeclared_warnings(config, monkeypatch)
    assert len(warned) == 1 and "curve" in warned[0] and "near" in warned[0]
