"""The declared rule as data (#1069): checked strictly, read without importing, one run cut at its
splits, a holdout read only after a pass, and the verdict block's trade counts. Invented trades."""

from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from cherrypick.backtest.study import checks as ck
from cherrypick.backtest.study import rule as ru

RULE = {
    "t_bar": 2.0,
    "min_n": 80,
    "holdout_min_n": 30,
    "tail": {"per_year": "sample", "resamples": 2000, "seed": 7},
    "delta": False,
    "splits": ["2021-01-01"],
}


def weekly(start, n, draw, seed=1):
    rng = random.Random(seed)
    d0 = date.fromisoformat(start)
    return [
        {
            "date": (d0 + timedelta(days=7 * i)).isoformat(),
            "net": draw(rng),
            "bp": 1.0,
            "days": 7,
            "rate": 0.0,
        }
        for i in range(n)
    ]


def good(r):
    return r.gauss(0.004, 0.002)


def bad(r):
    return r.gauss(-0.001, 0.002)


def test_one_run_is_cut_at_the_split_by_entry_date():
    rows = weekly("2020-11-06", 20, good)
    design, holdout = ru.cut(rows, ["2021-01-01"])
    assert all(r["date"] < "2021-01-01" for r in design) and all(r["date"] >= "2021-01-01" for r in holdout)
    assert len(design) + len(holdout) == 20 and len(ru.cut(rows, [])) == 1


def test_a_pass_in_both_parts_passes_and_counts_every_trade():
    rows = weekly("2018-01-05", 156, good) + weekly("2021-01-01", 60, good, seed=2)
    out = ru.evaluate(RULE, {"A": rows})
    assert out["verdict"] == {"A": ck.PASS} and out["arms"]["A"]["n"] == 216
    assert [p["verdict"] for p in out["arms"]["A"]["parts"]] == [ck.PASS, ck.PASS]


def test_a_design_fail_leaves_the_holdout_unread():
    rows = weekly("2018-01-05", 156, bad) + weekly("2021-01-01", 60, good, seed=2)
    a = ru.evaluate(RULE, {"A": rows})["arms"]["A"]
    assert a["verdict"] == ck.FAIL and a["parts"][1]["verdict"] == ru.NOT_READ and "bars" not in a["parts"][1]


def test_a_holdout_that_fails_fails_the_arm():
    rows = weekly("2018-01-05", 156, good) + weekly("2021-01-01", 60, bad, seed=2)
    assert ru.evaluate(RULE, {"A": rows})["verdict"] == {"A": ck.FAIL}


def test_several_splits_read_in_order_and_stop_at_the_first_fail():
    rule = {**RULE, "splits": ["2020-01-01", "2021-01-01", "2022-01-01"]}
    rows = (weekly("2018-01-05", 104, good) + weekly("2020-01-03", 52, good, seed=2)
            + weekly("2021-01-01", 52, bad, seed=3) + weekly("2022-01-07", 52, good, seed=4))  # fmt: skip
    parts = ru.evaluate(rule, {"A": rows})["arms"]["A"]["parts"]
    assert [p["verdict"] for p in parts] == [ck.PASS, ck.PASS, ck.FAIL, ru.NOT_READ]
    assert parts[1]["from"] == "2020-01-01" and parts[1]["before"] == "2021-01-01"


def test_a_crash_inside_a_short_holdout_still_fails_the_tail_bar():
    # 60 holdout trades are under the bootstrap's floor alone; read with the design they are not, and a
    # crash in them shows. Capital still reads the holdout alone.
    crash = weekly("2021-01-01", 60, lambda r: -0.25 if r.random() < 0.05 else 0.006, seed=9)
    rows = weekly("2018-01-05", 156, good) + crash
    hold = ru.evaluate(RULE, {"A": rows})["arms"]["A"]["parts"][1]
    assert hold["bars"]["tail"]["reads"] == "so far" and hold["bars"]["tail"]["n"] == 216
    assert hold["bars"]["capital"]["n"] == 60 and "tail" in hold["failed"]


def test_an_arm_with_no_trades_is_counted_so_the_queue_can_fail_it():
    out = ru.evaluate(RULE, {"A": weekly("2018-01-05", 156, good), "B": []})
    assert out["arms"]["B"]["n"] == 0 and out["verdict"]["B"] == ck.NO_VERDICT
    assert ru.zero_trade_arms(out) == ["B"]
    assert ru.zero_trade_arms({"verdict": "fail"}) == []


def test_the_delta_bar_applies_when_declared():
    rows = [{**r, "financed": r["net"] + 0.001} for r in weekly("2018-01-05", 156, good)]
    out = ru.evaluate({**RULE, "delta": True, "splits": []}, {"A": rows})
    assert out["arms"]["A"]["parts"][0]["failed"] == ["delta"]


@pytest.mark.parametrize(
    "change, why",
    [
        ({"t_bar": None}, "missing"),
        ({"extra": 1}, "unknown"),
        ({"min_n": 0}, "whole number"),
        ({"tail": {"per_year": 40, "resamples": 10}}, "exactly"),
        ({"delta": "yes"}, "true or false"),
        ({"splits": ["2022-01-01", "2021-01-01"]}, "oldest first"),
        ({"splits": ["2021-13-01"]}, "month"),
    ],
)
def test_a_malformed_rule_is_refused(change, why):
    rule = {k: v for k, v in {**RULE, **change}.items() if v is not None}
    with pytest.raises(ru.BadRule, match=why):
        ru.check_rule(rule)


def test_no_splits_needs_no_holdout_floor_but_a_split_does():
    whole = {k: v for k, v in RULE.items() if k != "holdout_min_n"} | {"splits": []}
    assert ru.check_rule(whole)
    with pytest.raises(ru.BadRule, match="holdout_min_n"):
        ru.check_rule({k: v for k, v in RULE.items() if k != "holdout_min_n"})


def test_a_scripts_rule_is_read_without_importing_it(tmp_path):
    p = tmp_path / "x_item99.py"
    p.write_text(f"RULE = {RULE!r}\nraise SystemExit('never imported')\n", encoding="utf-8")
    assert ru.read_rule(p) == RULE
    q = tmp_path / "y_item99.py"
    q.write_text("RULE = dict(t_bar=2)\n", encoding="utf-8")
    with pytest.raises(ru.BadRule, match="literal"):
        ru.read_rule(q)
    (tmp_path / "z.py").write_text("X = 1\n", encoding="utf-8")
    assert ru.read_rule(tmp_path / "z.py") is None
