"""The entry funnel (`entries`, `cli entries`): the sessions each automation was due, how many reached
each step of the decision and the causes of the ones that stopped there, from the session register, and
a Builder run's reasons through the same steps beside it."""

from __future__ import annotations

import json

import pytest

from cherrypick.automations import cli, db, entries


def _row(arm, session, outcome, refusal=None):
    return {"arm": arm, "symbol": "SPX", "entry_session": session, "outcome": outcome, "refusal": refusal}


@pytest.mark.parametrize(
    "reason, step",
    [
        ("no_fresh_quote", "data"),
        ("no_tick_in_window", "data"),
        ("needs_intraday", "data"),
        ("entry_not_due", "trigger"),
        ("no_breakout", "trigger"),
        ("max_open", "limits"),
        ("event:fomc", "limits"),
        ("no_expiry", "limits"),
        ("gate:vix_level", "gates"),
        ("gate:reward_risk", "structure"),
        ("strike:short_put", "structure"),
        ("unpriced", "structure"),
        ("group_risk", "size"),
        ("gate:not_a_gate", "due"),
        ("something_new", "due"),
    ],
)
def test_each_reason_belongs_to_the_step_of_the_decision_that_gives_it(reason, step):
    assert entries.step(reason) == step


def test_a_step_is_reached_by_every_session_that_did_not_stop_before_it():
    f = entries.funnel(
        {"entered": 6, "no_fresh_quote": 1, "entry_not_due": 2, "gate:vix_level": 3, "unpriced": 1}, pending=1
    )
    assert (f["due"], f["entered"], f["missed"], f["pending"], f["rate"]) == (13, 6, 7, 1, round(6 / 13, 4))
    reached = {s["step"]: (s["reached"], s["stopped"]) for s in f["steps"]}
    assert reached == {
        "due": (13, 0),
        "data": (13, 1),
        "trigger": (12, 2),
        "limits": (10, 0),
        "gates": (10, 3),
        "structure": (7, 1),
        "size": (6, 0),
    }
    # What passes the last step is what entered.
    last = f["steps"][-1]
    assert last["passed"] == last["reached"] - last["stopped"] == f["entered"]
    assert [s["passed"] for s in f["steps"]] == [13, 12, 10, 10, 7, 6, 6]
    assert f["most_lost"] == {
        "step": "gates",
        "label": "Passed its market gates",
        "stopped": 3,
        "share": round(3 / 13, 4),
    }
    # A tie goes to the earlier step.
    assert entries.funnel({"no_spot": 2, "unpriced": 2})["most_lost"]["step"] == "data"
    gates_step = next(s for s in f["steps"] if s["step"] == "gates")
    assert gates_step["causes"] == [{"reason": "gate:vix_level", "count": 3, "share": round(3 / 13, 4)}]


def test_no_sessions_reads_no_rate():
    f = entries.funnel({})
    assert (f["due"], f["rate"], f["most_lost"]) == (0, None, None)
    assert all(s["reached"] == 0 and s["reached_share"] is None for s in f["steps"])


def test_the_register_counts_last_reasons_and_leaves_pending_days_out():
    rows = [
        _row("a", "2026-11-16", "entered"),
        _row("a", "2026-11-17", "missed", "gate:vix_level"),
        _row("a", "2026-11-18", "missed", None),
        _row("a", "2026-11-19", "pending", "entry_not_due"),
        _row("b", "2026-11-18", "missed", "no_fresh_quote"),
    ]
    per = entries.from_register(rows)
    assert per["a"]["reasons"] == {"entered": 1, "gate:vix_level": 1, "no_tick_in_window": 1}
    assert (per["a"]["pending"], per["a"]["first"], per["a"]["last"]) == (1, "2026-11-16", "2026-11-18")
    assert per["b"]["reasons"] == {"no_fresh_quote": 1}


def test_paper_and_the_run_read_cause_by_cause_in_the_decisions_order():
    rows = [_row("a", "2026-11-16", "entered"), _row("a", "2026-11-17", "missed", "gate:vix_level")]
    out = entries.one("a", rows, {"entered": 70, "gate:vix_level": 20, "no_breakout": 10})
    assert out["run"]["rate"] == 0.7
    assert out["causes"] == [
        {"reason": "no_breakout", "step": "trigger", "paper": 0.0, "run": 0.1},
        {"reason": "gate:vix_level", "step": "gates", "paper": 0.5, "run": 0.2},
    ]
    assert [d["session"] for d in out["days"]] == ["2026-11-17", "2026-11-16"]
    assert out["days"][0]["step"] == "gates" and out["days"][1]["step"] is None


def test_without_a_run_its_side_is_none():
    out = entries.one("a", [_row("a", "2026-11-17", "missed", "unpriced")], None)
    assert out["run"] is None
    assert out["causes"] == [{"reason": "unpriced", "step": "structure", "paper": 1.0, "run": None}]


def test_the_list_holds_every_configured_automation_and_any_the_register_still_names():
    arms = {"a": {"group": "g", "symbol": "SPX", "enabled": True}, "b": {"group": "g", "symbol": "SPX"}}
    out = entries.view(arms, [_row("a", "2026-11-17", "entered"), _row("gone", "2026-11-10", "entered")])
    assert [(r["name"], r["configured"], r["funnel"]["due"]) for r in out] == [
        ("a", True, 1),
        ("b", True, 0),
        ("gone", False, 1),
    ]


def test_the_cli_reads_the_register_read_only_and_takes_the_runs_reasons(tmp_path, capsys):
    path = str(tmp_path / "paper_trades.db")
    conn = db.connect(path)
    db.save_entry(
        conn,
        {
            "arm": "a",
            "symbol": "SPX",
            "entry_session": "2026-11-17",
            "outcome": "missed",
            "refusal": "gate:vix_level",
        },
    )
    conn.close()
    assert (
        cli.main(["--db", path, "entries", "--name", "a", "--run-reasons", json.dumps({"entered": 3})]) == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["paper"]["due"] == 1 and out["run"]["rate"] == 1.0


def test_the_cli_reads_no_ledger_as_no_sessions(tmp_path, capsys):
    assert cli.main(["--db", str(tmp_path / "none.db"), "entries", "--name", "a"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["paper"]["due"] == 0 and not (tmp_path / "none.db").exists()
