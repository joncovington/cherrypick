"""Hypothesis 75's study script (packages/backtest/scripts/ensemble_item75.py): until the driver is built it
refuses to run, so a queue row cannot read a result before the declaration has its driver."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ensemble_item75.py"


def _module():
    spec = importlib.util.spec_from_file_location("ensemble_item75", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_placeholder_refuses_to_run():
    assert _module().main([]) == 2


def test_the_correction_log_starts_empty():
    assert _module().REPLAY_CORRECTIONS == []
