"""The Builder's settings grid: one grid queued as an allow-listed `script` row with the backtest
package's own run and split in its command, its results read back, and Declare and the held-out test
handed to the backtest package by name and setting."""

from __future__ import annotations

from datetime import datetime

from cherrypick.core.clock import ET

from cherrypick.orchestrator import gridqueue as gq

NOW = datetime(2026, 11, 18, 10, 0, tzinfo=ET)
PLAN = {"ok": True, "name": "mine", "run": "builder-mine-2611161730", "licensed": False,
        "sessions": ["2025-11-17", "2026-11-16"], "split": "2026-07-18"}  # fmt: skip


class _Queue:
    def __init__(self, rows=()):
        self.added, self._rows = [], list(rows)

    def add(self, study, kind, args, **kw):
        self.added.append((study, kind, args, kw))
        return {"id": 513}

    def rows(self, study=None):
        assert study == gq.STUDY
        return self._rows


class _Backtest:
    def __init__(self, reply):
        self.calls, self.reply = [], reply

    def __call__(self, argv):
        self.calls.append(argv)
        return self.reply(argv) if callable(self.reply) else self.reply


def test_it_queues_the_grid_driver_with_the_plans_run_and_split():
    q, bt = _Queue(), _Backtest(PLAN)
    out = gq.queue({}, {"name": "mine"}, backtest=bt, add=q.add, rows=q.rows, now=NOW)
    assert out == {"ok": True, "name": "mine", "grid": "grid-mine-2611181000", "task": 513,
                   "run": "builder-mine-2611161730", "split": "2026-07-18", "licensed": False}  # fmt: skip
    assert bt.calls == [["grid", "plan", "--name", "mine"]]
    ((study, kind, args, kw),) = q.added
    assert (study, kind, kw) == ("grid", "script", {"step": "mine-2611181000"})
    assert args == {
        "path": "packages/backtest/scripts/exit_grid.py",
        "args": ["run", "--name", "mine", "--run", "builder-mine-2611161730", "--split", "2026-07-18",
                 "--out", "grid-mine-2611181000"],
    }  # fmt: skip


def test_no_long_enough_run_a_live_grid_or_a_bad_name_is_refused():
    q = _Queue()
    short = _Backtest({"ok": False, "error": "no Builder run of mine has 120 sessions"})
    out = gq.queue({}, {"name": "mine"}, backtest=short, add=q.add, rows=q.rows, now=NOW)
    assert out["code"] == "invalid" and not q.added
    busy = _Queue([{"id": 7, "step": "mine-2611180900", "status": "queued"}])
    out = gq.queue({}, {"name": "mine"}, backtest=_Backtest(PLAN), add=busy.add, rows=busy.rows, now=NOW)
    assert out["code"] == "conflict" and not busy.added
    for bad in ("Mine", "a--b", "", None):
        assert gq.queue({}, {"name": bad}, backtest=_Backtest(PLAN), add=q.add, rows=q.rows)["ok"] is False
    assert (
        gq.queue({}, {"name": "mine"}, backtest=_Backtest(None), add=q.add, rows=q.rows)["code"]
        == "unavailable"
    )


def test_grids_lists_this_automations_rows_and_results():
    rows = [{"id": 9, "step": "mine-2611181000", "status": "done"}, {"id": 10, "step": "mine-two-2611181000"}]
    bt = _Backtest({"ok": True, "grids": [{"name": "grid-mine-2611181000"}]})
    out = gq.grids({}, {"name": "mine"}, backtest=bt, rows=_Queue(rows).rows)
    assert [t["grid"] for t in out["tasks"]] == ["grid-mine-2611181000"]
    assert out["grids"] == [{"name": "grid-mine-2611181000"}] and bt.calls == [
        ["read", "settings-grids", "--name", "mine"]
    ]


def test_declare_and_holdout_name_one_grid_and_one_setting():
    bt = _Backtest({"ok": True})
    req = {"name": "mine", "id": "grid-mine-2611181000", "setting": "width=x1.5"}
    assert gq.declare({}, req, backtest=bt)["ok"] and gq.holdout({}, req, backtest=bt)["ok"]
    assert bt.calls == [
        ["grid", "declare", "--name", "mine", "--id", "grid-mine-2611181000", "--setting", "width=x1.5"],
        ["grid", "holdout", "--name", "mine", "--id", "grid-mine-2611181000", "--setting", "width=x1.5"],
    ]
    for bad in ("width; rm", "width=x1.5 --x", "stop_credit_multiple", ""):
        assert gq.declare({}, {**req, "setting": bad}, backtest=bt)["ok"] is False
    assert gq.declare({}, {**req, "id": "grid-other-2611181000"}, backtest=bt)["ok"] is False
    assert gq.declare({}, {**req, "setting": "profit_target_pct=off"}, backtest=bt)["ok"]


def test_a_grid_is_read_whole_or_reported_missing():
    bt = _Backtest(lambda argv: {"ok": True, "rows": []} if argv[-1].endswith("1000") else {"ok": False})
    assert gq.grid({}, {"name": "mine", "id": "grid-mine-2611181000"}, backtest=bt)["ok"]
    assert gq.grid({}, {"name": "mine", "id": "grid-mine-2611181001"}, backtest=bt)["code"] == "not_found"
