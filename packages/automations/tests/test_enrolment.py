"""Enrolment: a start date paper keeps to (a group's boundary for its members), each block labelled for
what a backtest judged of it, and a forward-check rule declared with the automation in the shape the
orchestrator's forward-checks reads."""

from __future__ import annotations

import copy
from datetime import date

import pytest

from cherrypick.automations import arms, db, enrolment, paper_loop, stream_request
from cherrypick.automations import config as config_mod

A = arms.load_arms()
FORWARD = {"measure": "session_sum", "field": "net_pnl", "min_obs": 30, "kill_z": -2.0, "drift_z": -1.0}


def own(**changes):
    a = copy.deepcopy(A["long-put-1030"])
    a.pop("group")
    a.pop("register")
    a.update(name="mine", **changes)
    return a


def test_the_shipped_eight_are_labelled_backtested_block_by_block():
    for name, arm in A.items():
        lab = enrolment.labels(arm)
        assert set(lab) == set(enrolment.BLOCKS)
        assert all(r["state"] == "backtested" and r["item"] == 75 for r in lab.values()), name
    assert (
        enrolment.summary(A["long-put-1030"])
        == "structure, entry, gates, exit, size backtested (item 75, passed)"
    )


def test_a_changed_block_loses_its_label_and_a_new_automation_has_none():
    arm = copy.deepcopy(A["iron-condor-1345"])
    arm["exit"] = {**arm["exit"], "close_at_dte": 1}
    lab = enrolment.labels(arm)
    assert lab["exit"]["state"] == "changed" and lab["entry"]["state"] == "backtested"
    assert enrolment.summary(arm) == (
        "structure, entry, gates, size backtested (item 75, passed); exit changed since its backtest"
    )
    fresh = own()
    assert {r["state"] for r in enrolment.labels(fresh).values()} == {"not_backtested"}
    assert enrolment.summary(fresh) == "structure, entry, gates, exit, size not backtested"
    # A register label without block hashes vouches for no block.
    labelled = own(register={"item": 90, "verdict": "passed"})
    assert {r["state"] for r in enrolment.labels(labelled).values()} == {"not_backtested"}


def test_a_block_hash_reads_only_that_blocks_keys():
    a = copy.deepcopy(A["iron-fly-1000"])
    before = enrolment.block_hashes(a)
    a["note"], a["enabled"] = "changed note", False
    assert enrolment.block_hashes(a) == before
    a["limits"] = {**a["limits"], "max_open": 2}
    after = enrolment.block_hashes(a)
    assert [b for b in before if before[b] != after[b]] == ["size"]


def test_paper_enters_only_from_the_start():
    mon = date(2026, 11, 16)  # a Monday, one of the arm's weekdays
    loaded = config_mod.load_arms(
        {"automations": {"mine": own(start="2026-11-18", weekdays=[0, 1, 2, 3, 4])}}
    )
    assert "mine" not in stream_request.active_arms(loaded, mon)
    assert "mine" in stream_request.active_arms(loaded, date(2026, 11, 18))
    assert enrolment.joins({"name": "x"}, mon)


def test_a_group_member_joins_at_the_groups_boundary():
    loaded = config_mod.load_arms({"groups": {"ensemble": {"enabled": True, "start": "2026-12-01"}}})
    assert {a["start"] for a in loaded.values()} == {"2026-12-01"}
    later = config_mod.load_arms(
        {
            "automations": {"iron-fly-1000": {"start": "2026-12-15"}},
            "groups": {"ensemble": {"enabled": True, "start": "2026-12-01"}},
        }
    )
    assert later["iron-fly-1000"]["start"] == "2026-12-15"  # its own, later start stands
    with pytest.raises(ValueError, match="groups.ensemble.start"):
        config_mod.load_arms({"groups": {"ensemble": {"enabled": True, "start": "12/01/2026"}}})


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"start": "2026-13-01"}, "start"),
        ({"start": "2026-11-18", "forward": {**FORWARD, "measure": "mean"}}, "measure"),
        ({"start": "2026-11-18", "forward": {**FORWARD, "kill_z": 2.0, "drift_z": 3.0}}, "a negative number"),
        ({"start": "2026-11-18", "forward": {**FORWARD, "kill_z": -0.5}}, "below drift_z"),
        ({"start": "2026-11-18", "forward": {**FORWARD, "min_obs": 0}}, "min_obs"),
        ({"start": "2026-11-18", "forward": {**FORWARD, "since": "2026-11-18"}}, "unknown key"),
        (
            {"start": "2026-11-18", "forward": {k: v for k, v in FORWARD.items() if k != "field"}},
            "needs 'field'",
        ),
        ({"forward": FORWARD}, "reads from the start date"),
        ({"register": {"item": 9, "verdict": "passed", "blocks": {"exits": "a" * 12}}}, "register.blocks"),
        ({"register": {"item": 9, "verdict": "passed", "blocks": {"exit": "abc"}}}, "12-digit"),
    ],
)
def test_a_malformed_enrolment_is_refused_with_its_path(changes, match):
    problems = arms.validate(own(**changes))
    assert any(match in p for p in problems), problems


def test_the_forward_rule_becomes_a_register_shaped_row():
    loaded = config_mod.load_arms({"automations": {"mine": own(start="2026-11-18", forward=FORWARD)}})
    (row,) = enrolment.forward_rows(loaded)
    assert row["id"] == "automation:mine"
    block = row["forward"]
    assert block == {
        "module": "automations",
        "schema": "ensemble_trade",
        "arm": "mine",
        "since": "2026-11-18",
        "reference": None,
        **FORWARD,
    }
    # The keys the orchestrator's forward-checks needs (`forwardcheck.NEEDED`) are all there.
    needed = (
        "module",
        "schema",
        "arm",
        "measure",
        "field",
        "since",
        "min_obs",
        "reference",
        "kill_z",
        "drift_z",
    )
    assert all(k in block for k in needed)


def test_the_status_carries_the_starts_labels_and_forward_rows(tmp_path):
    config = {"automations": {"mine": own(start="2026-11-18", forward=FORWARD)}}
    loaded = config_mod.load_arms(config)
    conn = db.connect(str(tmp_path / "paper.db"))
    st = paper_loop.run_status(config, loaded, conn, cache_path=str(tmp_path / "none.db"))
    assert st["starts"] == {"mine": "2026-11-18"}
    assert st["backtested"]["mine"].endswith("not backtested")
    assert st["backtested"]["long-put-1030"].endswith("backtested (item 75, passed)")
    assert [r["id"] for r in st["forward"]] == ["automation:mine"]
