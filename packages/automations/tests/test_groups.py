"""Automation groups: the shipped eight are the ensemble group, each labelled with item 75's register
verdict; switching the group off switches its automations off for new entries; a misspelt group is
refused."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cherrypick.automations import arms, stream_request
from cherrypick.automations import config as config_mod

REPO = Path(__file__).resolve().parents[3]


def test_the_shipped_eight_are_the_ensemble_group_with_item_75s_verdicts():
    assert {a["group"] for a in arms.ARMS.values()} == {"ensemble"}
    assert all(a["register"]["item"] == 75 for a in arms.ARMS.values())
    # The register's passing arms are the ones it reads forward; the labels agree with it.
    reg = json.loads((REPO / "research/hypotheses.json").read_text(encoding="utf-8"))
    rows = reg if isinstance(reg, list) else next(v for v in reg.values() if isinstance(v, list))
    row = next(r for r in rows if r.get("id") == 75)
    forward = {f["arm"] for f in row["forward"]}
    passed = {n for n, a in arms.ARMS.items() if a["register"]["verdict"] == "passed"}
    assert passed == forward == {"iron-condor-1345", "long-put-1030"}
    assert {n for n, a in arms.ARMS.items() if a["register"]["verdict"] == "no_verdict"} == {
        "put-spread-1300",
        "gld-call-spread-1dte",
    }


def test_a_group_switched_off_switches_its_automations_off():
    on = config_mod.load_arms({})
    assert all(a["enabled"] for a in on.values())
    # A note beside the groups and one inside a group are both read past.
    off = config_mod.load_arms(
        {"groups": {"_note": "paused for the rename", "ensemble": {"enabled": False, "_note": "paused"}}}
    )
    assert not any(a["enabled"] for a in off.values())
    assert config_mod.enabled(off) == {}
    assert stream_request.active_arms(off, __import__("datetime").date(2026, 11, 16)) == {}
    status = config_mod.group_status(off, {"groups": {"ensemble": {"enabled": False}}})
    assert status["ensemble"]["enabled"] is False and len(status["ensemble"]["arms"]) == 8
    assert {r["name"]: r["verdict"] for r in status["ensemble"]["arms"]}["long-put-1030"] == "passed"
    # An arm outside any group, or in a group left on, is untouched.
    own = {**arms.ARMS["long-put-1030"], "name": "mine"}
    own.pop("group")
    mixed = config_mod.load_arms({"groups": {"ensemble": {"enabled": False}}, "arms": {"mine": own}})
    assert mixed["mine"]["enabled"] and not mixed["long-put-1030"]["enabled"]


@pytest.mark.parametrize(
    ("groups", "match"),
    [
        ({"ensembel": {"enabled": False}}, "no arm belongs"),
        ({"ensemble": {"enabled": "no"}}, "true or false"),
        ({"ensemble": {"enabled": False, "until": "x"}}, "unknown key"),
        ({"ensemble": False}, "an object"),
        (["ensemble"], "an object of"),
    ],
)
def test_a_malformed_groups_block_is_refused(groups, match):
    with pytest.raises(ValueError, match=match):
        config_mod.load_arms({"groups": groups})


def test_validation_refuses_a_bad_group_or_register():
    import copy

    a = copy.deepcopy(arms.ARMS["iron-fly-1000"])
    a["group"] = " ensemble"
    a["register"] = {"item": 75, "verdict": "pass"}
    problems = arms.validate(a)
    assert any(".group" in p for p in problems) and any(".register" in p for p in problems)
