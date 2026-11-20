"""Item 96's script (share_ladder_item96.py): invented bars only, no store read."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from cherrypick.backtest.study import rule as kit_rule

_spec = importlib.util.spec_from_file_location(
    "share_ladder_item96", Path(__file__).resolve().parents[1] / "scripts" / "share_ladder_item96.py"
)
m = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = m  # its dataclasses look their module up while being built
_spec.loader.exec_module(m)

DAYS = ["2024-09-18", "2024-09-19", "2024-09-20", "2024-09-23"]  # Wed, Thu, Fri, Mon


def test_the_declared_rules_are_complete():
    kit_rule.check_rule(m.RULE)
    assert m.PAIRED_RULE["arm"] == "session" and m.PAIRED_RULE["arm"] in m.ARMS
    assert [s["step"] for s in m.CHAIN] == ["smoke", "study"]


def test_a_night_bar_belongs_to_the_next_session():
    assert m.session_of_night("2024-09-19T21:00", DAYS) == "2024-09-20"  # Thursday evening: Friday
    assert m.session_of_night("2024-09-20T02:00", DAYS) == "2024-09-20"  # after midnight: that day
    assert m.session_of_night("2024-09-20T20:30", DAYS) == "2024-09-23"  # Friday evening: Monday
    assert m.session_of_night("2024-09-22T21:00", DAYS) == "2024-09-23"  # Sunday evening: Monday
    assert m.session_of_night("2024-09-20T05:00", DAYS) is None  # not a night minute


def _rows():
    closes = {d: 100.0 for d in DAYS}
    day = [
        ("2024-09-20T08:00", 95.0, 95.0, 95.0, 95.0),  # pre-market
        ("2024-09-20T10:00", 96.0, 96.0, 96.0, 96.0),
        ("2024-09-20T15:59", 97.0, 97.0, 97.0, 97.0),  # the last regular bar, for the daily guard
    ]
    night = [("2024-09-19T22:00", 92.0, 92.0, 92.0, 92.0)]  # under the 93.00 level overnight
    closes["2024-09-20"] = 97.0
    return m.merged_rows(day, night, DAYS), closes


def test_all_hours_buy_the_night_dip_paper_hours_never_see():
    rows, closes = _rows()
    allh = m.tape_from_rows(rows, closes, 0.005, "2024-09-20", None, {}, True)
    paper = m.tape_from_rows(rows, closes, 0.005, "2024-09-20", None, {}, False)
    assert [s.day for s in allh.sessions] == [s.day for s in paper.sessions] == ["2024-09-20"]
    got = m.run_arm(allh, "session", "fixed")
    # the session's first quote is a gap under the 93.00 level: filled at the ask, to the cent; its
    # 95.68 target is crossed between the 08:00 and 10:00 bars, so it sells at the target
    assert [(allh.tags[x.i], x.price) for x in got] == [("night", 92.0)]
    assert (got[0].sell, allh.tags[got[0].exit_i]) == (95.68, "day")
    assert m.run_arm(paper, "session", "fixed") == []


def test_paper_hours_drop_the_night_and_the_pre_market():
    rows, closes = _rows()
    paper = m.tape_from_rows(rows, closes, 0.005, "2024-09-20", None, {}, False)
    allh = m.tape_from_rows(rows, closes, 0.005, "2024-09-20", None, {}, True)
    assert paper.tags == ["day"] * 8
    assert allh.tags == ["night"] * 4 + ["pre"] * 4 + ["day"] * 8


def test_the_paired_rule_needs_funds_lots_and_a_positive_difference():
    def fund(diff, lots=10, cost=None):
        return {
            "session": {
                "diff": diff,
                "cost_diff": diff if cost is None else cost,
                "all_hours": {"lots": lots},
            }
        }

    per = {f"F{i}": fund(1.0 + 0.1 * i) for i in range(8)}
    out = m.judge_paired(per, list(per))
    assert out["verdict"] == "pass" and out["lots"] == 80
    assert m.judge_paired(per, list(per)[:7])["verdict"] == "no verdict"
    per["F0"] = fund(-50.0)
    assert m.judge_paired(per, list(per))["verdict"] == "fail"


def test_both_rules_must_pass():
    assert m.combined("pass", "pass") == "pass"
    assert m.combined("pass", "fail") == "fail"
    assert m.combined("no verdict", "pass") == "no verdict"
