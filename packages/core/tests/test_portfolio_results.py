"""core.portfolio over backtest results (`portfolio.results`, the manifest's `backtest` kind).

The guards, each broken on purpose while this was written: a trade is dated by its exit session, not
its entry; its net is the verdict pricing kind, not mid; a backtest arm's span starts at the run's
first session, so its quiet opening sessions count as zeros; and a licensed result marks the reading.
"""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

import cherrypick.core
from cherrypick.core import portfolio as P
from cherrypick.core.portfolio import __main__ as cli
from cherrypick.core.portfolio import results as R

DAYS = [f"2026-09-{d:02d}" for d in (14, 15, 16, 17, 18, 21, 22, 23, 24, 25)]


def _trade(arm, entry, exit_, verdict, mid=None):
    return {
        "arm": arm,
        "session": entry,
        "exit_session": exit_,
        "net": {"verdict": verdict, "mid": verdict + 5 if mid is None else mid},
    }


def _builder(trades, licensed=False):
    return {
        "name": "builder-x-2609250000",
        "arms": {"a": {}, "b": {}},
        "sessions": [DAYS[0], DAYS[-1]],
        "licensed": licensed,
        "trades": trades,
    }


def test_this_is_the_worktree_s_core():
    here = Path(__file__).resolve().parents[1]
    assert Path(cherrypick.core.__path__[0]).resolve().is_relative_to(here)


def test_a_trade_is_dated_by_its_exit_session_at_the_verdict_kind():
    trades = [_trade("a", DAYS[0], DAYS[2], -40.0), _trade("a", DAYS[1], DAYS[2], 10.0)]
    assert R.session_nets(R.trades_of(_builder(trades), "a")) == {DAYS[2]: -30.0}


def test_a_study_result_reads_its_arm_s_own_trade_list():
    doc = {
        "window": [DAYS[0], DAYS[-1]],
        "licence": "derived",
        "arms": {"x": {"trades": [_trade("x", DAYS[3], DAYS[3], 7.0)]}},
    }
    assert R.session_nets(R.trades_of(doc, "x")) == {DAYS[3]: 7.0}
    assert R.window_of(doc) == (DAYS[0], DAYS[-1])
    assert R.licensed(doc)


def test_an_arm_the_result_does_not_hold_refuses():
    with pytest.raises(R.ResultError):
        R.trades_of(_builder([]), "zzz")
    with pytest.raises(R.ResultError):
        R.trades_of({"window": [DAYS[0], DAYS[1]]}, "a")


def test_span_from_counts_a_backtest_s_quiet_first_sessions_as_zero():
    late = {DAYS[5]: 10.0, DAYS[7]: -4.0}
    plain = P.portfolio({"a": late}, start=DAYS[0], end=DAYS[-1])
    spanned = P.portfolio({"a": late}, start=DAYS[0], end=DAYS[-1], span_from={"a": DAYS[0]})
    assert plain["arms"][0]["periods_in_span"] == 5
    assert spanned["arms"][0]["periods_in_span"] == 10
    assert spanned["arms"][0]["first"] == DAYS[5] and spanned["arms"][0]["span_from"] == DAYS[0]


def _read(tmp_path, manifest, start=DAYS[0]):
    args = Namespace(manifest=json.dumps(manifest), start=start, end=None, period="day", weights="booked")
    return cli.cmd_read(args)


def test_the_cli_reads_a_backtest_arm_and_marks_a_licensed_one(tmp_path):
    trades = [_trade("a", d, d, v) for d, v in zip(DAYS, [5, -3, 4, -2, 6, -1, 3, 2, -4, 1], strict=True)]
    trades += [_trade("b", d, d, v) for d, v in zip(DAYS[4:], [2, 2, -1, 3, -2, 1], strict=True)]
    path = tmp_path / "builder-x-2609250000.json"
    path.write_text(json.dumps(_builder(trades)), encoding="utf-8")
    out = _read(
        tmp_path,
        [
            {"key": "bt:a", "kind": "backtest", "path": str(path), "arm": "a"},
            {"key": "bt:b", "kind": "backtest", "path": str(path), "arm": "b", "licensed": True},
        ],
    )
    assert out["ok"] and out["failures"] == []
    by = {a["key"]: a for a in out["arms"]}
    assert by["bt:a"]["net"] == 11.0
    assert by["bt:b"]["periods_in_span"] == 10  # the run's window, not b's first close
    assert out["sources"]["bt:a"] == {"kind": "backtest", "licensed": False, "window": [DAYS[0], DAYS[-1]]}
    assert out["sources"]["bt:b"]["licensed"] and out["licensed"]


def test_an_unreadable_backtest_or_unknown_kind_is_named_not_read_as_zeros(tmp_path):
    out = _read(
        tmp_path,
        [
            {"key": "gone", "kind": "backtest", "path": str(tmp_path / "missing.json"), "arm": "a"},
            {"key": "odd", "kind": "nope"},
        ],
    )
    assert {f["key"] for f in out["failures"]} == {"gone", "odd"}
    assert out["arms"] == [] and out["licensed"] is False
