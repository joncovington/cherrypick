"""The Builder's backtest: one explore run queued as an allow-listed `script` row, only where the
automation's own suggestion says our data covers it, and its run card read back by name."""

from __future__ import annotations

from datetime import datetime

from cherrypick.core.clock import ET

from cherrypick.orchestrator import automationrun as ar
from cherrypick.orchestrator import researchqueue

NOW = datetime(2026, 11, 16, 17, 30, tzinfo=ET)
POSSIBLE = {
    "blocks": ["structure"],
    "window": ["2025-11-14", "2026-11-13"],
    "needs": [
        {"field": "closes", "cadence": "1d", "covered": True, "source": "stock-history"},
        {"field": "chain", "cadence": "1m", "expiry": "0dte", "covered": True, "source": "recorded-0dte"},
    ],
    "possible": True,
}


def _view(**over):
    return {"automations": [{"name": "mine", "symbol": "SPX", "suggestion": POSSIBLE, **over}]}


class _Queue:
    def __init__(self, rows=()):
        self.added, self._rows = [], list(rows)

    def add(self, study, kind, args, **kw):
        self.added.append((study, kind, args, kw))
        return {"id": 401}

    def rows(self, study=None):
        assert study == ar.STUDY
        return self._rows


def _queue(view, q=None):
    q = q or _Queue()
    out = ar.queue({}, {"name": "mine"}, builder=lambda _cfg: view, add=q.add, rows=q.rows, now=NOW)
    return out, q


def test_it_queues_the_driver_over_the_suggestions_window_and_source():
    out, q = _queue(_view())
    assert out == {"ok": True, "name": "mine", "run": "builder-mine-2611161730", "task": 401,
                   "window": ["2025-11-14", "2026-11-13"], "source": "recorded-0dte"}  # fmt: skip
    ((study, kind, args, kw),) = q.added
    assert (study, kind, kw) == ("builder", "script", {"step": "mine-2611161730"})
    assert args == {
        "path": "packages/backtest/scripts/automation_backtest.py",
        "args": ["run", "--arms", "mine", "--symbol", "SPX", "--name", "builder-mine-2611161730",
                 "--from", "2025-11-14", "--to", "2026-11-13", "--source", "recorded-0dte"],
    }  # fmt: skip


def test_the_row_it_builds_passes_the_queues_own_checks():
    _out, q = _queue(_view())
    ((_study, kind, args, _kw),) = q.added
    argv = researchqueue.build_argv(kind, args, "python", tracked=lambda _root, _p: True)
    assert argv[1].replace("\\", "/").endswith(ar.DRIVER)


def test_it_refuses_without_a_suggestion_or_where_our_data_does_not_cover_it():
    out, q = _queue(_view(suggestion=None))
    assert out["ok"] is False and out["code"] == "invalid" and q.added == []
    short = {**POSSIBLE, "possible": False, "needs": [{**POSSIBLE["needs"][1], "covered": False}]}
    out, q = _queue(_view(suggestion=short))
    assert out["ok"] is False and "chain" in out["error"] and q.added == []


def test_it_refuses_an_unknown_automation_a_bad_name_and_an_unreadable_module():
    out, _q = _queue({"automations": []})
    assert out["code"] == "not_found"
    out = ar.queue({}, {"name": "Bad Name"}, builder=lambda _cfg: _view(), add=None, rows=None, now=NOW)
    assert out["ok"] is False and out["code"] == "bad_request"
    out, _q = _queue(None)
    assert out["code"] == "unavailable"


def test_a_second_run_while_one_is_live_is_refused_but_another_automations_is_not():
    live = _Queue([{"id": 400, "step": "mine-2611161700", "status": "running"}])
    out, q = _queue(_view(), live)
    assert out["code"] == "conflict" and q.added == []
    other = _Queue([{"id": 400, "step": "mine-two-2611161700", "status": "running"},
                    {"id": 399, "step": "mine-2611151700", "status": "done"}])  # fmt: skip
    out, q = _queue(_view(), other)
    assert out["ok"] is True and len(q.added) == 1


def test_a_queue_refusal_is_returned_not_raised():
    def refuse(*_a, **_kw):
        raise researchqueue.Refused("another live row has that name")

    out = ar.queue(
        {}, {"name": "mine"}, builder=lambda _cfg: _view(), add=refuse, rows=lambda study=None: [], now=NOW
    )
    assert out == {"ok": False, "error": "another live row has that name", "code": "invalid"}


def test_the_run_card_lists_this_automations_rows_only_and_its_results():
    rows = [
        {"id": 3, "step": "mine-2611161730", "status": "done", "exit_code": 0},
        {"id": 2, "step": "mine-two-2611161700", "status": "done"},
        {"id": 1, "step": "mine-2611151730", "status": "failed", "note": "no source covers"},
    ]
    seen = []

    def results(name):
        seen.append(name)
        return {"ok": True, "runs": [{"run": "builder-mine-2611161730"}]}

    out = ar.runs({}, {"name": "mine"}, rows=lambda study=None: rows, results=results)
    assert [t["id"] for t in out["tasks"]] == [3, 1]
    assert (
        out["tasks"][0]["run"] == "builder-mine-2611161730" and out["tasks"][1]["note"] == "no source covers"
    )
    assert out["runs"] == [{"run": "builder-mine-2611161730"}] and out["results_unavailable"] is False
    assert seen == ["mine"]
    unread = ar.runs({}, {"name": "mine"}, rows=lambda study=None: [], results=lambda _n: None)
    assert unread["results_unavailable"] is True and unread["runs"] == []


def test_one_runs_card_is_asked_for_by_its_exact_name_only():
    seen = []

    def read(name, run):
        seen.append((name, run))
        return {"ok": True, "run": run, "licensed": False}

    out = ar.run_card({}, {"name": "mine", "run": "builder-mine-2611161730"}, read=read)
    assert out["ok"] is True and seen == [("mine", "builder-mine-2611161730")]
    for bad in ("builder-mine-two-2611161730", "../builder-mine-2611161730", "builder-mine-26111617", 7):
        assert ar.run_card({}, {"name": "mine", "run": bad}, read=read)["code"] == "bad_request"
    assert seen == [("mine", "builder-mine-2611161730")]
    assert (
        ar.run_card({}, {"name": "mine", "run": "builder-mine-2611161730"}, read=lambda n, r: None)["code"]
        == "unavailable"
    )
    missing = ar.run_card(
        {}, {"name": "mine", "run": "builder-mine-2611161730"}, read=lambda n, r: {"ok": False, "error": "no"}
    )
    assert missing["code"] == "not_found"
