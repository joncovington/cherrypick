"""The research queue (orchestrator/researchqueue.py): what a row may run, when it may start, and what
a run leaves behind.

The allow-list is the safety property -- the supervisor runs these as a service with the broker
credentials in reach -- so each refusal is pinned. The clock, the calendar, the spawn and the
notification are injected; no real process runs.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from cherrypick.orchestrator import researchqueue as rq

ET = rq.ET
PY = "python"


def _weekday(d: date) -> bool:
    return d.weekday() < 5


def _at(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=ET)


@pytest.fixture(autouse=True)
def no_backtest_read(monkeypatch):
    """No test spawns the backtest to ask where its stores live; the store check's own tests pass one."""
    monkeypatch.setattr(rq, "backtest_stores", lambda: None)


@pytest.fixture
def files(tmp_path):
    req = tmp_path / "requests.json"
    req.write_text(json.dumps({"requests": []}), encoding="utf-8")
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "packages").mkdir()
    (root / "scripts" / "study.py").write_text("print('{}')\n", encoding="utf-8")
    (root / "scripts" / "draft.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_path / "loose.py").write_text("print(1)\n", encoding="utf-8")
    return {"req": req, "root": root, "tmp": tmp_path}


def _tracked(root: Path, p: Path) -> bool:
    return p.name == "study.py"  # draft.py is present but never committed


# --- the allow-list -----------------------------------------------------------------------------


def test_a_fetch_builds_its_own_command_bounded_by_its_stop_by(files):
    argv = rq.build_argv(
        "fetch-chains", {"requests": str(files["req"]), "workers": 3}, PY, "2026-10-26T18:30"
    )
    assert argv == [PY, "-m", "cherrypick.backtest", "fetch-chains", "--requests", str(files["req"])] + [
        "--workers",
        "3",
        "--stop-by",
        "2026-10-26T18:30",
    ]


def test_a_path_fetch_needs_the_vendor_session_and_is_bounded_by_its_stop_by(files):
    argv = rq.build_argv("fetch-chain-paths", {"requests": str(files["req"])}, PY, "2026-10-28T18:30")
    assert argv == [PY, "-m", "cherrypick.backtest", "fetch-chain-paths", "--requests", str(files["req"])] + [
        "--workers",
        "3",
        "--stop-by",
        "2026-10-28T18:30",
    ]
    assert rq.KINDS["fetch-chain-paths"] == ((rq.THETADATA,), True)
    row = {"kind": "fetch-chain-paths", "args": json.dumps({"requests": "req.json"})}
    out = rq.result_of(row, {"complete": 2}, Path("q.log"))
    assert out["input"] == "req.json" and out["store"] == "backtest eod_chains.db"


def test_an_open_interest_fetch_takes_a_plan_needs_the_vendor_session_and_is_bounded(files):
    argv = rq.build_argv("fetch-oi", {"plan": str(files["req"])}, PY, "2026-11-02T18:30")
    assert argv == [PY, "-m", "cherrypick.backtest", "fetch-oi", "--plan", str(files["req"])] + [
        "--workers",
        "3",
        "--stop-by",
        "2026-11-02T18:30",
    ]
    assert rq.KINDS["fetch-oi"] == ((rq.THETADATA,), True)
    row = {"kind": "fetch-oi", "args": json.dumps({"plan": "plan.json"})}
    out = rq.result_of(row, {"complete": 2}, Path("q.log"))
    assert out["input"] == "plan.json" and out["store"] == "backtest open_interest.db"
    flow = rq.result_of({"kind": "fetch-flow", "args": "{}"}, {}, Path("q.log"))
    assert flow["store"] == "backtest flow store"


def test_anything_off_the_allow_list_is_refused(files):
    with pytest.raises(rq.Refused, match="unknown kind"):
        rq.build_argv("shell", {"cmd": "del /s *"}, PY)
    with pytest.raises(rq.Refused, match="not a file"):
        rq.build_argv("fetch-chains", {"requests": str(files["tmp"] / "missing.json")}, PY)
    with pytest.raises(rq.Refused, match="workers"):
        rq.build_argv("fetch-flow", {"plan": str(files["req"]), "workers": 40}, PY)


def test_a_script_must_be_committed_under_scripts_and_take_only_string_arguments(files):
    root = files["root"]
    ok = rq.build_argv(
        "script", {"path": "scripts/study.py", "args": ["--item", "17"]}, PY, root=root, tracked=_tracked
    )
    assert ok == [PY, str((root / "scripts" / "study.py").resolve()), "--item", "17"]
    with pytest.raises(rq.Refused, match="commit it first"):
        rq.build_argv("script", {"path": "scripts/draft.py"}, PY, root=root, tracked=_tracked)
    with pytest.raises(rq.Refused, match="under scripts/"):
        rq.build_argv("script", {"path": "../loose.py"}, PY, root=root, tracked=lambda r, p: True)
    with pytest.raises(rq.Refused, match="list of strings"):
        rq.build_argv(
            "script", {"path": "scripts/study.py", "args": "--x; rm"}, PY, root=root, tracked=_tracked
        )


def test_a_refused_row_is_never_added(files, tmp_path):
    db = tmp_path / "q.db"
    with pytest.raises(rq.Refused):
        rq.add("item17", "shell", {}, path=db)
    assert rq.rows(db) == []
    out = rq.add("item17", "fetch-chains", {"requests": str(files["req"])}, path=db)
    assert out["needs"] == "thetadata" and out["name"] == "item17/fetch-chains"
    (row,) = rq.rows(db)
    assert (row["study"], row["step"], row["kind"], row["status"]) == (
        "item17",
        "fetch-chains",
        "fetch-chains",
        "queued",
    )


def test_a_name_is_two_slugs_and_one_live_row_per_name(files, tmp_path):
    db = tmp_path / "q.db"
    req = {"requests": str(files["req"])}
    for bad_study in ("Item 17", "item_17", "item17/", "-item17", ""):
        with pytest.raises(rq.Refused, match="slug"):
            rq.add(bad_study, "fetch-chains", req, path=db)
    with pytest.raises(rq.Refused, match="slug"):
        rq.add("item17", "fetch-chains", req, step="Fetch Chains", path=db)
    rq.add("item17", "fetch-chains", req, path=db)
    with pytest.raises(rq.Refused, match="already queued as task 1"):
        rq.add("item17", "fetch-chains", req, path=db)
    rq.add("item17", "fetch-chains", req, step="fetch-chains-2016", path=db)  # another step is another row
    assert rq.cancel(1, db)["ok"]
    rq.add("item17", "fetch-chains", req, path=db)  # a cancelled row is not live: the name is free again
    assert [r["study"] for r in rq.rows(db, study="item17")] == ["item17"] * 3
    assert rq.rows(db, study="flow-tags") == []


# --- when a task may start ----------------------------------------------------------------------


def _row(**over):
    r = {"id": 1, "kind": "fetch-chains", "needs": "thetadata", "not_before": None, "stop_by": None}
    r.update(over)
    return r


def test_a_thetadata_task_waits_for_the_lock_and_the_backtest_fetch_window():
    assert rq.ready(_row(), _at("2026-10-26T14:00"), _weekday, False) is None
    assert "held by another" in rq.ready(_row(), _at("2026-10-26T14:00"), _weekday, True)
    assert "backtest-fetch window" in rq.ready(_row(), _at("2026-10-26T19:00"), _weekday, False)
    # Not a trading day: no nightly fetch, so no blackout.
    assert rq.ready(_row(), _at("2026-10-25T19:00"), _weekday, False) is None
    # A script needs no session: neither holds it back.
    assert rq.ready(_row(kind="script", needs=""), _at("2026-10-26T19:00"), _weekday, True) is None


def test_not_before_holds_a_row_and_a_passed_stop_by_never_starts_it():
    assert "held until" in rq.ready(
        _row(not_before="2026-10-27T09:00"), _at("2026-10-26T14:00"), _weekday, False
    )
    assert "stop-by has passed" in rq.ready(
        _row(stop_by="2026-10-26T12:00"), _at("2026-10-26T14:00"), _weekday, False
    )


def test_the_blackout_follows_the_nightly_fetch_time():
    assert rq.blackout({}) == rq.BLACKOUT == ("18:30", "20:30")
    assert rq.blackout({"backtest": {"fetch_at": "19:30"}}) == ("19:15", "21:15")
    moved = rq.blackout({"backtest": {"fetch_at": "19:30"}})
    assert rq.ready(_row(), _at("2026-10-26T18:45"), _weekday, False, moved) is None
    assert "window" in rq.ready(_row(), _at("2026-10-26T19:20"), _weekday, False, moved)


def test_the_stop_by_is_the_next_blackout_or_the_rows_own_if_sooner():
    # Saturday evening: the next blackout is Monday's.
    assert rq.stop_by_for(_row(), _at("2026-10-24T20:00"), _weekday) == "2026-10-26T18:30"
    assert rq.stop_by_for(_row(), _at("2026-10-26T10:00"), _weekday) == "2026-10-26T18:30"
    # After Monday's window: Tuesday's.
    assert rq.stop_by_for(_row(), _at("2026-10-26T21:00"), _weekday) == "2026-10-27T18:30"
    assert (
        rq.stop_by_for(_row(stop_by="2026-10-26T12:00"), _at("2026-10-26T10:00"), _weekday)
        == "2026-10-26T12:00"
    )
    assert rq.stop_by_for(_row(kind="script", needs=""), _at("2026-10-26T10:00"), _weekday) is None


# --- what a run leaves behind -------------------------------------------------------------------


def test_a_stopped_fetch_resumes_unless_its_own_stop_by_stopped_it():
    assert rq.outcome(_row(), 0, {"stopped": True}, _at("2026-10-26T18:30")) == "queued"
    assert (
        rq.outcome(_row(stop_by="2026-10-26T12:00"), 0, {"stopped": True}, _at("2026-10-26T12:00"))
        == "stopped"
    )
    assert rq.outcome(_row(), 0, {"stopped": False}, _at("2026-10-26T12:00")) == "done"
    assert rq.outcome(_row(), 1, {}, _at("2026-10-26T12:00")) == "failed"


def test_the_report_is_the_last_json_the_verb_printed():
    out = 'chains 500/900 (2.9/s)\n{\n "complete": 3,\n "nested": {\n  "a": 1\n },\n "stopped": true\n}\n'
    assert rq.last_json(out) == {"complete": 3, "nested": {"a": 1}, "stopped": True}
    assert rq.last_json("no report") == {}


class _Proc:
    def __init__(self, code: int, out: str):
        self.pid = 4242
        self.returncode = code
        self._out = out

    def communicate(self):
        return self._out, None


def _run(db, now, code=0, out='{\n "complete": 2,\n "stopped": false\n}\n', monkeypatch=None):
    sent = []
    spawned = []

    def spawn(argv, **kw):
        spawned.append(argv)
        return _Proc(code, out)

    result = rq.run_next(
        path=db,
        now=lambda: _at(now),
        is_trading_day=_weekday,
        spawn=spawn,
        notify=lambda level, title, msg: sent.append((level, title)),
    )
    return result, spawned, sent


def test_one_run_takes_the_oldest_ready_row_and_notifies(files, tmp_path, monkeypatch):
    monkeypatch.setattr(rq, "session_held", lambda state_dir=None: False)
    monkeypatch.setattr(rq, "log_dir", lambda: tmp_path / "logs")
    db = tmp_path / "q.db"
    rq.add("item17", "fetch-chains", {"requests": str(files["req"])}, step="first", path=db)
    rq.add("item17", "fetch-chains", {"requests": str(files["req"])}, step="second", path=db)
    result, spawned, sent = _run(db, "2026-10-24T20:00")
    assert result["ran"] == 1 and result["status"] == "done"
    assert spawned[0][-2:] == ["--stop-by", "2026-10-26T18:30"]
    assert sent == [("INFO", "Research task done: item17/first")]
    statuses = {r["step"]: r["status"] for r in rq.rows(db)}
    assert statuses == {"first": "done", "second": "queued"}


def test_a_finished_fetch_points_to_its_counts_its_input_and_its_log(files, tmp_path, monkeypatch):
    monkeypatch.setattr(rq, "session_held", lambda state_dir=None: False)
    monkeypatch.setattr(rq, "log_dir", lambda: tmp_path / "logs")
    db = tmp_path / "q.db"
    rq.add("item17", "fetch-chains", {"requests": str(files["req"])}, path=db)
    _run(db, "2026-10-24T20:00", out='{\n "complete": 2,\n "refused": 1,\n "stopped": false\n}\n')
    result = json.loads(rq.rows(db)[0]["result"])
    assert result["report"] == {"complete": 2, "refused": 1, "stopped": False}
    assert result["input"] == str(files["req"]) and "eod_chains.db" in result["store"]
    assert result["log"].endswith("1.log")


def test_a_script_must_write_its_declared_output_to_be_done(files, tmp_path, monkeypatch):
    monkeypatch.setattr(rq, "session_held", lambda state_dir=None: False)
    monkeypatch.setattr(rq, "log_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(rq, "repo_root", lambda: files["root"])
    db = tmp_path / "q.db"
    report = tmp_path / "out" / "item17-report.md"
    script = {"path": "scripts/study.py"}
    rq.add(
        "item17",
        "script",
        script,
        step="study",
        output=str(report),
        path=db,
        root=files["root"],
        tracked=_tracked,
    )
    monkeypatch.setattr(rq, "git_tracked", _tracked)
    _, _, sent = _run(db, "2026-10-26T10:00", out="")
    row = rq.rows(db)[0]
    assert row["status"] == "failed" and "was not written" in row["note"]
    assert sent == [("WARNING", "Research task failed: item17/study")]
    # Written this time: done, and the row points at it.
    report.parent.mkdir()
    report.write_text("verdict", encoding="utf-8")
    rq.add(
        "item17",
        "script",
        script,
        step="study",
        output=str(report),
        path=db,
        root=files["root"],
        tracked=_tracked,
    )
    _, _, sent = _run(db, "2026-10-26T10:00", out="")
    row = rq.rows(db)[0]
    assert row["status"] == "done"
    assert json.loads(row["result"])["output"] == str(report) and json.loads(row["result"])["output_exists"]
    assert sent[0][1] == "Research task done: item17/study"


def test_a_failure_notifies_and_a_stopped_fetch_goes_back_to_the_queue(files, tmp_path, monkeypatch):
    monkeypatch.setattr(rq, "session_held", lambda state_dir=None: False)
    monkeypatch.setattr(rq, "log_dir", lambda: tmp_path / "logs")
    db = tmp_path / "q.db"
    rq.add("item17", "fetch-chains", {"requests": str(files["req"])}, step="bad", path=db)
    _, _, sent = _run(db, "2026-10-26T10:00", code=1, out="")
    assert sent == [("WARNING", "Research task failed: item17/bad")]
    rq.add("item17", "fetch-chains", {"requests": str(files["req"])}, step="long", path=db)
    _, _, sent = _run(db, "2026-10-26T10:00", out='{\n "stopped": true\n}\n')
    assert sent == []  # stopped for the window: no news, it resumes
    long_row = next(r for r in rq.rows(db) if r["step"] == "long")
    assert long_row["status"] == "queued" and long_row["runs"] == 1 and "resumes" in long_row["note"]


def test_nothing_starts_in_the_window_and_an_orphaned_running_row_is_queued_again(
    files, tmp_path, monkeypatch
):
    monkeypatch.setattr(rq, "session_held", lambda state_dir=None: False)
    monkeypatch.setattr(rq, "log_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(rq, "pid_alive", lambda pid: False)
    db = tmp_path / "q.db"
    rq.add("item17", "fetch-chains", {"requests": str(files["req"])}, path=db)
    result, spawned, _ = _run(db, "2026-10-26T19:00")
    assert result["ran"] is None and spawned == [] and "window" in result["waiting"][1]
    with rq.connect(db) as conn:
        conn.execute("UPDATE tasks SET status = 'running', pid = 99999 WHERE id = 1")
    result, spawned, _ = _run(db, "2026-10-26T21:00")
    assert result["recovered"] == [1]
    assert spawned and rq.rows(db)[0]["status"] == "done"


def test_only_a_queued_row_is_cancelled(files, tmp_path):
    db = tmp_path / "q.db"
    rq.add("item17", "fetch-chains", {"requests": str(files["req"])}, path=db)
    assert rq.cancel(1, db)["status"] == "cancelled"
    assert rq.cancel(1, db)["ok"] is False
    assert rq.cancel(7, db)["ok"] is False


def _script_row(files, db, output, stores):
    return rq.add(
        "item19",
        "script",
        {"path": "scripts/study.py"},
        step="fetch",
        output=output,
        path=db,
        root=files["root"],
        tracked=_tracked,
        stores=lambda: stores,
    )


def test_an_output_naming_a_vendor_store_must_be_that_store(files, tmp_path):
    vendor = tmp_path / "data" / "backtest" / "vendor" / "thetadata"
    stores = {"ok": True, "dir": str(vendor), "files": ["SPX-60s.db", "SPX-time-warp.db"]}
    db = tmp_path / "q.db"
    # item 19, 2026-10-26: the store's name one directory up, so a complete fetch read as failed.
    with pytest.raises(rq.Refused, match="lives at"):
        _script_row(files, db, str(vendor.parent / "SPX-time-warp.db"), stores)
    assert rq.rows(db) == []
    assert _script_row(files, db, str(vendor / "SPX-time-warp.db"), stores)["status"] == "queued"


def test_an_output_that_is_no_store_or_unknown_stores_is_not_judged(files, tmp_path):
    vendor = tmp_path / "vendor" / "thetadata"
    stores = {"ok": True, "dir": str(vendor), "files": ["SPX-60s.db"]}
    db = tmp_path / "q.db"
    assert _script_row(files, db, str(tmp_path / "results" / "verdict.json"), stores)["status"] == "queued"
    rq.cancel(1, db)
    # The backtest could not say where its stores live: the check cannot fire, and says so in its doc.
    assert _script_row(files, db, str(tmp_path / "SPX-60s.db"), None)["status"] == "queued"
