"""core.portfolio: how a set of arms behaves held together (docs/strategy-analysis-plan.md Phase 5).

The guards, each broken on purpose while this was written: a period before an arm's first close is
not zero-filled; equal-risk weighting leaves correlations unchanged and equalises the arms' spread;
and the two correlation definitions are kept apart.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from cherrypick.core import portfolio as P
from cherrypick.core.portfolio import __main__ as cli

# Ten consecutive NYSE sessions (2026-09-14 .. 09-25; no holidays).
DAYS = [f"2026-09-{d:02d}" for d in (14, 15, 16, 17, 18, 21, 22, 23, 24, 25)]
START, END = DAYS[0], DAYS[-1]


def _series(values, days=DAYS):
    return {d: v for d, v in zip(days, values, strict=True) if v is not None}


def test_pearson_refuses_what_it_cannot_measure():
    assert P.pearson([1, 2], [2, 4]) is None  # under three points
    assert P.pearson([1, 1, 1], [1, 2, 3]) is None  # no variance
    assert P.pearson([1, 2, 3], [2, 4, 6]) == 1.0


def test_zero_filled_and_similarity_answer_different_questions():
    # b trades only on a's good days, and moves against a then: on shared days they disagree, but
    # over every day b's zeros line up with a's bad ones.
    a = _series([10, -10, 10, -10, 10, -10, 10, -10, 12, -9])
    b = _series([1, None, 3, None, 2, None, 4, None, 1, None])
    out = P.portfolio({"a": a, "b": b}, start=START, end=END)
    [pair] = out["correlation"]
    assert pair["zero_filled"] > 0.5
    assert pair["similarity"] < 0
    assert (pair["zero_filled_periods"], pair["similarity_periods"]) == (10, 5)


def test_an_arm_contributes_nothing_before_its_first_close():
    a = _series([5, -3, 2, 4, -1, 3, -2, 1, 2, -1])
    late = _series([None, None, None, None, None, 2, -1, 3, -2, 1])
    out = P.portfolio({"a": a, "late": late}, start=START, end=END, weights="booked", min_periods=3)
    [pair] = out["correlation"]
    assert pair["zero_filled_periods"] == 5  # not ten: before 09-21 "late" did not exist
    assert out["shared"] == {
        "from": "2026-09-21",
        "periods": 5,
        "thin": False,
        "arms": ["a", "late"],
        "too_new": [],
    }
    assert [c["net"] for c in out["contribution"]] == [10.0, 3.0]


def test_an_arm_too_new_to_count_does_not_empty_everyone_else_s_window():
    a = _series([5, -8, 2, 4, -6, 3, -2, 1, 2, -1])
    b = _series([1, 2, -3, 1, -1, 2, 2, -2, 1, 1])
    newcomer = _series([None] * 9 + [4])
    out = P.portfolio({"a": a, "b": b, "new": newcomer}, start=START, end=END, min_periods=5)
    assert out["shared"]["periods"] == 10 and out["shared"]["too_new"] == ["new"]
    assert out["effective_arms"] is not None


def test_identical_arms_are_one_bet_and_offsetting_arms_cancel_their_drawdown():
    a = _series([5, -8, 2, 4, -6, 3, -2, 1, 2, -1])
    same = P.portfolio({"a": a, "b": dict(a)}, start=START, end=END, min_periods=3)
    assert same["effective_arms"] == 1.0
    assert same["drawdown"]["benefit"] == 0.0
    mirror = {d: -v for d, v in a.items()}
    hedged = P.portfolio({"a": a, "b": mirror}, start=START, end=END)
    assert hedged["drawdown"]["combined"] == 0.0 and hedged["drawdown"]["benefit"] == 1.0


def test_two_uncorrelated_equal_arms_count_as_two():
    a = _series([1, -1, 1, -1, 1, -1, 1, -1, 1, -1])
    b = _series([1, 1, -1, -1, 1, 1, -1, -1, 1, 1])  # covariance with a exactly 0
    out = P.portfolio({"a": a, "b": b}, start=START, end=END, min_periods=3)
    assert out["correlation"][0]["zero_filled"] == 0.0
    assert 1.95 <= out["effective_arms"] <= 2.0


def test_equal_risk_scales_a_large_book_down_and_leaves_correlation_alone():
    a = _series([5, -8, 2, 4, -6, 3, -2, 1, 2, -1])
    c = _series([1, 2, -3, 1, -1, 2, 2, -2, 1, 1])
    big = {d: v * 100 for d, v in c.items()}
    booked = P.portfolio({"a": a, "big": big}, start=START, end=END, weights="booked")
    equal = P.portfolio({"a": a, "big": big}, start=START, end=END, weights="equal_risk")
    assert booked["correlation"][0]["zero_filled"] == equal["correlation"][0]["zero_filled"]
    scaled = {x["key"]: x["sd"] * x["scale"] for x in equal["arms"]}
    assert scaled["a"] == pytest.approx(scaled["big"], rel=1e-3)
    assert abs(booked["contribution"][1]["share"]) > 0.9  # booked, the big book is the portfolio


def test_worst_periods_say_how_many_arms_lost():
    a = _series([5, -8, 2, 4, -6, 3, -2, 1, 2, -1])
    b = _series([1, -2, 1, -1, 3, 1, None, 1, -1, 1])
    out = P.portfolio({"a": a, "b": b}, start=START, end=END, weights="booked")
    worst = out["worst_periods"][0]
    assert (worst["period"], worst["net"], worst["arms_lost"], worst["arms_traded"]) == (
        "2026-09-15",
        -10.0,
        2,
        2,
    )


def test_weeks_sum_each_arm_by_iso_week():
    a = _series([1, 1, 1, 1, 1, 2, 2, 2, 2, 2])
    out = P.portfolio({"a": a}, start=START, end=END, period="week", weights="booked")
    assert out["window"]["combined_periods"] == 2
    assert out["arms"][0]["periods_traded"] == 2 and out["arms"][0]["net"] == 15.0


def test_an_arm_with_no_closes_is_named_not_zero_filled():
    a = _series([5, -8, 2, 4, -6, 3, -2, 1, 2, -1])
    out = P.portfolio({"a": a, "idle": {}}, start=START, end=END)
    assert out["no_closes"] == ["idle"] and out["correlation"] == []


def test_the_cli_reads_each_arm_as_the_metrics_cli_does(tmp_path, capsys):
    path = tmp_path / "meic.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE ic_trades (symbol TEXT, risk_profile TEXT, pnl REAL, fees REAL, exit_time TEXT, "
        "experiment_id TEXT)"
    )
    conn.executemany(
        "INSERT INTO ic_trades VALUES ('SPX', ?, ?, 1.0, ?, ?)",
        [
            ("control", 10.0, "2026-09-14T15:45:00", None),
            ("control", 20.0, "2026-09-14T15:50:00", None),
            ("control", -5.0, "2026-09-15T15:45:00", None),
            ("advised:control", 99.0, "2026-09-14T15:45:00", "exp-1"),  # another group: not mixed in
        ],
    )
    conn.commit()
    conn.close()
    manifest = [{"key": "meic:control", "db": str(path), "schema": "meic_ic", "tag": "control", "era": None}]
    cli.main(
        ["read", "--manifest", json.dumps(manifest), "--start", START, "--end", END, "--weights", "booked"]
    )
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] and doc["failures"] == []
    assert doc["arms"][0]["net"] == 22.0  # (10-1) + (20-1) + (-5-1)


def test_an_unreadable_arm_is_named_not_read_as_zeros(capsys, tmp_path):
    manifest = [
        {
            "key": "x",
            "db": str(tmp_path / "missing" / "nope.db"),
            "schema": "meic_ic",
            "tag": "c",
            "era": None,
        }
    ]
    cli.main(["read", "--manifest", json.dumps(manifest), "--start", START, "--end", END])
    doc = json.loads(capsys.readouterr().out)
    assert [f["key"] for f in doc["failures"]] == ["x"] and doc["arms"] == []
