"""Adding an underlying to the full set from the Builder (`fullsetadd`): sized by the backtest's own
planner first, refused unless new work may use the vendor or over one click's cap, then its rows queued
from the script's `ADD_CHAIN`, and the automation's backtest after them."""

from __future__ import annotations

from cherrypick.orchestrator import automationrun as ar
from cherrypick.orchestrator import fullsetadd as fa
from cherrypick.orchestrator import researchqueue, studychain

PLAN = {"ok": True, "symbol": "ABC", "from": "2025-11-17", "through": "2026-11-16",
        "requests": {"near_ABC.json": 1200, "sessions": 250}, "concurrent": 2}  # fmt: skip
OPEN = lambda: {"available": True, "reasons": []}  # noqa: E731


class _Script:
    def __init__(self, doc=PLAN):
        self.calls, self.doc = [], doc

    def __call__(self, args):
        self.calls.append(args)
        return self.doc


class _Rows:
    def __init__(self):
        self.calls = []

    def __call__(self, rows, study, dry_run=False, **_):
        self.calls.append((dry_run, rows))
        return {"ok": True, "added": [] if dry_run else [{"id": 500 + i} for i in range(len(rows))]}


def test_the_plan_asks_the_backtest_planner_dry_and_says_whether_one_click_may_add_it():
    script = _Script()
    out = fa.plan({}, {"symbol": "abc"}, script)
    assert script.calls == [["add", "ABC", "--dry-run"]]
    assert out["within_cap"] and out["cap_sessions"] == fa.CAP_SESSIONS == 300
    assert fa.plan({}, {"symbol": "abc!"}, script)["code"] == "bad_request"
    assert fa.plan({}, {"symbol": "ABC", "from": 20250101}, script)["code"] == "bad_request"
    assert fa.plan({}, {"symbol": "ABC"}, lambda a: None)["code"] == "unavailable"


def test_an_addition_queues_its_chain_at_its_priority_each_row_after_the_last_and_the_run_after_all():
    script, rows, runs = _Script(), _Rows(), []
    out = fa.add({}, {"symbol": "ABC", "run": "mine"}, run=script, feature=OPEN, queue_rows=rows,
                 queue_run=lambda cfg, req: runs.append(req) or {"ok": True, "task": 9})  # fmt: skip
    assert out["ok"] and out["symbol"] == "ABC" and out["tasks"][0] == 500
    assert script.calls == [["add", "ABC", "--dry-run"], ["add", "ABC"]], "sized first, recorded once it fits"
    (dry, checked), (live, queued) = rows.calls
    assert dry is True and live is False and checked == queued
    steps = [r["step"] for r in queued]
    assert steps[0] == "add-abc-zero" and steps[-1] == "add-abc-mid-2"
    assert {r["priority"] for r in queued} == {fa.PRIORITY} and {r["study"] for r in queued} == {"fullset"}
    assert queued[0]["args"]["store_tag"] == "added" and queued[0]["args"]["dataset"] == "ABC"
    assert queued[0]["args"]["from"] == "2025-11-17"
    assert all(b["after"] == f"fullset/{a['step']}" for a, b in zip(queued, queued[1:], strict=False))
    assert runs == [{"name": "mine", "after": "fullset/add-abc-mid-2"}] and out["run"]["task"] == 9


def test_an_addition_is_refused_while_new_work_may_not_use_the_vendor_or_over_one_clicks_cap():
    script, rows = _Script(), _Rows()
    shut = fa.add({}, {"symbol": "ABC"}, run=script, feature=lambda: {"available": False,
                  "reasons": ["vendor licence ended"]}, queue_rows=rows)  # fmt: skip
    assert shut["code"] == "unavailable" and "licence ended" in shut["error"] and not script.calls
    big = _Script({**PLAN, "requests": {"sessions": 301}})
    over = fa.add({}, {"symbol": "ABC"}, run=big, feature=OPEN, queue_rows=rows)
    assert over["code"] == "invalid" and "301 sessions" in over["error"]
    assert big.calls == [["add", "ABC", "--dry-run"]] and rows.calls == [], "nothing written or queued"


def test_a_live_row_of_the_same_name_refuses_before_anything_is_written():
    script = _Script()

    def clash(rows, study, dry_run=False, **_):
        raise researchqueue.Refused("already queued or running: fullset/add-abc-zero")

    out = fa.add({}, {"symbol": "ABC"}, run=script, feature=OPEN, queue_rows=clash)
    assert out["code"] == "conflict" and script.calls == [["add", "ABC", "--dry-run"]]


def test_a_named_chain_is_read_with_its_placeholders_filled_before_it_is_checked():
    chain = studychain.read_chain(researchqueue.repo_root() / fa.SCRIPT, "ADD_CHAIN",
                                  {"symbol": "BRK.B", "sym": "brk-b", "from": "2025-11-17"})  # fmt: skip
    assert chain[0]["step"] == "add-brk-b-zero" and chain[0]["task_args"]["dataset"] == "BRK.B"
    assert studychain.read_chain(researchqueue.repo_root() / fa.SCRIPT)[0]["step"] == "probe"


def test_the_added_tag_takes_an_added_underlying_never_one_of_the_sets():
    args = {"store_tag": "added", "from": "2025-11-17", "to": "2026-11-16", "workers": 2}
    argv = researchqueue.build_argv("fetch-sessions", {**args, "dataset": "ABC"}, "python")
    assert argv[argv.index("--store-tag") + 1] == "added" and argv[-1] == "ABC"
    for bad in ("SPY", "abc", "A B"):
        try:
            researchqueue.build_argv("fetch-sessions", {**args, "dataset": bad}, "python")
        except researchqueue.Refused:
            continue
        raise AssertionError(f"{bad!r} was accepted")


def test_a_builder_run_waits_only_on_an_addition_and_then_takes_no_source():
    view = {"automations": [{"name": "mine", "symbol": "ABC", "suggestion": {
        "blocks": ["structure"], "window": ["2025-11-17", "2026-11-16"], "possible": False,
        "needs": [{"field": "chain", "expiry": "0dte", "cadence": "1m", "covered": False}]}}]}  # fmt: skip
    added = []

    def add(study, kind, args, **kw):
        added.append((args, kw))
        return {"id": 7}

    def queue(**req):
        return ar.queue(
            {}, {"name": "mine", **req}, builder=lambda _cfg: view, add=add, rows=lambda study=None: []
        )

    assert queue()["code"] == "invalid", "not possible, and waiting on nothing"
    assert queue(after="item91/study")["code"] == "invalid"
    out = queue(after="fullset/add-abc-mid-2")
    assert out["ok"] and out["after"] == "fullset/add-abc-mid-2" and out["source"] is None
    ((args, kw),) = added
    assert kw["after"] == "fullset/add-abc-mid-2" and "--source" not in args["args"]
