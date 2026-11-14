"""#1079: how many of the register's passes chance alone would give, overall and per family."""

from __future__ import annotations

from cherrypick.orchestrator import hypotheses as hy


def _reg(rows):
    return {"hypotheses": [{"id": i, "study": f"item{i}", **r} for i, r in enumerate(rows, start=18)]}


def test_expected_passes_are_items_judged_times_the_bar():
    failed = [{"status": "failed", "structure": "iron-condor"}] * 38
    passed = [{"status": "passed", "structure": "iron-condor"}] * 2
    out = hy.false_passes(_reg(failed + passed), alpha=0.025)
    p_two_or_more = 1 - 0.975**40 - 40 * 0.025 * 0.975**39
    assert out["overall"] == {
        "judged": 40,
        "passed": 2,
        "expected_by_chance": 1.0,
        "p_this_many_by_chance": round(p_two_or_more, 4),
    }


def test_declared_retired_and_observation_rows_are_not_judged_and_no_verdict_is():
    reg = _reg(
        [{"status": s, "structure": "x"} for s in ("declared", "retired", "observation", "no_verdict")]
    )
    assert hy.false_passes(reg, alpha=0.05)["overall"]["judged"] == 1


def test_families_name_their_passes():
    reg = _reg(
        [
            {"status": "passed", "structure": "short-put"},
            {"status": "failed", "structure": "short-put"},
            {"status": "failed", "structure": "iron-fly"},
        ]
    )
    fams = hy.false_passes(reg, alpha=0.05)["families"]
    assert fams["short-put"]["passed_ids"] == [18] and fams["iron-fly"]["passed"] == 0
    assert fams["iron-fly"]["p_this_many_by_chance"] == 1.0


def test_nothing_judged_reads_none():
    assert hy.false_passes(_reg([]), alpha=0.05)["overall"]["p_this_many_by_chance"] is None


def test_the_board_carries_it():
    out = hy.board(_reg([{"status": "passed", "structure": "x"}]), [], read=lambda p: (None, None))
    assert (
        out["false_passes"]["overall"]["passed"] == 1 and out["false_passes"]["alpha"] == hy.FALSE_PASS_ALPHA
    )
