"""#1090: each study's stage dates and the queue's split of its time, from invented history."""

from __future__ import annotations

from cherrypick.orchestrator import studytimes as st

ROW = {"id": 90, "study": "item90", "status": "failed", "scripts": ["packages/backtest/scripts/x_item90.py"]}

# The register's commits, oldest first: declared on day 1, still declared on day 3, failed on day 6.
HISTORY = [
    ("2026-11-01T10:00:00-06:00", {"hypotheses": [{**ROW, "status": "declared"}]}),
    ("2026-11-03T10:00:00-06:00", {"hypotheses": [{**ROW, "status": "declared"}]}),
    ("2026-11-06T10:00:00-06:00", {"hypotheses": [ROW]}),
]

TASKS = [
    # Queue times are ET without an offset, as the queue writes them.
    {"id": 1, "study": "item90", "step": "fetch-chains", "kind": "fetch-chains", "status": "done",
     "created_at": "2026-11-02T12:00:00", "started_at": "2026-11-02T14:00:00",
     "finished_at": "2026-11-03T14:00:00"},
    {"id": 2, "study": "item90", "step": "study", "kind": "script", "status": "failed",
     "created_at": "2026-11-02T12:00:00", "started_at": "2026-11-03T14:00:00",
     "finished_at": "2026-11-03T15:00:00"},
    # Queued again 9 hours after the failure.
    {"id": 3, "study": "item90", "step": "study", "kind": "script", "status": "done",
     "created_at": "2026-11-04T00:00:00", "started_at": "2026-11-04T01:00:00",
     "finished_at": "2026-11-04T03:00:00"},
    {"id": 4, "study": "item99", "step": "study", "kind": "script", "status": "done",
     "created_at": "2026-11-01T00:00:00", "started_at": "2026-11-01T00:00:00",
     "finished_at": "2026-11-01T01:00:00"},
]  # fmt: skip


def test_the_register_history_dates_the_declaration_and_the_recorded_verdict():
    d = st.register_dates(HISTORY)[90]
    assert d["declared"] == st.when("2026-11-01T10:00:00-06:00")
    assert d["recorded"] == st.when("2026-11-06T10:00:00-06:00")


def test_a_row_backfilled_with_its_verdict_has_no_recorded_date():
    history = [("2026-10-24T22:00:00-06:00", {"hypotheses": [{"id": 20, "status": "failed"}]}),
               ("2026-10-25T22:00:00-06:00", {"hypotheses": [{"id": 20, "status": "failed"}]})]  # fmt: skip
    assert "recorded" not in st.register_dates(history)[20]


def test_the_plan_heading_dates_a_declaration_made_before_the_register_began():
    reg = {"hypotheses": [ROW]}
    out = st.report(reg, HISTORY, {}, TASKS, plan_dates={90: "2026-10-20T10:00:00-06:00"})
    assert out["items"][0]["stages"]["declared"] == st.when("2026-10-20T10:00:00-06:00").isoformat()


def test_a_queue_time_is_eastern_and_a_git_time_keeps_its_offset():
    assert st.when("2026-11-02T12:00:00") == st.when("2026-11-02T17:00:00+00:00")
    assert st.when("2026-11-01T10:00:00-06:00") == st.when("2026-11-01T16:00:00+00:00")


def test_the_stages_and_spans_of_one_study():
    out = st.study_stages(ROW, st.register_dates(HISTORY)[90], "2026-11-02T08:00:00-06:00", TASKS)
    s = out["spans_h"]
    assert s["declare_to_script"] == 22.0  # 11-01 16:00Z to 11-02 14:00Z
    assert s["script_to_queue"] == 3.0  # to the first row created, 11-02 17:00Z
    assert s["queue_to_fetched"] == 26.0  # to the fetch's finish, 11-03 19:00Z
    assert s["fetched_to_studied"] == 13.0  # to the last study row done, 11-04 08:00Z
    assert s["studied_to_recorded"] == 56.0  # to the verdict commit, 11-06 16:00Z
    assert s["declare_to_recorded"] == 120.0
    # Waiting: 2 + 26 + 1 hours; running: 24 + 1 + 2; failed until queued again: 9.
    assert out["queue"] == {"waiting_h": 29.0, "running_h": 27.0, "failed_until_requeued_h": 9.0}


def test_a_study_on_stores_already_held_has_its_fetch_stage_at_its_queueing():
    tasks = [t for t in TASKS if t["kind"] == "script"]
    out = st.study_stages(ROW, st.register_dates(HISTORY)[90], None, tasks)
    assert out["stages"]["fetched"] == out["stages"]["queued"]
    assert out["spans_h"]["declare_to_script"] is None


def test_the_report_keeps_rows_from_item_18_and_takes_medians_over_rows_that_have_them():
    reg = {"hypotheses": [ROW, {"id": 5, "study": "item5", "legacy": True, "status": "failed"},
           {"id": 91, "study": "item91", "status": "declared"}]}  # fmt: skip
    out = st.report(reg, HISTORY, {ROW["scripts"][0]: "2026-11-02T08:00:00-06:00"}, TASKS)
    assert [i["id"] for i in out["items"]] == [90, 91]
    assert out["studied_not_recorded"] == []
    assert out["medians"]["declare_to_recorded"] == {"median_h": 120.0, "n": 1}
    assert out["medians"]["failed_until_requeued_h"] == {"median_h": 9.0, "n": 1}
    # Studied, still declared: named, since the median over recorded rows cannot see it.
    still = {**ROW, "status": "declared"}
    out = st.report({"hypotheses": [still]}, HISTORY[:2], {}, TASKS)
    assert out["studied_not_recorded"] == [90]
