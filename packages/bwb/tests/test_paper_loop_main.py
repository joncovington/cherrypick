"""`python -m cherrypick.bwb.paper_loop`, the supervisor's entry point, keeps its flags, its lock
discipline and its messages.

The loop's scaffolding (lock, interval driver, once driver) is shared; these pin what the supervisor
and the console rely on from this module: which flags exist, that `--once` prints one JSON tick and
releases the lock, that a held lock is a quiet exit 0, and that a failing tick costs one tick."""

import json
import time

import pytest

from cherrypick.bwb import paper_loop as pl

FLAGS = [
    "--config",
    "--db",
    "--stream-cache",
    "--once",
    "--interval",
    "--status",
    "--force",
    "--settle",
    "--price",
    "--date",
]
SKIPPED = {"ok": True, "skipped": "another paper loop is already running"}
DRIFT = ["x_col"]


class _Stop(BaseException):
    """Raised from the fake sleep to end a resident loop the way a kill would."""


@pytest.fixture
def run(monkeypatch, tmp_path):
    state = {"ticks": 0, "released": 0, "held": False, "logs": [], "fail_first": False, "cadence": []}
    monkeypatch.setattr(pl.climod, "load_config", lambda *a, **k: {})
    monkeypatch.setattr(pl.db, "stale_writer_columns", lambda conn: DRIFT)
    monkeypatch.setattr(pl.db, "connect", lambda path: "CONN")
    monkeypatch.setattr(pl.stream_request, "register", lambda *a, **k: None)

    def fake_run_once(*a, **k):
        state["ticks"] += 1
        if state["fail_first"] and state["ticks"] == 1:
            raise RuntimeError("boom")
        return {"ran": state["ticks"]}

    monkeypatch.setattr(pl, "run_once", fake_run_once)
    monkeypatch.setattr(pl, "_acquire_loop_lock", lambda *a, **k: not state["held"])

    def fake_release():
        state["released"] += 1

    monkeypatch.setattr(pl, "_release_loop_lock", fake_release)
    monkeypatch.setattr(pl, "_log", state["logs"].append)

    def fake_sleep(seconds):
        state["slept"] = seconds
        if state["ticks"] >= 3:
            raise _Stop

    monkeypatch.setattr(time, "sleep", fake_sleep)
    base = ["--db", str(tmp_path / "ledger.db"), "--stream-cache", "CACHE"]

    def go(*argv):
        return pl.main([*base, *argv])

    go.state = state
    go.monkeypatch = monkeypatch
    return go


def test_help_lists_every_flag(capsys):
    with pytest.raises(SystemExit) as stop:
        pl.main(["--help"])
    assert stop.value.code == 0
    out = capsys.readouterr().out
    for flag in FLAGS:
        assert flag in out


def test_no_mode_is_a_usage_error(run):
    with pytest.raises(SystemExit) as stop:
        run()
    assert stop.value.code == 2


def test_an_unknown_flag_is_a_usage_error():
    with pytest.raises(SystemExit) as stop:
        pl.main(["--no-such-flag"])
    assert stop.value.code == 2


def test_once_prints_one_json_tick_and_releases_the_lock(run, capsys):
    assert run("--once") == 0
    assert json.loads(capsys.readouterr().out) == {"ran": 1}
    assert run.state["released"] == 1


def test_once_exits_quietly_when_another_loop_holds_the_lock(run, capsys):
    run.state["held"] = True
    assert run("--once") == 0
    assert json.loads(capsys.readouterr().out) == SKIPPED
    assert run.state["ticks"] == 0 and run.state["released"] == 0


def test_interval_ticks_every_interval_until_stopped_and_releases_the_lock(run):
    with pytest.raises(_Stop):
        run("--interval", "5", "--force")
    assert run.state["ticks"] == 3 and run.state["slept"] == 5
    assert run.state["released"] == 1
    assert run.state["logs"] == [
        "loop starting, interval 5s, cache CACHE",
        "WARNING: 1 ledger column(s) this checkout will never write — x_col.",
    ]


def test_interval_exits_quietly_when_another_loop_holds_the_lock(run, capsys):
    run.state["held"] = True
    assert run("--interval", "5", "--force") == 0
    assert json.loads(capsys.readouterr().out) == SKIPPED
    assert run.state["ticks"] == 0 and run.state["released"] == 0
    assert run.state["logs"] == []


def test_a_failing_tick_is_logged_and_the_loop_continues(run):
    run.state["fail_first"] = True
    with pytest.raises(_Stop):
        run("--interval", "5", "--force")
    assert run.state["ticks"] == 3
    assert "iteration error (continuing): RuntimeError: boom" in run.state["logs"]


def test_the_loop_ends_at_the_close_and_says_so(run):
    run.monkeypatch.setattr(pl, "in_session", lambda minute: False)
    assert run("--interval", "5") == 0
    assert run.state["ticks"] == 0 and run.state["released"] == 1
    assert run.state["logs"][-1] == "session closed"
