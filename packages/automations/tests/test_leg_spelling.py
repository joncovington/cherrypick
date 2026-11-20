"""A leg's two spellings: written (action, qty, side: the page's words) and older (side as signed
contracts, right as C/P). Either loads to the engine's form, so an automation hashes the same in both
and no backtest label moves when one is converted."""

from __future__ import annotations

import copy

import pytest

from cherrypick.automations import arms, config, drafts, enrolment

#: A near leg's written keys: `expiry` is kept only on a far leg, so a one-expiry leg is as it was.
NEAR_KEYS = arms.WRITTEN_LEG_KEYS - {"expiry"}

STRIKE = {"rule": "delta", "target": 0.2}


def _older(leg: dict) -> dict:
    """A written leg in the older spelling."""
    got, problems = arms.internal_leg(leg)
    assert not problems
    return got


def test_the_written_spelling_reads_as_the_older_one():
    assert arms.internal_leg({"name": "L2", "action": "STO", "qty": 2, "side": "put", "strike": STRIKE}) == (
        {"name": "L2", "right": "P", "side": -2, "strike": STRIKE},
        [],
    )
    assert (
        arms.internal_leg({"name": "L1", "action": "BTO", "side": "call", "strike": STRIKE})[0]["side"] == 1
    )
    older = {"name": "x", "right": "C", "side": -1, "strike": STRIKE}
    assert arms.internal_leg(older) == (older, [])


def test_written_is_the_inverse_of_internal():
    for older in (
        {"name": "a", "right": "P", "side": -3, "strike": STRIKE},
        {"name": "b", "right": "C", "side": 1, "strike": STRIKE},
    ):
        written = arms.written_leg(older)
        assert set(written) == NEAR_KEYS
        assert _older(written) == older
        assert arms.written_leg(written) == written


@pytest.mark.parametrize(
    "leg, says",
    [
        ({"name": "a", "action": "STO", "qty": 1, "side": -1, "strike": STRIKE}, ".side: put or call"),
        ({"name": "a", "action": "STO", "qty": 1, "side": "put", "right": "P", "strike": STRIKE}, "not both"),
        ({"name": "a", "action": "SELL", "side": "put", "strike": STRIKE}, ".action: BTO or STO"),
        ({"name": "a", "action": "STO", "qty": 0, "side": "put", "strike": STRIKE}, ".qty: a whole number"),
        (
            {"name": "a", "action": "STO", "qty": True, "side": "put", "strike": STRIKE},
            ".qty: a whole number",
        ),
        (
            {"name": "a", "action": "STO", "side": "put", "colour": "red", "strike": STRIKE},
            "unknown key 'colour'",
        ),
        ({"name": "a", "right": "P", "side": "put", "strike": STRIKE}, "right/side"),
    ],
)
def test_a_leg_is_one_spelling_or_the_other_and_never_guessed(leg, says):
    got, problems = arms.internal_leg(leg, "x.legs[0]")
    assert got is None and any(says in p for p in problems), problems


def test_the_shipped_automations_are_written_in_the_page_spelling():
    for name, arm in arms.ARMS.items():
        assert all(set(leg) == NEAR_KEYS for leg in arm["legs"]), name


def test_every_shipped_automation_loads_and_hashes_the_same_in_either_spelling():
    """The guard on the conversion: an automation's legs, as loaded, and every block hash are the same
    whether its legs are written or older, so converting one never moves its backtest label."""
    written = arms.load_arms()
    older = arms.load_arms({n: {"legs": [_older(leg) for leg in a["legs"]]} for n, a in arms.ARMS.items()})
    for name in arms.ARMS:
        assert written[name]["legs"] == older[name]["legs"], name
        assert enrolment.block_hashes(written[name]) == enrolment.block_hashes(older[name]), name
        assert {b: v["state"] for b, v in enrolment.labels(written[name]).items()} == {
            b: v["state"] for b, v in enrolment.labels(older[name]).items()
        }, name


def test_the_editor_starts_from_the_written_spelling_and_a_save_writes_it():
    older = [_older(leg) for leg in arms.ARMS["iron-fly-1000"]["legs"]]
    cfg = {"automations": {"iron-fly-1000": {"legs": older}}}
    spec = drafts.editable(cfg, "iron-fly-1000")
    assert all(set(leg) == NEAR_KEYS for leg in spec["legs"])
    out = drafts.draft(cfg, "mine-copy", {**spec, "legs": older}, __import__("datetime").date(2026, 1, 5))
    assert out["problems"] == [] and all(set(leg) == NEAR_KEYS for leg in out["entry"]["legs"])


def test_the_conversion_lists_older_legs_and_proves_nothing_moves():
    older = {n: {"legs": [_older(leg) for leg in a["legs"]]} for n, a in list(arms.ARMS.items())[:3]}
    cfg = {"automations": copy.deepcopy(older)}
    out = drafts.leg_conversion(cfg)
    assert out["problems"] == [] and sorted(out["convert"]) == sorted(older)
    for name, legs in out["convert"].items():
        assert legs == arms.ARMS[name]["legs"]
    assert drafts.leg_conversion({"automations": {"iron-fly-1000": {"enabled": False}}}) == {
        "ok": True,
        "convert": {},
        "problems": [],
    }
    assert config.load_arms(cfg)["iron-fly-1000"]["legs"] == arms.load_arms()["iron-fly-1000"]["legs"]
