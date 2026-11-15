"""Item 84's script (meic_stack_bars_item84.py): invented sessions and entries, no run read."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from cherrypick.backtest.study import rule as kit_rule

_scripts = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_scripts))
_spec = importlib.util.spec_from_file_location(
    "meic_stack_bars_item84", _scripts / "meic_stack_bars_item84.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def test_the_declared_rule_is_complete_and_neutral():
    kit_rule.check_rule(m.RULE)
    assert m.RULE["delta"] is False and m.RULE["splits"] == []
    assert [s["step"] for s in m.CHAIN] == ["study"]


def test_a_session_is_its_net_over_the_buying_power_its_condors_held():
    entry = {"wing_width": 10, "net_credit": 2.0, "quantity": 1}
    ledger = {"2024-01-02": [entry, entry], "2024-01-03": [entry], "2024-01-04": []}
    sessions = {"2024-01-02": {"net": 160.0}, "2024-01-03": {"net": -400.0}, "2024-01-05": {"net": 0.0}}
    rows = m.arm_rows(sessions, ledger, lambda d: 0.05)
    assert [r["date"] for r in rows] == ["2024-01-02", "2024-01-03"]  # no entry, no capital held
    assert rows[0]["net"] == pytest.approx(160.0 / 1600.0) and rows[0]["session_bp"] == 1600.0
    assert rows[1]["net"] == pytest.approx(-400.0 / 800.0)
    assert all(r["days"] == 1 and r["bp"] == 1.0 and r["rate"] == 0.05 for r in rows)


def test_too_few_sessions_is_no_verdict():
    rows = [
        {"date": f"2024-01-{d:02d}", "net": 0.01, "bp": 1.0, "days": 1, "rate": 0.05} for d in range(1, 30)
    ]
    assert kit_rule.evaluate(m.RULE, {"n1": rows})["verdict"]["n1"] == "no verdict"
