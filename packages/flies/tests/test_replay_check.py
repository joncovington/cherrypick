"""flies.replay_check: the replay's decisions against paper's, each from paper's own book.

The guards, each broken on purpose while this was written: paper's book as of an instant shows a
position completed later as its opening vertical (never as the fly it became); a position entered
later is absent; and the pass test needs ten sessions as well as every declared threshold.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from cherrypick.core.clock import ET

from cherrypick.flies import replay_check as RC


def _t(hour, minute, second=0):
    return datetime(2026, 10, 15, hour, minute, second, tzinfo=ET)


ROW = {
    "position_id": "P",
    "entry_mode": "legged",
    "kind": "fly",
    "side": "put",
    "center": 7700.0,
    "credit": 2.3,
    "debit": 2.05,
    "net": 0.25,
    "fees": 6.89,
    "settlement_fees": 0.0,
    "entry_time": _t(10, 6).isoformat(),
    "completed_at": _t(10, 20).isoformat(),
    "best_completing_debit": 2.0,
    "status": "settled",
    "settlement_price": 7744.0,
    "pnl": 18.11,
    "gross_pnl": 25.0,
    "pinned": 1,
}


def test_a_position_is_absent_before_its_entry():
    assert RC.as_of(ROW, _t(10, 5).timestamp()) is None
    assert RC.as_of(ROW, _t(10, 6).timestamp()) is None  # entered AT t is the decision being tested


def test_before_its_completion_it_is_the_opening_vertical():
    s = RC.as_of(ROW, _t(10, 10).timestamp())
    assert (s["kind"], s["net"], s["debit"], s["completed_at"]) == ("short_vertical", 2.3, None, None)
    assert s["fees"] == 6.89 / 2 and s["best_completing_debit"] is None
    assert (s["status"], s["pnl"], s["settlement_price"], s["pinned"]) == ("open", None, None, 0)


def test_after_its_completion_it_is_the_fly_still_open():
    s = RC.as_of(ROW, _t(10, 30).timestamp())
    assert (s["kind"], s["net"], s["debit"], s["status"]) == ("fly", 0.25, 2.05, "open")
    assert s["pnl"] is None


def test_a_never_completed_vertical_keeps_its_fees_less_settlement():
    strand = {
        **ROW,
        "kind": "short_vertical",
        "completed_at": None,
        "debit": None,
        "fees": 13.44,
        "settlement_fees": 10.0,
    }
    s = RC.as_of(strand, _t(15, 0).timestamp())
    assert s["kind"] == "short_vertical" and s["fees"] == pytest.approx(3.44)


def test_consecutive_hits_of_one_decision_are_one_episode():
    hits = [(100.0, ("put", 7700)), (115.0, ("put", 7700)), (130.0, ("put", 7700)), (400.0, ("put", 7700)),
            (115.0, ("call", 7710))]  # fmt: skip
    eps = RC._episodes(hits, gap=30.0)
    assert sorted((e["key"], e["samples"]) for e in eps) == [
        (("call", 7710), 1),
        (("put", 7700), 1),
        (("put", 7700), 3),
    ]


def _session(**over):
    base = {
        "ok": True, "paper_entries": 10, "entries_matched": 10, "extra_entry_episodes": [],
        "paper_completions": 8, "completions_matched": 8, "early_completion_episodes": [],
        "matched_entries": [{"credit_diff": 0.05}] * 10,
        "completion_outcome": {"paper_completed": 8, "replay_also_completed": 8, "paper_stranded": 2,
                               "replay_also_stranded": 2},
    }  # fmt: skip
    return {**base, **over}


def test_the_pass_test_needs_ten_sessions_and_every_threshold():
    nine = RC.summarise([_session() for _ in range(9)])
    assert nine["checks"] and all(nine["checks"].values()) and nine["passes"] is False
    ten = RC.summarise([_session() for _ in range(10)])
    assert ten["passes"] is True and ten["completion_outcome_agreement"]["share"] == 1.0
    late = RC.summarise([_session(completions_matched=5) for _ in range(10)])
    assert late["checks"]["completions_matched"] is False and late["passes"] is False
