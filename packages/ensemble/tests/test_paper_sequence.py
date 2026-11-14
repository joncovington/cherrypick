"""`paper.decide_all` stacks the arms the way the backtest does: an arm that enters is visible to the arms
after it, and the snapshot the caller passed is not changed. A compact record replays to the same
decisions."""

from __future__ import annotations

import json

from market import CALENDAR, spx_snapshot

from cherrypick.ensemble import arms, paper

A = arms.load_arms()


def both_due_snapshot():
    # a flat range at 6000 for the first hour, the first spot above it at 13:00, when both arms are due
    tr = [(m, 6000.0) for m in range(570, 780)] + [(780, 6001.0)]
    return spx_snapshot(minute=780, spot=6001.0, prev_close=6000.0, tr=tr, oi_call=2000)


def test_a_later_arm_sees_the_arm_that_entered_before_it():
    snap = both_due_snapshot()
    chosen = {
        "orb-put-spread": {**A["orb-put-spread"], "gates": []},
        "put-spread-1300": A["put-spread-1300"],
    }
    out = {d.arm: d for d in paper.decide_all(chosen, snap)}
    assert out["orb-put-spread"].enter
    blocked = out["put-spread-1300"]
    assert not blocked.enter and blocked.reason == "gate:no_position_today"
    assert snap.entries == {} and snap.open_positions == []  # the caller's snapshot is untouched


def test_without_the_first_arm_the_second_is_not_blocked_by_it():
    snap = both_due_snapshot()
    alone = paper.decide_all({"put-spread-1300": A["put-spread-1300"]}, snap)
    assert alone[0].reason != "gate:no_position_today"


def test_a_compact_record_replays_to_the_same_decisions():
    snap = spx_snapshot(vol=0.2, oi_call=2000)
    live = paper.decide_all(A, snap)
    recorded = json.loads(json.dumps(paper.compact(snap)))
    assert "gex" not in recorded
    again = paper.replay(A, recorded, CALENDAR)
    assert [(d.arm, d.enter, d.reason, d.checks) for d in live] == [
        (d.arm, d.enter, d.reason, d.checks) for d in again
    ]
    assert any(d.enter for d in live)


def test_the_stacking_check_can_fail():
    snap = both_due_snapshot()
    chosen = {"orb-put-spread": {**A["orb-put-spread"], "gates": []}, "put-spread-1300": A["put-spread-1300"]}
    stacked = {d.arm: d.reason for d in paper.decide_all(chosen, snap)}
    from cherrypick.ensemble import engine

    unstacked = {n: engine.decide(a, snap).reason for n, a in chosen.items()}
    assert stacked != unstacked  # decided one by one on the same snapshot, the second would not be blocked
