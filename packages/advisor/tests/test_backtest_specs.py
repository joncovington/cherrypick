"""Backtest specs: the model may propose one, never run one. Off by default; when on, the same gates
as an experiment (the module's switch and its advice bounds); never a chosen window; queued for
packages/backtest, whose design result comes back into the deep pack and nowhere else."""

from __future__ import annotations

import json

import fakes
import pytest

from cherrypick.advisor import __main__ as cli
from cherrypick.advisor import experiments, factpack, proposals, store

SESSION = fakes.anchor_session()
FLIES_BOUNDS = {"wing_width_strikes": {"min": 1, "max": 5}}


def _home(tmp_home, *, on=True, flies=True):
    """Seed the suite once per test; later calls only flip the switches."""
    if not (tmp_home / "data" / "meic" / "paper_trades.db").exists():
        fakes.seed_suite(tmp_home, SESSION)
    fakes.write_config(tmp_home, "flies", fakes.advice_block(FLIES_BOUNDS, base_key="base_arm"))
    fakes.write_suite_config(
        tmp_home,
        {"enabled": True, "backtest_proposals": on, "modules": {"flies": {"enabled": flies}}},
    )
    return tmp_home


@pytest.fixture
def conn():
    c = store.connect()
    yield c
    c.close()


def _spec(**over):
    raw = {
        "kind": "backtest_spec",
        "module": "flies",
        "name": "wider wings",
        "base_arm": "control",
        "params": {"wing_width_strikes": 4},
        "hypothesis": "wider wings complete more on trend days",
        **over,
    }
    typed, reason = proposals._typed(raw)
    assert reason is None, reason
    return typed


def test_a_spec_parses_and_a_chosen_window_is_refused():
    assert _spec()["base_arm"] == "control"
    for key in ("window", "start", "dates", "holdout"):
        _typed, reason = proposals._typed({**_spec()["raw"], key: "2022-01-01"})
        assert reason and "cannot choose its history" in reason
    _typed, reason = proposals._typed({**_spec()["raw"], "params": {"wing_width_strikes": 4, "start": "x"}})
    assert reason and "cannot choose its history" in reason


def test_off_by_default_a_spec_is_rejected(tmp_home, conn):
    _home(tmp_home, on=False)
    out = experiments.queue_backtest(conn, session=SESSION, proposal=_spec(), proposal_id=None)
    assert out["ok"] is False and out["reason"].startswith("backtest_proposals_off")


def test_on_a_spec_within_bounds_is_queued_once(tmp_home, conn):
    _home(tmp_home)
    first = experiments.queue_backtest(conn, session=SESSION, proposal=_spec(), proposal_id=None)
    again = experiments.queue_backtest(conn, session=SESSION, proposal=_spec(), proposal_id=None)
    assert first["ok"] and first["request_id"] == again["request_id"]
    (req,) = store.backtest_requests(conn, "queued")
    assert (req["module"], req["base_arm"], req["params"]) == ("flies", "control", {"wing_width_strikes": 4})


def test_the_module_s_bounds_and_switch_still_rule(tmp_home, conn):
    _home(tmp_home)
    out = experiments.queue_backtest(
        conn, session=SESSION, proposal=_spec(params={"wing_width_strikes": 9}), proposal_id=None
    )
    assert out["ok"] is False, "out of the module's own advice bounds"
    out = experiments.queue_backtest(conn, session=SESSION, proposal=_spec(module="meic"), proposal_id=None)
    assert out["ok"] is False and out["reason"].startswith("no_backtest_for_module")
    _home(tmp_home, flies=False)
    out = experiments.queue_backtest(conn, session=SESSION, proposal=_spec(), proposal_id=None)
    assert out["ok"] is False and out["reason"].startswith("module_advice_disabled")


def test_the_runner_reads_the_queue_and_writes_a_result_back(tmp_home, conn, tmp_path, capsys):
    _home(tmp_home)
    rid = experiments.queue_backtest(conn, session=SESSION, proposal=_spec(), proposal_id=None)["request_id"]
    conn.commit()
    assert cli.main(["backtest-requests"]) == 0
    assert [r["id"] for r in json.loads(capsys.readouterr().out)["requests"]] == [rid]
    result = tmp_path / "r.json"
    result.write_text(json.dumps({"window": "design", "sessions": 600}), encoding="utf-8")
    assert (
        cli.main(
            [
                "backtest-result",
                "--id",
                str(rid),
                "--status",
                "done",
                "--run-id",
                "R1",
                "--result-file",
                str(result),
            ]
        )
        == 0
    )
    capsys.readouterr()
    (done,) = store.backtest_requests(conn, "done")
    assert done["run_id"] == "R1" and done["result"]["sessions"] == 600


def test_the_deep_pack_carries_backtests_only_when_switched_on(tmp_home, conn):
    _home(tmp_home)
    experiments.queue_backtest(conn, session=SESSION, proposal=_spec(), proposal_id=None)
    conn.commit()
    pack = factpack.build(SESSION, factpack.DEEP_SLOT)
    assert [r["name"] for r in pack["backtests"]["requests"]] == ["wider wings"]
    _home(tmp_home, on=False)
    assert "backtests" not in factpack.build(SESSION, factpack.DEEP_SLOT)
