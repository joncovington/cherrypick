"""A group with no automation: kept while it is off (one being set up), refused switched on, and still
refused beside an automation naming a group the block does not (the misspelling that would leave it
trading). The Groups check keeps it, and the view lists it."""

from __future__ import annotations

from datetime import date

import pytest

from cherrypick.automations import config as config_mod
from cherrypick.automations import groupedit

SAT = date(2026, 11, 21)
ENSEMBLE = {"ensemble": {"enabled": False}}


def test_an_empty_group_switched_off_is_kept():
    arms = config_mod.load_arms({"groups": {**ENSEMBLE, "testing": {"enabled": False}}})
    assert all(a["group"] == "ensemble" for a in arms.values())


def test_an_empty_group_cannot_be_on():
    with pytest.raises(
        ValueError, match="groups.testing: no automation belongs to this group, so it cannot be on"
    ):
        config_mod.load_arms({"groups": {**ENSEMBLE, "testing": {"enabled": True}}})


def test_an_empty_group_beside_a_group_the_block_does_not_name_reads_as_a_misspelling():
    with pytest.raises(ValueError, match="a misspelling"):
        config_mod.load_arms({"groups": {"ensembel": {"enabled": False}}})


def test_the_check_keeps_an_empty_group_and_the_view_lists_it():
    r = groupedit.check({"groups": ENSEMBLE}, {"groups": {**ENSEMBLE, "testing": {"enabled": False}}}, SAT)
    assert r["problems"] == [] and r["block"]["testing"] == {"enabled": False}
    names = {g["name"]: g for g in r["after"]["groups"]}
    assert names["testing"]["members"] == [] and names["testing"]["enabled"] is False


def test_the_check_refuses_an_empty_group_switched_on():
    r = groupedit.check({"groups": ENSEMBLE}, {"groups": {**ENSEMBLE, "testing": {"enabled": True}}}, SAT)
    assert r["problems"] == ["groups.testing: no automation belongs to this group, so it cannot be on"]


def test_a_group_left_out_of_the_proposal_is_deleted():
    cfg = {"groups": {**ENSEMBLE, "testing": {"enabled": False}}}
    r = groupedit.check(cfg, {"groups": ENSEMBLE}, SAT)
    assert r["problems"] == [] and "testing" not in r["block"]


def test_the_last_member_leaving_an_on_group_must_turn_it_off():
    cfg = {
        "groups": {"ensemble": {"enabled": True}, "gold": {"enabled": True}},
        "automations": {"gld-call-spread-1dte": {"group": "gold"}},
    }
    leave = {
        "groups": {"ensemble": {"enabled": True}, "gold": {"enabled": True}},
        "members": {"gld-call-spread-1dte": "ensemble"},
    }
    assert "cannot be on" in groupedit.check(cfg, leave, SAT)["problems"][0]
    leave["groups"]["gold"] = {"enabled": False}
    assert groupedit.check(cfg, leave, SAT)["problems"] == []
