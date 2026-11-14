"""#1074: a verdict block becomes a Result paragraph and a register outcome in words, with no study
figure in either."""

from __future__ import annotations

from cherrypick.orchestrator import verdictdraft as vd


def _part(verdict, failed=(), n=120, mean=0.0123):
    bars = {"capital": {"verdict": "pass", "n": n, "mean": mean, "t": 2.71}}
    return {
        "from": None,
        "before": "2021-06-01",
        "n": n,
        "verdict": verdict,
        "failed": list(failed),
        "bars": bars,
    }


BLOCK = {
    "rule": {"splits": ["2021-06-01"]},
    "arms": {
        "cash": {"n": 160, "verdict": "fail",
                 "parts": [_part("fail", ["capital", "tail"]), {"n": 40, "verdict": "not read"}]},
        "margin": {"n": 160, "verdict": "pass", "parts": [_part("pass"), _part("pass", n=40)]},
    },
    "verdict": {"cash": "fail", "margin": "pass"},
}  # fmt: skip


def _draft(block=BLOCK, provisional=False):
    return vd.draft(77, block, day="2026-11-14", provisional=provisional,
                    results_dir="~/.cherrypick/data/backtest/results/research/item77/")  # fmt: skip


def test_a_passing_arm_makes_the_item_a_pass_and_is_named():
    d = _draft()
    assert d["status"] == "passed"
    assert d["paragraph"].startswith("- **Result (2026-11-14): pass, `margin`.**")


def test_each_arm_says_which_bars_failed_where_and_what_was_not_read():
    p = _draft()["paragraph"]
    assert "`cash` fails the capital bar" in p and "the tail bar" in p and "in the design part" in p
    assert "the holdouts after it are not read" in p
    assert "`margin` passes every bar in every part" in p


def test_no_figure_from_the_block_reaches_the_draft():
    d = _draft()
    text = d["paragraph"] + d["outcome"]
    assert "0.0123" not in text and "2.71" not in text and "120" not in text
    assert d["figures"] == []


def test_the_figure_check_catches_a_number_and_allows_the_item_dates_and_arms():
    allowed = {"77", "2021-06-01", "n3"}
    assert vd.figures("item 77 split 2021-06-01, arm n3", allowed) == []
    assert vd.figures("mean 0.42 over 1,200 trades", allowed) == ["0.42", "1,200"]


def test_a_failing_holdout_and_a_provisional_verdict_read_so():
    block = {
        "rule": {"splits": ["2021-06-01"]},
        "arms": {"a": {"n": 9, "verdict": "fail", "parts": [_part("pass"), _part("fail", ["delta"])]}},
        "verdict": {"a": "fail"},
    }
    d = _draft(block, provisional=True)
    assert d["status"] == "failed" and "(2026-11-14, provisional): fail, every arm." in d["paragraph"]
    assert "fails the delta bar (edge beyond its financed delta) in the first holdout" in d["paragraph"]
    assert d["outcome"].startswith("provisional: ") and d["figures"] == []
