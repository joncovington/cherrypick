"""The Groups page's view and check: each automation's own switch beside its group's, and a change
checked by the same load the loop runs, so the page can write only what the loop would accept."""

from __future__ import annotations

from datetime import date

from cherrypick.automations import arms, groupedit
from cherrypick.automations import config as config_mod

SHIPPED = sorted(arms.ARMS)


def _ensemble_on() -> dict:
    return {"enabled": True}


def test_the_view_lists_every_group_with_each_members_own_switch():
    cfg = {"groups": {"ensemble": {"enabled": False}}, "automations": {"iron-fly-1000": {"enabled": False}}}
    v = groupedit.view(cfg)
    (g,) = v["groups"]
    assert g["name"] == "ensemble" and g["enabled"] is False
    assert [m["name"] for m in g["members"]] == SHIPPED
    own = {m["name"]: m["enabled"] for m in g["members"]}
    # The group being off does not show as each member off: the member's own switch is what is listed.
    assert own["iron-fly-1000"] is False and own["iron-condor-1345"] is True
    assert v["ungrouped"] == []
    assert all(m["shipped"] for m in g["members"])


def test_a_group_not_named_in_the_file_shows_on_as_the_loop_reads_it():
    (g,) = groupedit.view({})["groups"]
    assert g["enabled"] is True and "allocation" not in g


def test_a_valid_change_returns_the_block_and_the_moves_to_write():
    cfg = {"groups": {"_note": "kept", "ensemble": {"enabled": True, "_why": "kept too"}}}
    r = groupedit.check(
        cfg,
        {
            "groups": {"ensemble": {"enabled": False, "max_risk": 2000}, "gold": {"enabled": False}},
            "members": {"gld-call-spread-1dte": "gold"},
        },
    )
    assert r["problems"] == []
    assert r["block"] == {
        "_note": "kept",
        "ensemble": {"_why": "kept too", "enabled": False, "max_risk": 2000},
        "gold": {"enabled": False},
    }
    assert r["moves"] == {"gld-call-spread-1dte": "gold"}
    # Laid over the config, the loop loads it: the moved automation is in its new group, off with it.
    after = config_mod.load_arms(
        {**cfg, "groups": r["block"], "automations": {"gld-call-spread-1dte": {"group": "gold"}}}
    )
    assert (
        after["gld-call-spread-1dte"]["group"] == "gold" and after["gld-call-spread-1dte"]["enabled"] is False
    )


def test_an_automation_taken_out_of_every_group_is_written_as_null():
    r = groupedit.check(
        {}, {"groups": {"ensemble": _ensemble_on()}, "members": {"gld-call-spread-1dte": None}}
    )
    assert r["problems"] == [] and r["moves"] == {"gld-call-spread-1dte": None}
    after = config_mod.load_arms({"automations": {"gld-call-spread-1dte": {"group": None}}})
    assert after["gld-call-spread-1dte"].get("group") is None
    assert {
        m["name"]
        for m in groupedit.view({"automations": {"gld-call-spread-1dte": {"group": None}}})["ungrouped"]
    } == {"gld-call-spread-1dte"}


def test_a_group_emptied_of_members_is_dropped_from_the_block():
    moves = {n: "rest" for n in SHIPPED}
    r = groupedit.check(
        {"groups": {"ensemble": {"enabled": True}}}, {"groups": {"rest": _ensemble_on()}, "members": moves}
    )
    assert r["problems"] == [] and set(r["block"]) == {"rest"}


def test_the_loops_own_rules_refuse_a_bad_change():
    over = groupedit.check(
        {},
        {
            "groups": {
                "ensemble": {
                    "enabled": True,
                    "allocation": 1000,
                    "shares": {"iron-fly-1000": 60, "iron-condor-1345": 60},
                }
            },
            "members": {},
        },
    )
    assert over["block"] is None and any("more than 100" in p for p in over["problems"])
    outsider = groupedit.check(
        {"automations": {"gld-call-spread-1dte": {"group": None}}},
        {
            "groups": {
                "ensemble": {"enabled": True, "max_risk": 500, "shares": {"gld-call-spread-1dte": 10}}
            },
            "members": {},
        },
    )
    assert any("not a member" in p for p in outsider["problems"])
    nothing = groupedit.check(
        {}, {"groups": {"ensemble": {"enabled": True, "shares": {"iron-fly-1000": 10}}}, "members": {}}
    )
    assert any("share of nothing" in p for p in nothing["problems"])


def test_a_group_with_members_must_say_whether_it_is_on_and_names_are_checked():
    r = groupedit.check({}, {"groups": {}, "members": {}})
    assert r["problems"] == ["groups.ensemble: has members, so say whether it is on"]
    r = groupedit.check({}, {"groups": {"ensemble": {"enabled": "yes"}}, "members": {}})
    assert any("enabled: true or false" in p for p in r["problems"])
    r = groupedit.check(
        {}, {"groups": {"ensemble": _ensemble_on()}, "members": {"nope": "x", "iron-fly-1000": " pad"}}
    )
    assert "members.nope: no such automation" in r["problems"]
    assert "members.iron-fly-1000: a group name, or null for none" in r["problems"]
    r = groupedit.check({}, {"groups": {"ensemble": {"enabled": True, "colour": "red"}}, "members": {}})
    assert r["problems"] == ["groups.ensemble: unknown key 'colour'"]


