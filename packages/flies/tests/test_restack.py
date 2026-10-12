"""The `restack` arm: control plus the duplicate rule ignoring a completed risk-free fly (2026-10-11)."""

import pytest
from test_book import one_arm_config
from test_engine import _machine_config, params, q, snapshot

from cherrypick.flies import book as bookmod
from cherrypick.flies import db as dbmod
from cherrypick.flies import engine, paper_loop

ON = {"restack_completed": True}


@pytest.fixture()
def conn(tmp_path):
    return dbmod.connect(str(tmp_path / "paper_trades.db"))


def held(kind="fly", net=1.50, fees=0.0, side="put"):
    return {
        "center": 6000.0,
        "kind": kind,
        "side": side,
        "wing_width": 5,
        "far_width": None,
        "net": net,
        "fees": fees,
        "quantity": 1,
    }


def test_a_completed_risk_free_fly_no_longer_blocks_its_centre():
    snap = snapshot(underlying_price=5998.0)
    enter, reason, _ = engine.evaluate_credit_spread_entry(snap, params(), [held()])
    assert not enter and reason == "duplicate_structure"
    enter, reason, plan = engine.evaluate_credit_spread_entry(snap, params(**ON), [held()])
    assert enter, reason
    assert plan["center"] == 6000.0


def test_an_uncompleted_vertical_still_blocks_its_centre():
    """Stacking a second short vertical on one still open is adding to a possible loser, not the
    question the arm asks."""
    vertical = held(kind="short_vertical")
    enter, reason, _ = engine.evaluate_credit_spread_entry(
        snapshot(underlying_price=5998.0), params(**ON), [vertical]
    )
    assert not enter and reason == "duplicate_structure"


def test_a_completion_that_locked_in_a_loss_still_blocks():
    assert engine.completed_risk_free(held()) is True
    loser = held(net=0.10, fees=40.0)
    assert engine.completed_risk_free(loser) is False
    enter, reason, _ = engine.evaluate_credit_spread_entry(
        snapshot(underlying_price=5998.0), params(**ON), [loser]
    )
    assert not enter and reason == "duplicate_structure"


def test_an_unreadable_row_counts_as_held():
    """Fails closed: this path admits an entry, so a row it cannot read must keep blocking."""
    assert engine.completed_risk_free({"center": 6000.0, "kind": "fly", "wing_width": 5}) is False


def test_an_iron_fly_completion_counts_and_a_bwb_does_not():
    assert engine.completed_risk_free(held(kind="iron_fly", net=5.20)) is True
    assert engine.completed_risk_free({**held(kind="bwb"), "far_width": 10}) is False


def test_the_rule_is_off_unless_an_arm_sets_it():
    enter, reason, _ = engine.evaluate_credit_spread_entry(
        snapshot(underlying_price=5998.0), params(restack_completed=False), [held()]
    )
    assert not enter and reason == "duplicate_structure"


def test_through_the_book_a_completed_fly_is_entered_again_and_control_is_not(conn):
    """Shown to fail without the switch: the same three ticks give control one position and the
    switched arm two at the same centre, the first completed."""
    open_tick = snapshot(underlying_price=5998.0)
    complete_tick = snapshot(underlying_price=6004.0, puts={6000: q(1.0, 1.2), 6005: q(2.4, 2.6)})
    back_tick = snapshot(underlying_price=5998.0, now_min=13 * 60)
    counts = {}
    for arm, extra in (("control", {}), ("restack", ON)):
        cfg = one_arm_config(entry_modes=["legged"])
        cfg["arms"] = {arm: dict(extra)}
        for snap in (open_tick, complete_tick, back_tick):
            bookmod.process_snapshot(snap, cfg, conn, arm)
        rows = dbmod.book_positions(conn, bookmod.book_id_for("2026-07-20", arm, "SPX"))
        counts[arm] = sorted((r["center"], r["kind"]) for r in rows)
    assert counts["control"] == [(6000.0, "fly")]
    assert counts["restack"] == [(6000.0, "fly"), (6000.0, "short_vertical")]


def test_restack_is_registered():
    assert "restack" in engine.ARMS


def test_this_machines_restack_differs_from_control_in_one_variable():
    arms = _machine_config()["arms"]
    if "restack" not in arms:
        pytest.skip("this machine does not run the restack arm")

    def live_keys(d):
        return {k: v for k, v in d.items() if not k.startswith("_") and k != "enabled"}

    rs, ctl = live_keys(arms["restack"]), live_keys(arms["control"])
    assert rs.pop("restack_completed") is True
    assert rs == ctl


def test_the_arm_is_journaled_once_when_configured(tmp_path):
    conn = dbmod.connect(str(tmp_path / "paper_trades.db"))
    cfg = {"arms": {"restack": {"enabled": True, **ON}}}
    paper_loop._note_restack_arm(conn, cfg)
    paper_loop._note_restack_arm(conn, cfg)
    rows = [r for r in dbmod.measurement_breaks(conn) if r["scope"] == "restack"]
    assert len(rows) == 1 and rows[0]["kind"] == "arm_added" and rows[0]["break_date"] == "2026-10-19"
    staged = dbmod.connect(str(tmp_path / "staged.db"))
    paper_loop._note_restack_arm(staged, {"arms": {"restack": {"enabled": False, **ON}}})
    assert not [r for r in dbmod.measurement_breaks(staged) if r["scope"] == "restack"]
