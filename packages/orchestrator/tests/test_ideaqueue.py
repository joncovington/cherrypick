"""The Builder's idea search: one search queued as an allow-listed `script` row with the backtest
package's own run and split in its command, its results read back, and Declare and the held-out test
handed to the backtest package by name."""

from __future__ import annotations

from datetime import datetime

from cherrypick.core.clock import ET

from cherrypick.orchestrator import ideaqueue as iq

NOW = datetime(2026, 11, 18, 10, 0, tzinfo=ET)
PLAN = {"ok": True, "name": "mine", "run": "builder-mine-2611161730", "licensed": False,
        "sessions": ["2025-11-17", "2026-11-16"], "split": "2026-07-18"}  # fmt: skip


class _Queue:
    def __init__(self, rows=()):
        self.added, self._rows = [], list(rows)

    def add(self, study, kind, args, **kw):
        self.added.append((study, kind, args, kw))
        return {"id": 512}

    def rows(self, study=None):
        assert study == iq.STUDY
        return self._rows


class _Backtest:
    def __init__(self, reply):
        self.calls, self.reply = [], reply

    def __call__(self, argv):
        self.calls.append(argv)
        return self.reply(argv) if callable(self.reply) else self.reply


def test_it_queues_the_driver_with_the_plans_run_and_split():
    q, bt = _Queue(), _Backtest(PLAN)
    out = iq.queue({}, {"name": "mine"}, backtest=bt, add=q.add, rows=q.rows, now=NOW)
    assert out == {"ok": True, "name": "mine", "search": "ideas-mine-2611181000", "task": 512,
                   "run": "builder-mine-2611161730", "split": "2026-07-18", "licensed": False}  # fmt: skip
    assert bt.calls == [["ideas", "plan", "--name", "mine"]]
    ((study, kind, args, kw),) = q.added
    assert (study, kind, kw) == ("ideas", "script", {"step": "mine-2611181000"})
    assert args == {
        "path": "packages/backtest/scripts/idea_search.py",
        "args": ["run", "--name", "mine", "--run", "builder-mine-2611161730", "--split", "2026-07-18",
                 "--out", "ideas-mine-2611181000"],
    }  # fmt: skip


def test_no_long_enough_run_a_live_search_or_a_bad_name_is_refused():
    q = _Queue()
    short = _Backtest({"ok": False, "error": "no Builder run of mine has 120 sessions"})
    out = iq.queue({}, {"name": "mine"}, backtest=short, add=q.add, rows=q.rows, now=NOW)
    assert out["code"] == "invalid" and "120 sessions" in out["error"] and not q.added
    busy = _Queue([{"id": 7, "step": "mine-2611180900", "status": "running"}])
    out = iq.queue({}, {"name": "mine"}, backtest=_Backtest(PLAN), add=busy.add, rows=busy.rows, now=NOW)
    assert out["code"] == "conflict" and not busy.added
    other = _Queue([{"id": 8, "step": "mine-two-2611180900", "status": "running"}])
    assert iq.queue({}, {"name": "mine"}, backtest=_Backtest(PLAN), add=other.add, rows=other.rows, now=NOW)[
        "ok"
    ]
    for bad in ("Mine", "a--b", "", None):
        assert iq.queue({}, {"name": bad}, backtest=_Backtest(PLAN), add=q.add, rows=q.rows)["ok"] is False
    gone = iq.queue({}, {"name": "mine"}, backtest=_Backtest(None), add=q.add, rows=q.rows)
    assert gone["code"] == "unavailable"


def test_searches_lists_this_automations_rows_and_results():
    rows = [{"id": 9, "step": "mine-2611181000", "status": "done", "created_at": "t"},
            {"id": 10, "step": "mine-two-2611181000", "status": "done"}]  # fmt: skip
    q = _Queue(rows)
    bt = _Backtest({"ok": True, "searches": [{"name": "ideas-mine-2611181000"}]})
    out = iq.searches({}, {"name": "mine"}, backtest=bt, rows=q.rows)
    assert [t["search"] for t in out["tasks"]] == ["ideas-mine-2611181000"]
    assert out["searches"] == [{"name": "ideas-mine-2611181000"}] and out["results_unavailable"] is False
    assert bt.calls == [["read", "idea-searches", "--name", "mine"]]


def test_declare_and_holdout_name_one_search_and_one_idea():
    bt = _Backtest({"ok": True, "row": {}})
    req = {"name": "mine", "id": "ideas-mine-2611181000", "idea": "vix:above:3&weekday:bin:0"}
    assert iq.declare({}, req, backtest=bt)["ok"]
    assert iq.holdout({}, req, backtest=bt)["ok"]
    assert bt.calls == [
        [
            "ideas",
            "declare",
            "--name",
            "mine",
            "--id",
            "ideas-mine-2611181000",
            "--idea",
            "vix:above:3&weekday:bin:0",
        ],
        [
            "ideas",
            "holdout",
            "--name",
            "mine",
            "--id",
            "ideas-mine-2611181000",
            "--idea",
            "vix:above:3&weekday:bin:0",
        ],
    ]
    assert iq.declare({}, {**req, "id": "ideas-other-2611181000"}, backtest=bt)["ok"] is False
    assert iq.declare({}, {**req, "idea": "vix; rm"}, backtest=bt)["ok"] is False
    refused = _Backtest({"ok": False, "error": "this idea is declared already"})
    out = iq.declare({}, req, backtest=refused)
    assert out == {"ok": False, "error": "this idea is declared already", "code": "invalid"}


def test_a_search_is_read_whole_or_reported_missing():
    bt = _Backtest(lambda argv: {"ok": True, "rows": []} if argv[-1].endswith("1000") else {"ok": False})
    assert iq.search({}, {"name": "mine", "id": "ideas-mine-2611181000"}, backtest=bt)["ok"]
    assert iq.search({}, {"name": "mine", "id": "ideas-mine-2611181001"}, backtest=bt)["code"] == "not_found"
