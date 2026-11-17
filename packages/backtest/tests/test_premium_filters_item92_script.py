"""Item 92's script (premium_filters_item92.py): invented trades only, the store never read."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from cherrypick.backtest.study import rule as kit_rule

_spec = importlib.util.spec_from_file_location(
    "premium_filters_item92", Path(__file__).resolve().parents[1] / "scripts" / "premium_filters_item92.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def test_the_declared_rule_is_complete():
    # Fails until the declaration fills every value of RULE.
    kit_rule.check_rule(m.RULE)


def test_the_chain_samples_smokes_then_fetches_and_studies():
    assert [s["step"] for s in m.CHAIN] == ["plan", "fetch-sample", "smoke", "fetch-chains", "study"]


def test_the_declaration_is_what_the_plan_states():
    # A change to any of these is a change to the declared test: it needs a logged correction.
    assert m.RULE["splits"] == ["2021-06-01"] and m.RULE["delta"] is True and m.RULE["t_bar"] == 2.4
    assert (m.RULE["min_n"], m.RULE["holdout_min_n"]) == (30, 30)
    assert m.ARMS == ("put-ivr30", "put-hv_lt_iv", "spread-ivr30", "spread-hv_lt_iv")
    assert m.FILTER_RULE == {
        "t_bar": 2.4,
        "min_entries": 20,
        "holdout_min_entries": 20,
        "cluster": "cycle",
        "split": "2021-06-01",
    }
    assert m.SPEC["funds"] == {"SPY": "VIX", "QQQ": "VXN", "IWM": "RVX", "GLD": "GVZ"}
    assert m.SPEC["structures"] == {
        "put": {"short_delta": 0.16},
        "spread": {"short_delta": 0.20, "long_delta": 0.10},
    }
    assert (
        m.SPEC["exit"]["profit_target_pct_of_credit"] == 50 and m.SPEC["exit"]["close_days_to_expiry"] == 21
    )
    assert not set(m.ARMS) & set(m.REPORTED)


@pytest.mark.skip(reason="item 92: write the replay's tests on invented trades")
def test_the_replay():
    raise AssertionError
