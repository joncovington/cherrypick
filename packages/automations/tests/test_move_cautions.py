"""A move that sets an automation trading starts it at the next session, never mid-day, as switching on does;
and each move carries its cautions: the record goes with it, its positions count against the new cap, and
a budget not sized for it."""

from __future__ import annotations

from datetime import date

from cherrypick.automations import groupedit

SAT = date(2026, 11, 21)
MON = "2026-11-23"
MOVER = "gld-call-spread-1dte"


def _config(gold_on: bool, ensemble: dict) -> dict:
    return {
        "groups": {"ensemble": ensemble, "gold": {"enabled": gold_on}},
        "automations": {MOVER: {"group": "gold"}, "long-put-1030": {"group": "gold"}},
    }


def _move(gold_on: bool, ensemble: dict) -> dict:
    return {"groups": {"ensemble": ensemble, "gold": {"enabled": gold_on}}, "members": {MOVER: "ensemble"}}


RUNNING = {"enabled": True, "start": "2026-11-02"}


def test_a_move_from_an_off_group_into_a_running_one_starts_at_the_next_session():
    r = groupedit.check(_config(False, RUNNING), _move(False, RUNNING), SAT)
    assert r["problems"] == []
    assert r["switches"] == {MOVER: {"enabled": True, "start": MON}}
    assert f"{MOVER} trades in ensemble from {MON}, the next session" in r["notes"]


def test_a_move_between_two_running_groups_keeps_trading_as_it_was():
    r = groupedit.check(_config(True, RUNNING), _move(True, RUNNING), SAT)
    assert r["problems"] == [] and r["switches"] == {}


def test_a_move_into_a_group_that_is_off_needs_no_start():
    off = {"enabled": False}
    r = groupedit.check(_config(False, off), _move(False, off), SAT)
    assert r["problems"] == [] and r["switches"] == {}


def test_a_move_out_of_an_off_group_into_none_starts_at_the_next_session():
    r = groupedit.check(_config(False, RUNNING), {**_move(False, RUNNING), "members": {MOVER: None}}, SAT)
    assert r["switches"] == {MOVER: {"enabled": True, "start": MON}}


def test_every_move_says_its_record_goes_with_it():
    r = groupedit.check(_config(True, RUNNING), _move(True, RUNNING), SAT)
    assert r["cautions"] == [
        f"{MOVER}'s record moves with it: gold and ensemble are read over their members now,"
        " so both groups' figures change, past sessions included"
    ]


def test_a_move_into_a_group_with_a_budget_names_the_cap_and_the_budget():
    budgeted = {**RUNNING, "allocation": 5000, "max_risk": 2000}
    r = groupedit.check(_config(True, budgeted), _move(True, budgeted), SAT)
    cautions = r["cautions"]
    assert f"Any position {MOVER} holds counts against ensemble's cap from the save" in cautions
    budget = [c for c in cautions if c.startswith("ensemble's budget was not sized for")]
    assert len(budget) == 1 and "it has no share there, only the group's cap" in budget[0]
