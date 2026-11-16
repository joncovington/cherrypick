"""Forward checks a module enrolled through its paper status: read beside the register's, only for the
module that published them, and an unsound one reported, never dropped."""

from __future__ import annotations

from test_forwardcheck import _trades

from cherrypick.orchestrator import forwardcheck as fc

BLOCK = {
    "module": "automations",
    "schema": "ensemble_trade",
    "arm": "mine",
    "measure": "cycle_mean",
    "field": "net_bp",
    "since": "2026-11-09",
    "min_obs": 10,
    "reference": None,
    "kill_z": -2.0,
    "drift_z": -1.0,
}


def test_only_a_modules_own_enrolled_rows_are_taken():
    statuses = {
        "automations": {"forward": [{"id": "automation:mine", "study": "mine", "forward": BLOCK},
                                    {"id": "x", "forward": {**BLOCK, "module": "meic"}}, "junk"]},
        "meic": None,
    }  # fmt: skip
    (row,) = fc.enrolled(statuses)
    assert row == {"id": "automation:mine", "study": "mine", "forward": BLOCK, "enrolled": "automations"}
    assert fc.enrolled({}) == []


def test_an_enrolled_row_is_judged_beside_the_registers_and_an_unsound_one_reported():
    bad = {**BLOCK, "arm": "bad", "measure": "x"}
    rows = fc.enrolled(
        {"automations": {"forward": [{"id": "automation:mine", "study": "mine", "forward": BLOCK},
                                     {"id": "automation:bad", "forward": bad}]}}
    )  # fmt: skip
    trades = _trades([4.0] * 12, arm="mine")
    out = {
        r["id"]: r
        for r in fc.run({"hypotheses": []}, lambda m, s: trades, lambda p: None, enrolled_rows=rows)
    }
    mine = out["automation:mine"]
    assert mine["arm"] == "mine" and mine["state"] == "confirm" and "paper alone" in mine["basis"]
    assert out["automation:bad"]["state"] == "invalid" and out["automation:bad"]["problems"]
