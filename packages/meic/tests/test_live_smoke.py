"""The dry-run smoke harness's pure parts (live_smoke.spec_from_strategy / evaluate).

No broker, no subprocess — the harness's only live behavior is the supervised run itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from cherrypick.meic import live_smoke

STRATEGY = {
    "ok": True,
    "symbol": "XSP",
    "net_credit": 0.47,
    "estimated_pop": 0.72,
    "legs": {
        "short_put": {"symbol": "XSP 260729P00745000", "instrument_type": "Equity Option"},
        "long_put": {"symbol": "XSP 260729P00740000", "instrument-type": "Equity Option"},
        "short_call": {"symbol": "XSP 260729C00760000"},
        "long_call": {"symbol": "XSP 260729C00765000"},
    },
}


def test_spec_builds_the_four_legs_with_correct_actions():
    spec = live_smoke.spec_from_strategy(STRATEGY, quantity=1)
    actions = {leg["symbol"]: leg["action"] for leg in spec["legs"]}
    assert actions["XSP 260729P00745000"] == "sell to open"
    assert actions["XSP 260729P00740000"] == "buy to open"
    assert actions["XSP 260729C00760000"] == "sell to open"
    assert actions["XSP 260729C00765000"] == "buy to open"
    # instrument_type survives either serialization key, with a safe default.
    assert all(leg["instrument_type"] == "Equity Option" for leg in spec["legs"])
    assert spec["price_effect"] == "credit" and spec["time_in_force"] == "Day"


def test_spec_price_floors_to_a_nickel():
    # 0.47 -> 0.45: asking for LESS credit is the conservative direction, and index
    # options tick in 0.05s so the preflight can't reject the increment.
    spec = live_smoke.spec_from_strategy(STRATEGY)
    assert spec["price"] == 0.45


def test_spec_refuses_missing_or_nonpositive_credit():
    with pytest.raises(ValueError):
        live_smoke.spec_from_strategy({**STRATEGY, "net_credit": None})
    with pytest.raises(ValueError):
        live_smoke.spec_from_strategy({**STRATEGY, "net_credit": 0.02})  # floors to 0


def _result(**over):
    base = {
        "ok": True,
        "dry_run": True,
        "account_number": "5WT00001",
        "buying_power": {"change": -500.0, "isolated": None},
        "governor": {"deploy_governor": "enforced", "allowed": True},
    }
    base.update(over)
    return base


def _by_check(checks):
    return {c["check"]: c for c in checks}


def test_evaluate_passes_a_clean_dry_run():
    checks = live_smoke.evaluate(_result(), "5WT00001")
    assert all(c["ok"] for c in checks)


def test_evaluate_fails_if_the_submission_was_not_a_dry_run():
    # The check that matters most: dry_run must be literally True in the response.
    checks = _by_check(live_smoke.evaluate(_result(dry_run=False), "5WT00001"))
    assert checks["submission stayed a DRY RUN"]["ok"] is False


def test_evaluate_fails_on_wrong_account_or_no_designation():
    wrong = _by_check(live_smoke.evaluate(_result(account_number="5WT99999"), "5WT00001"))
    assert wrong["ran against the designated account"]["ok"] is False
    none = _by_check(live_smoke.evaluate(_result(), None))
    assert none["account designated"]["ok"] is False


def test_evaluate_governor_off_is_a_note_not_a_failure():
    checks = _by_check(live_smoke.evaluate(_result(governor=None), "5WT00001"))
    c = checks["deploy governor verdict (informational)"]
    assert c["ok"] is True and "OFF" in c["detail"]


def test_evaluate_fails_without_buying_power_effect():
    checks = _by_check(live_smoke.evaluate(_result(buying_power={}), "5WT00001"))
    assert checks["preflight priced a buying-power effect"]["ok"] is False
