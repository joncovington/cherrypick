"""The scorecard: one row per arm and automation, each reading passed through, and the look worst first."""

from cherrypick.orchestrator import scorecard


def _vs(sessions, mean_diff, excludes_zero=False, to_exclude=None):
    return {
        "sessions": sessions,
        "mean_diff": mean_diff,
        "ci_excludes_zero": excludes_zero,
        "sessions_to_exclude_zero": to_exclude,
    }


def _arm(name, *, sessions=20, net=100.0, mean=5.0, excludes=False, control="control", vs=None):
    return {
        "arm": name,
        "sessions": sessions,
        "trades": sessions * 2,
        "net": net,
        "max_drawdown": 50.0,
        "mean_session_net": mean,
        "session_net_ci_excludes_zero": excludes,
        "control": control if name != "control" else None,
        "vs_control": vs,
    }


MEIC = {
    "module": "meic",
    "status": "ok",
    "era": {"start": "2026-11-03"},
    "arms": [
        _arm("control"),
        _arm("ready-worse", vs=_vs(20, -30.0, excludes_zero=True, to_exclude=12)),
        _arm("losing", sessions=12, net=-900.0, mean=-75.0, excludes=True, vs=_vs(12, -1.0, to_exclude=400)),
        _arm(
            "thin-losing", sessions=4, net=-300.0, mean=-75.0, excludes=True, vs=_vs(4, -1.0, to_exclude=40)
        ),
        _arm("declared", vs=_vs(20, 2.0, to_exclude=30)),
    ],
}
REGISTER = {
    "hypotheses": [
        {
            "id": 73,
            "status": "passed",
            "forward": {
                "module": "meic",
                "arm": "declared",
                "reference": {"file": "{home}/data/backtest/results/x.json"},
            },
        },
        {
            "id": 75,
            "status": "passed",
            "forward": [{"module": "automations", "arm": "auto-a", "reference": None}],
        },
    ]
}
CHECKS = [
    {
        "id": 73,
        "study": "item73",
        "module": "meic",
        "arm": "declared",
        "n": 41,
        "min_obs": 40,
        "state": "drift",
    },
    {
        "id": 75,
        "study": "item75",
        "module": "automations",
        "arm": "auto-a",
        "n": 3,
        "min_obs": 30,
        "state": "collecting",
    },
]
MEMBERS = [
    {"name": "auto-a", "state": "on", "verdict": "passed"},
    {"name": "auto-failed-on", "state": "on", "verdict": "failed"},
    {"name": "auto-failed-off", "state": "off_own", "verdict": "failed"},
]


def _rows(**kw):
    doc = scorecard.build([MEIC], CHECKS, REGISTER, MEMBERS, **kw)
    return doc, {(r["module"], r["arm"]): r for r in doc["rows"]}


def test_every_arm_and_automation_has_a_row():
    _, rows = _rows()
    assert set(rows) == {
        ("meic", "control"),
        ("meic", "ready-worse"),
        ("meic", "losing"),
        ("meic", "thin-losing"),
        ("meic", "declared"),
        ("automations", "auto-a"),
        ("automations", "auto-failed-on"),
        ("automations", "auto-failed-off"),
    }


def test_each_look_and_none_where_nothing_calls_for_one():
    _, rows = _rows()
    assert rows[("meic", "declared")]["look"] == "forward_drift"
    assert rows[("meic", "ready-worse")]["look"] == "decision_due"
    assert rows[("meic", "losing")]["look"] == "losing"
    assert rows[("meic", "thin-losing")]["look"] is None  # four sessions are not read
    assert rows[("automations", "auto-failed-on")]["look"] == "failed_running"
    assert rows[("automations", "auto-failed-off")]["look"] is None  # failed, but switched off
    assert rows[("meic", "control")]["look"] is None and rows[("meic", "control")]["vs_control"] is None


def test_the_worst_look_wins_and_leads_the_list():
    doc, _ = _rows()
    looks = [r["look"] for r in doc["rows"]]
    assert looks[:4] == ["forward_drift", "decision_due", "losing", "failed_running"]
    assert all(lk is None for lk in looks[4:])
    # Then rows with something to read, MEIC's in its comparison's order, before a blank automation.
    rest = [(r["module"], r["arm"]) for r in doc["rows"][4:]]
    assert rest.index(("meic", "control")) < rest.index(("automations", "auto-failed-off"))
    assert doc["counts"] == {"forward_drift": 1, "decision_due": 1, "losing": 1, "failed_running": 1}


def test_research_verdicts_and_the_sample():
    _, rows = _rows()
    declared = rows[("meic", "declared")]
    assert declared["research"] == {"id": 73, "verdict": "passed"}
    assert declared["sample"] == {"basis": "forward", "have": 41, "need": 40}
    auto = rows[("automations", "auto-a")]
    assert (
        auto["research"] == {"id": None, "verdict": "passed"} and auto["vs_backtest"]["state"] == "collecting"
    )
    assert auto["sample"] == {"basis": "forward", "have": 3, "need": 30}
    worse = rows[("meic", "ready-worse")]
    assert worse["vs_control"]["reading"] == "ready" and worse["vs_control"]["direction"] == "worse"
    assert rows[("meic", "control")]["sample"] == {"basis": "sessions", "have": 20, "need": None}


def test_a_forward_state_on_a_licensed_reference_is_withheld_while_results_are_hidden():
    _, rows = _rows(is_licensed=lambda path: "/backtest/results/" in path)
    vb = rows[("meic", "declared")]["vs_backtest"]
    assert vb["withheld"] is True and vb["state"] is None
    assert rows[("meic", "declared")]["look"] is None  # a hidden state calls for no look
    # `collecting` is the paper count alone, and a check with no reference names no licensed file.
    assert rows[("automations", "auto-a")]["vs_backtest"]["withheld"] is False
    _, open_rows = _rows(is_licensed=None)
    assert open_rows[("meic", "declared")]["vs_backtest"]["state"] == "drift"
