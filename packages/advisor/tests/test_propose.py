"""`propose`: a person starts an experiment through the same admission a model proposal takes.

Until it existed, the only way in was a hand-written, model-shaped reply fed to `admit`, which wrote a
checkpoint row with no model behind it. These pin that `propose` takes the model's path everywhere it
matters -- the bounds, the dedup -- and leaves the checkpoint record alone.
"""

from __future__ import annotations

import json

import fakes
import pytest

from cherrypick.advisor import store
from cherrypick.advisor.__main__ import main

SESSION = fakes.anchor_session()


@pytest.fixture
def home(tmp_home):
    fakes.seed_suite(tmp_home, SESSION)
    fakes.write_config(
        tmp_home,
        "meic",
        fakes.advice_block(
            {
                "stop_trigger_ratio": {"min": 0.85, "max": 0.95},
                "per_side_stop_management": {"choices": [True, False]},
            }
        ),
    )
    fakes.write_suite_config(tmp_home, {"enabled": True, "modules": {"meic": {"enabled": True}}})
    return tmp_home


def _propose(capsys, *params: str, name: str = "live-stop") -> dict:
    argv = [
        "propose",
        "--module",
        "meic",
        "--name",
        name,
        "--session",
        SESSION,
        "--sessions",
        "20",
        "--hypothesis",
        "the live stop costs more than it saves",
        "--success-metric",
        "paired net per session",
    ]
    for p in params:
        argv += ["--param", p]
    main(argv)
    return json.loads(capsys.readouterr().out)


def test_a_proposal_in_bounds_is_admitted_journaled_as_human_and_writes_no_checkpoint(home, capsys):
    out = _propose(capsys, "per_side_stop_management=true")
    assert out["ok"] and out["status"] == "active" and out["sessions"] == 20
    conn = store.connect()
    events = [
        r["event"]
        for r in conn.execute(
            "SELECT event FROM experiment_events WHERE experiment_id = ?", (out["experiment_id"],)
        )
    ]
    assert "proposed_by_human" in events and "created" in events
    assert json.loads(store.experiment(conn, out["experiment_id"])["params_json"]) == {
        "per_side_stop_management": True
    }
    assert conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 0


def test_a_proposal_outside_the_bounds_is_refused_and_nothing_is_stored(home, capsys):
    out = _propose(capsys, "stop_trigger_ratio=1.5")
    assert not out["ok"]
    assert store.connect().execute("SELECT COUNT(*) FROM experiments").fetchone()[0] == 0


def test_the_same_proposal_twice_is_one_experiment(home, capsys):
    first = _propose(capsys, "per_side_stop_management=true")
    second = _propose(capsys, "per_side_stop_management=true")
    assert second["already_admitted"] and second["experiment_id"] == first["experiment_id"]
    conn = store.connect()
    assert conn.execute("SELECT COUNT(*) FROM experiments").fetchone()[0] == 1
    assert (
        conn.execute("SELECT COUNT(*) FROM experiment_events WHERE event = 'proposed_by_human'").fetchone()[0]
        == 1
    )


def test_param_values_arrive_typed():
    from cherrypick.advisor.__main__ import _parse_param

    assert _parse_param("per_side_stop_management=true") == ("per_side_stop_management", True)
    assert _parse_param("stop_trigger_ratio=0.95") == ("stop_trigger_ratio", 0.95)
    assert _parse_param("entry_window_end=13:30") == ("entry_window_end", "13:30")
    with pytest.raises(ValueError):
        _parse_param("no-equals-sign")