WED = date(2026, 11, 18)


def test_each_automation_is_named_with_the_one_thing_that_stops_it():
    cfg = {
        "groups": {"ensemble": {"enabled": False}},
        "automations": {
            "iron-fly-1000": {"enabled": False},
            "gld-call-spread-1dte": {"group": None, "start": "2026-11-20"},
            "gld-call-spread-20d": {"group": None},
        },
    }
    v = groupedit.view(cfg, WED)
    assert v["today"] == "2026-11-18" and v["next_session"] == "2026-11-19"
    rows = {m["name"]: m for g in v["groups"] for m in g["members"]} | {m["name"]: m for m in v["ungrouped"]}
    assert rows["iron-fly-1000"]["state"] == "off_own"  # its own switch, though its group is off too
    assert rows["iron-condor-1345"]["state"] == "off_group"
    # In no group, an automation answers to its own switch and its start alone.
    assert (rows["gld-call-spread-1dte"]["state"], rows["gld-call-spread-1dte"]["from"]) == (
        "waiting",
        "2026-11-20",
    )
    assert rows["gld-call-spread-20d"]["state"] == "on"
    assert v["counts"] == {"on": 1, "waiting": 1, "off_group": 5, "off_own": 1}


def test_a_group_switched_on_starts_at_the_next_session():
    r = groupedit.check(
        {"groups": {"ensemble": {"enabled": False}}}, {"groups": {"ensemble": {"enabled": True}}}, WED
    )
    assert r["problems"] == [] and r["block"] == {"ensemble": {"enabled": True, "start": "2026-11-19"}}
    assert r["notes"] == ["ensemble switches on from 2026-11-19, the next session"]
    states = {m["state"] for m in r["after"]["groups"][0]["members"]}
    assert states == {"waiting"}
    # A later start the page gives is kept; a group already on is left as it is.
    r = groupedit.check(
        {"groups": {"ensemble": {"enabled": False}}},
        {"groups": {"ensemble": {"enabled": True, "start": "2026-12-01"}}},
        WED,
    )
    assert r["block"]["ensemble"]["start"] == "2026-12-01"
    r = groupedit.check({}, {"groups": {"ensemble": {"enabled": True}}}, WED)
    assert "start" not in r["block"]["ensemble"]


def test_an_automation_switched_on_starts_at_the_next_session_and_off_is_written_as_is():
    cfg = {"automations": {"iron-fly-1000": {"enabled": False}}}
    r = groupedit.check(
        cfg,
        {"groups": {"ensemble": _ensemble_on()}, "switches": {"iron-fly-1000": True, "long-put-1030": False}},
        WED,
    )
    assert r["problems"] == []
    assert r["switches"] == {
        "iron-fly-1000": {"enabled": True, "start": "2026-11-19"},
        "long-put-1030": {"enabled": False},
    }
    rows = {m["name"]: m for m in r["after"]["groups"][0]["members"]}
    assert (rows["iron-fly-1000"]["state"], rows["iron-fly-1000"]["from"]) == ("waiting", "2026-11-19")
    assert rows["long-put-1030"]["state"] == "off_own"
    # A switch to what it already is, is no switch.
    r = groupedit.check(
        cfg, {"groups": {"ensemble": _ensemble_on()}, "switches": {"long-put-1030": True}}, WED
    )
    assert r["switches"] == {}


def test_a_switch_with_a_forward_rule_keeps_the_start_its_rule_reads_from(monkeypatch):
    arm = {
        **arms.ARMS["iron-fly-1000"],
        "enabled": False,
        "start": "2026-10-01",
        "forward": {"measure": "net"},
    }
    monkeypatch.setattr(groupedit, "_own_arms", lambda config: {**arms.ARMS, "iron-fly-1000": arm})
    monkeypatch.setattr(config_mod, "load_arms", lambda config, **kw: {**arms.ARMS, "iron-fly-1000": arm})
    r = groupedit.check(
        {}, {"groups": {"ensemble": _ensemble_on()}, "switches": {"iron-fly-1000": True}}, WED
    )
    assert r["switches"] == {"iron-fly-1000": {"enabled": True}}
    assert r["notes"] == ["iron-fly-1000 keeps its start 2026-10-01: its forward rule reads from it"]


def test_a_bad_switch_is_refused():
    r = groupedit.check(
        {}, {"groups": {"ensemble": _ensemble_on()}, "switches": {"nope": True, "iron-fly-1000": "on"}}, WED
    )
    assert "switches.nope: no such automation" in r["problems"]
    assert "switches.iron-fly-1000: true or false" in r["problems"]
    assert r["block"] is None and r["switches"] == {}


def test_an_unchanged_member_is_not_a_move():
    r = groupedit.check(
        {}, {"groups": {"ensemble": _ensemble_on()}, "members": {"iron-fly-1000": "ensemble"}}
    )
    assert r["problems"] == [] and r["moves"] == {}
