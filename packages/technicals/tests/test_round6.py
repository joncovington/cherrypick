"""Round 6's pure parts: the direction, the entry/pool split, the warning flip and the verdict."""

from __future__ import annotations

from datetime import UTC, datetime

from cherrypick.technicals import revisions, round6
from cherrypick.technicals.revisions import Event


def test_the_direction_needs_both_trends_and_the_score_to_agree():
    assert round6.direction(3, 4, 8) == 1
    assert round6.direction(-3, -3, 3) == -1
    assert round6.direction(2, 4, 10) == 0, "a 1M of +2 is not strong"
    assert round6.direction(4, 4, 7) == 0, "a score of 7 is not strong"
    assert round6.direction(-4, -4, 4) == 0, "KHC and BMY on the board: Bear, not Strong Bear"
    assert round6.direction(None, 4, 9) == 0


def test_tagged_sessions_are_entries_and_untagged_ones_are_the_pool():
    states = [
        ("A", "d1", 1, 1, "rating"),
        ("A", "d2", 1, 0, ""),
        ("B", "d1", 1, 0, ""),
        ("C", "d1", -1, 1, "target"),
    ]
    entries, by_date, by_name = round6.plan(states)
    assert [(e["test"], e["symbol"]) for e in entries] == [("against-bear", "C"), ("agree-bull", "A")]
    assert by_date[("d1", 1)] == ["B"] and by_name[("A", 1)] == ["d2"]
    assert ("d1", -1) not in by_date, "a tagged session never enters the pool"


def test_draws_come_from_the_same_direction_and_replay_exactly():
    entries, by_date, by_name = round6.plan(
        [("A", "d1", 1, 1, "rating")]
        + [(f"S{i}", "d1", 1, 0, "") for i in range(60)]
        + [("Z", "d1", -1, 0, "")]
    )
    work = round6.draw_plan(entries, by_date, by_name)
    drawn = {s for s, items in work.items() for _, _, _, is_draw in items if is_draw}
    assert len(drawn) == round6.DRAWS_SAME_DATE and "Z" not in drawn and "A" not in drawn
    assert work == round6.draw_plan(entries, by_date, by_name)


def test_a_warning_is_tested_on_the_pool_minus_the_entry():
    rows = [{"date": "d1", "r": -0.5, "base": 0.2}]
    assert round6._rows_for(rows, "against-bull") == [{"date": "d1", "r": 0.5, "base": -0.2}]
    assert round6._rows_for(rows, "agree-bull") == rows


def test_a_warning_passes_on_significance_alone_and_an_agree_test_also_needs_profit():
    def hyp(p, edge, expectancy):
        return {"test": {"judged": True, "p": p, "edge_r": edge}, "describe": {"expectancy_r": expectancy}}

    out = {"agree-bull": hyp(0.001, 0.2, -0.1), "against-bull": hyp(0.001, 0.2, -0.3)}
    round6.decide(out)
    assert out["agree-bull"]["significant"] and not out["agree-bull"]["passed"], "a loss never passes"
    assert out["against-bull"]["passed"]


def test_session_tags_carry_direction_and_kind():
    at = datetime(2026, 10, 13, 13, tzinfo=UTC)
    events = [
        Event("A", at, "F", "", "Buy", "main", 100.0, 110.0),
        Event("A", at, "G", "Buy", "Hold", "down", 100.0, 100.0),
        Event("B", at, "F", "", "Buy", "main", 100.0, 120.0),
    ]
    tags = round6.session_tags(events)
    assert tags["A"]["2026-10-13"] == (-1, "rating"), "the downgrade outranks the target raise"
    assert tags["B"]["2026-10-13"] == (1, "target")
    assert revisions.by_session(events)[("B", "2026-10-13")].kind == "target"
