"""A share leaves with its automation: moved out of a group, an automation's share is dropped from that
group's budget, today's, every dated change and one waiting, so the save is not refused for a share of an
outsider."""

from __future__ import annotations

from datetime import date

from cherrypick.automations import config as config_mod
from cherrypick.automations import groupedit

SAT = date(2026, 11, 21)
LEAVER = "iron-condor-1345"
STAYER = "put-spread-1300"
GOLD = {"enabled": False}


def _config(ensemble: dict) -> dict:
    return {
        "groups": {"ensemble": ensemble, "gold": GOLD},
        "automations": {"gld-call-spread-1dte": {"group": "gold"}},
    }


def _move(spec: dict) -> dict:
    return {"groups": {"ensemble": spec, "gold": GOLD}, "members": {LEAVER: "gold"}}


def test_a_group_not_yet_run_drops_the_leavers_share():
    spec = {"enabled": False, "allocation": 5000, "shares": {LEAVER: 40, STAYER: 30}}
    r = groupedit.check(_config(spec), _move(spec), SAT)
    assert r["problems"] == []
    assert r["block"]["ensemble"]["shares"] == {STAYER: 30}
    assert f"{LEAVER}'s share of ensemble goes with it: a share is a member's" in r["notes"]


def test_the_only_share_leaving_drops_shares_whole():
    spec = {"enabled": False, "allocation": 5000, "shares": {LEAVER: 40}}
    r = groupedit.check(_config(spec), _move(spec), SAT)
    assert r["problems"] == [] and "shares" not in r["block"]["ensemble"]


def test_a_group_that_has_run_drops_it_from_today_and_every_dated_change():
    spec = {
        "enabled": True,
        "start": "2026-11-02",
        "allocation": 5000,
        "shares": {LEAVER: 40, STAYER: 30},
        "budget_changes": [
            {"from": "2026-11-10", "allocation": 6000, "shares": {LEAVER: 50}},
            {"from": "2026-11-23", "allocation": 7000, "shares": {LEAVER: 20, STAYER: 20}},
        ],
    }
    page = {"enabled": True, "start": "2026-11-02", "allocation": 7000, "shares": {LEAVER: 20, STAYER: 20}}
    r = groupedit.check(_config(spec), _move(page), SAT)
    assert r["problems"] == []
    block = r["block"]["ensemble"]
    assert block["allocation"] == 5000 and block["shares"] == {STAYER: 30}
    assert block["budget_changes"] == [
        {"from": "2026-11-10", "allocation": 6000.0},
        {"from": "2026-11-23", "allocation": 7000.0, "shares": {STAYER: 20.0}},
    ]


def test_without_the_drop_the_loop_refuses_the_move():
    """The rule the drop answers: a share for an automation outside the group stops the load."""
    spec = {"enabled": False, "allocation": 5000, "shares": {LEAVER: 40}}
    cfg = {
        "groups": {"ensemble": spec, "gold": GOLD},
        "automations": {"gld-call-spread-1dte": {"group": "gold"}, LEAVER: {"group": "gold"}},
    }
    try:
        config_mod.load_arms(cfg)
    except ValueError as exc:
        assert "not a member of this group" in str(exc)
    else:
        raise AssertionError("a share for an outsider loaded")
