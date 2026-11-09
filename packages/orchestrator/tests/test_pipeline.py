"""The morning and evening data runs as ordered pipelines: the job table (membership, order, what
stays a job of its own), and the runner (order, stop on a failure, an independent step that does not
stop the run, a skipped gate, the recorded results, resume, the watchdog's wording)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from test_jobspec import derive, suite_cfg

from cherrypick.orchestrator import jobspec, pipeline, winupdate
from cherrypick.orchestrator import watchdog as wd
from cherrypick.orchestrator.jobspec import JobSpec, PipelineStep

ET = ZoneInfo("America/New_York")
MONDAY = datetime(2026, 8, 10, 6, 0, tzinfo=ET)  # a trading day
SATURDAY = datetime(2026, 8, 8, 6, 0, tzinfo=ET)


def _cfg():
    cfg = suite_cfg()
    cfg["market_report"] = {"universe": True, "screener_greeks": True}
    cfg["manage"] = {"enabled": True}
    return cfg


# ------------------------------------------------------------------------------------ the job table
def test_each_pipeline_derives_once_in_the_declared_order_and_its_members_are_not_jobs():
    flat = {j.id: j for j in derive(_cfg(), pipelines=False)[0]}
    jobs, errors = derive(_cfg(), pipelines=True)
    assert errors == {}
    by_id = {j.id: j for j in jobs}
    for name, members in jobspec.PIPELINES.items():
        job = by_id[name]
        assert [s.id for s in job.steps] == [m[0] for m in members if m[0] in flat]
        assert job.argv[-2:] == ("pipeline", name) and job.kind == jobspec.KIND_DAILY
        assert "network" in job.tags and not job.trading_days_only
        for s in job.steps:
            # Each step is the command the job had: same argv, same gate, same switch.
            assert s.argv == flat[s.id].argv and s.trading_days_only == flat[s.id].trading_days_only
            assert s.enabled == flat[s.id].enabled
            assert s.id not in by_id or s.id in ("market-files", "technicals-earnings-calendar")
    # Every declared member is a real job: a renamed or mistyped id would silently shrink a pipeline.
    declared = {m[0] for members in jobspec.PIPELINES.values() for m in members}
    assert declared <= set(flat)


def test_a_pipeline_starts_with_its_first_step_and_catches_up_for_its_longest():
    by_id = {j.id: j for j in derive(_cfg(), pipelines=True)[0]}
    flat = {j.id: j for j in derive(_cfg(), pipelines=False)[0]}
    for name in jobspec.PIPELINES:
        job = by_id[name]
        live = [s for s in job.steps if s.enabled]
        assert job.at_et == live[0].at_et
        assert job.catchup_minutes == max(s.catchup_minutes for s in live)
        assert job.enabled
    assert by_id["morning-data"].at_et == flat["earnings-dolt-pull"].at_et == "05:30"
    assert by_id["evening-data"].at_et == flat["technicals-dollar-volume"].at_et


def test_the_steps_that_stop_the_run_are_the_ones_a_later_step_reads():
    by_id = {j.id: j for j in derive(_cfg(), pipelines=True)[0]}
    stops = {name: [s.id for s in by_id[name].steps if not s.independent] for name in jobspec.PIPELINES}
    assert stops["morning-data"] == [
        "technicals-land",
        "technicals-history",
        "technicals-fundamentals",
        "fundamentals-land",
    ]
    assert stops["evening-data"] == [
        "technicals-securities",
        "technicals-iv-rank",
        "technicals-earnings-calendar",
    ]


def test_a_job_that_runs_twice_a_day_gives_one_time_to_the_pipeline_and_keeps_the_other():
    flat = {j.id: j for j in derive(_cfg(), pipelines=False)[0]}
    by_id = {j.id: j for j in derive(_cfg(), pipelines=True)[0]}
    evening = {s.id: s for s in by_id["evening-data"].steps}
    files, cal = flat["market-files"], flat["technicals-earnings-calendar"]
    # market-files: the evening fetch is the step, the morning retry stays a job.
    assert evening["market-files"].at_et == files.times_et[-1]
    assert by_id["market-files"].at_et == files.times_et[0] and not by_id["market-files"].times_et
    assert by_id["market-files"].catchup_minutes == files.slot_catchup_minutes[0]
    # The calendar: the first pass is the step, the late pass stays a job.
    assert evening["technicals-earnings-calendar"].at_et == cal.times_et[0]
    assert by_id["technicals-earnings-calendar"].at_et == cal.times_et[-1]
    assert by_id["technicals-earnings-calendar"].catchup_minutes == cal.slot_catchup_minutes[-1]


def test_a_member_that_is_off_stays_a_step_with_its_reason_and_all_off_disables_the_pipeline():
    cfg = _cfg()
    cfg["capabilities"]["dolt"] = False
    by_id = {j.id: j for j in derive(cfg, pipelines=True)[0]}
    land = next(s for s in by_id["morning-data"].steps if s.id == "technicals-land")
    assert not land.enabled and "dolt" in land.enabled_reason
    # With Dolt off every morning step is off, so the pipeline is listed off with the reason.
    assert not by_id["morning-data"].enabled and "every step is off" in by_id["morning-data"].enabled_reason
    # One step off leaves the pipeline on and the step skipped at run time.
    only = jobspec.fold_pipelines(
        [
            JobSpec(
                id="technicals-land",
                argv=("x",),
                kind="daily",
                at_et="06:15",
                enabled=False,
                enabled_reason="no",
            ),
            JobSpec(id="technicals-report", argv=("y",), kind="daily", at_et="06:30"),
        ],
        "pw",
        "run.py",
    )
    assert [j.id for j in only] == ["morning-data"] and only[0].enabled and only[0].at_et == "06:30"
    assert [s.enabled for s in only[0].steps] == [False, True]


def test_the_old_job_ids_are_gone_so_the_supervisor_prunes_their_rows():
    ids = {j.id for j in derive(_cfg(), pipelines=True)[0]}
    gone = {m[0] for members in jobspec.PIPELINES.values() for m in members if m[2] is None}
    assert not gone & ids


def test_the_winupdate_span_covers_the_steps_not_only_the_start():
    jobs = derive(_cfg(), pipelines=True)[0]
    times = winupdate.times_from_specs(jobs)
    assert "20:15" in times  # the last evening step
    rows = {
        "evening-data": {"schedule": "daily 18:35 ET, 12 steps in order", "step_times": ["18:35", "20:15"]}
    }
    assert sorted(winupdate.job_times(rows)) == ["18:35", "18:35", "20:15"]


# ------------------------------------------------------------------------------------ the runner
def _spec(*steps, name="pipe"):
    return JobSpec(id=name, argv=("py", "run.py", "pipeline", name), kind="daily", at_et="06:00", steps=steps)


def _step(step_id, at="06:00", **kw):
    return PipelineStep(id=step_id, argv=("py", f"{step_id}.py"), at_et=at, **kw)


class Fake:
    """A spawn that records the order and answers each step's exit code (default 0)."""

    def __init__(self, codes=None, tails=None):
        self.codes, self.tails, self.ran = codes or {}, tails or {}, []

    def __call__(self, step, log):
        self.ran.append(step.id)
        if step.id in self.tails:
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(self.tails[step.id], encoding="utf-8")
        return self.codes.get(step.id, 0)


def _run(spec, fake, now=MONDAY, **kw):
    clock = {"now": now}
    slept = kw.pop("slept", [])

    def sleep(seconds):
        slept.append(seconds)
        clock["now"] += timedelta(seconds=seconds)

    return pipeline.run(
        spec.id, {"timezone": "America/New_York"}, spec=spec, now_fn=lambda: clock["now"], sleep=sleep,
        spawn=fake, holidays=set(), **kw,
    )  # fmt: skip


def test_the_steps_run_in_order_and_each_result_is_recorded():
    spec = _spec(_step("a"), _step("b"), _step("c"))
    fake = Fake()
    doc = _run(spec, fake)
    assert fake.ran == ["a", "b", "c"] and doc["result"] == "ok" and doc["failed_step"] is None
    assert [(s["id"], s["status"], s["exit_code"]) for s in doc["steps"]] == [
        ("a", "ok", 0),
        ("b", "ok", 0),
        ("c", "ok", 0),
    ]
    assert all(isinstance(s["seconds"], float) for s in doc["steps"])
    assert pipeline.load_state("pipe")["result"] == "ok"  # the same record, on disk
    assert pipeline.summary("pipe")["steps"][0] == {
        "id": "a",
        "status": "ok",
        "exit_code": 0,
        "seconds": doc["steps"][0]["seconds"],
    }


def test_a_failed_step_stops_the_run_and_the_rest_are_not_run():
    spec = _spec(_step("a"), _step("b"), _step("c"))
    fake = Fake(codes={"b": 3}, tails={"b": "Traceback\nValueError: no file for 2026-08-10\n"})
    doc = _run(spec, fake)
    assert fake.ran == ["a", "b"]  # c never starts
    assert [s["status"] for s in doc["steps"]] == ["ok", "failed", "not_run"]
    assert doc["result"] == "failed" and doc["failed_step"] == "b"
    failed = doc["steps"][1]
    assert failed["exit_code"] == 3 and "ValueError: no file" in failed["stderr_tail"]
    assert "stopped after b failed" in doc["steps"][2]["reason"]


def test_an_independent_failure_is_recorded_and_the_run_carries_on():
    spec = _spec(_step("a", independent=True), _step("b"))
    fake = Fake(codes={"a": 1})
    doc = _run(spec, fake)
    assert fake.ran == ["a", "b"]
    assert [s["status"] for s in doc["steps"]] == ["failed", "ok"]
    assert doc["result"] == "failed" and doc["failed_step"] == "a"  # still a failed run: the exit says so


def test_a_step_that_is_off_or_gated_out_today_is_skipped_not_failed():
    spec = _spec(
        _step("off", enabled=False, enabled_reason="disabled in config (x)"),
        _step("weekday", trading_days_only=True),
        _step("every"),
    )
    fake = Fake()
    doc = _run(spec, fake, now=SATURDAY)
    assert fake.ran == ["every"]
    assert [s["status"] for s in doc["steps"]] == ["skipped", "skipped", "ok"]
    assert (
        doc["steps"][0]["reason"] == "disabled in config (x)"
        and doc["steps"][1]["reason"] == "not a trading day"
    )
    assert doc["result"] == "ok"
    fake = Fake()
    assert _run(spec, fake, now=MONDAY)["steps"][1]["status"] == "ok" and "weekday" in fake.ran


def test_a_step_waits_for_its_own_time_but_not_when_it_is_already_past():
    spec = _spec(_step("a", at="06:00"), _step("b", at="06:30"), _step("c", at="06:10"))
    slept = []
    _run(spec, Fake(), slept=slept)
    assert slept == [1800.0]  # b waits half an hour; c's time has passed by then
    slept = []
    late = _spec(_step("a", at="23:50"), _step("b", at="23:55"))
    _run(late, Fake(), now=datetime(2026, 8, 10, 23, 40, tzinfo=ET), slept=slept)
    assert slept == [600.0, 300.0]


def test_a_retry_resumes_at_the_failed_step_and_fresh_starts_over():
    spec = _spec(_step("a"), _step("b"), _step("c"))
    _run(spec, Fake(codes={"b": 1}))
    fake = Fake()
    doc = _run(spec, fake)
    assert fake.ran == ["b", "c"]  # a is ok from the earlier run
    assert doc["result"] == "ok" and doc["resumed"] is True
    assert doc["steps"][0]["status"] == "ok" and "earlier run" in doc["steps"][0]["reason"]
    fake = Fake()
    _run(spec, fake)  # the last run finished ok: a new run is a whole one
    assert fake.ran == ["a", "b", "c"]
    _run(spec, Fake(codes={"b": 1}))
    fake = Fake()
    _run(spec, fake, fresh=True)
    assert fake.ran == ["a", "b", "c"]
    # Yesterday's failure does not shape today's run.
    _run(spec, Fake(codes={"a": 1}))
    fake = Fake()
    _run(spec, fake, now=MONDAY + timedelta(days=1))
    assert fake.ran == ["a", "b", "c"]


def test_the_stderr_names_the_pipeline_and_the_step_and_the_last_line_is_the_one_that_stopped_it():
    spec = _spec(_step("soft", independent=True), _step("hard"), _step("after"))
    doc = _run(spec, Fake(codes={"soft": 1, "hard": 2}, tails={"hard": "boom: secret ****1234"}))
    lines = pipeline.report_lines(doc)
    assert lines[0] == "pipeline pipe: not run: after"
    assert lines[-1].startswith("pipeline pipe: step hard failed (exit 2): boom")
    assert any("step soft failed (exit 1)" in ln for ln in lines)
    assert pipeline.failure_text("pipe").startswith("step hard (exit 2: boom")


def test_an_unknown_pipeline_is_a_failed_run_not_an_exception():
    doc = pipeline.run(
        "nope", {"timezone": "America/New_York"}, spec=None, now_fn=lambda: MONDAY, holidays=set()
    )
    assert doc["result"] == "failed" and "no pipeline" in doc["error"]


def test_a_step_that_cannot_start_fails_with_127(tmp_path):
    step = PipelineStep(id="x", argv=("definitely-not-a-program-xyz",), at_et="06:00")
    assert pipeline.spawn_step(step, tmp_path / "x.log") == 127
    assert "could not start" in (tmp_path / "x.log").read_text()


# ------------------------------------------------------------------------------------ the watchdog
def test_the_failed_job_finding_names_the_pipeline_and_the_step():
    now = datetime(2026, 8, 10, 7, 0, tzinfo=ET)
    row = {
        "kind": "daily",
        "enabled": True,
        "last_exit_code": 1,
        "last_exit_at": (now - timedelta(minutes=5)).astimezone(ZoneInfo("UTC")).isoformat(),
        "last_fire_day": "2026-08-10",
        "last_error": "pipeline morning-data: step technicals-land failed (exit 2): no clone",
        "steps": {
            "result": "failed",
            "failed_step": "technicals-land",
            "steps": [
                {"id": "earnings-dolt-pull", "status": "ok", "exit_code": 0},
                {"id": "technicals-land", "status": "failed", "exit_code": 2},
            ],
        },
    }
    findings = wd._fixed_time_job_findings({"morning-data": row}, now)
    failed = next(f for f in findings if f.key == "jobs.failed")
    assert "morning-data step technicals-land (exit 2" in failed.message
    plain = dict(row)
    plain.pop("steps")
    other = next(f for f in wd._fixed_time_job_findings({"some-job": plain}, now) if f.key == "jobs.failed")
    assert "some-job (exit 1" in other.message


def test_the_supervisor_keeps_the_per_step_results_in_the_registry_row(monkeypatch):
    from cherrypick.orchestrator import supervisor

    spec = _spec(_step("a"))
    monkeypatch.setattr(
        pipeline, "summary", lambda name: {"result": "failed", "failed_step": "a", "steps": []}
    )
    sup = supervisor.Supervisor({"timezone": "America/New_York", "modules": {}})
    st: dict = {}
    sup._record_exit(spec, st, 1)
    assert st["steps"]["failed_step"] == "a"
    plain: dict = {}
    sup._record_exit(JobSpec(id="j", argv=("x",), kind="daily", at_et="06:00"), plain, 1)
    assert "steps" not in plain


def test_pipeline_ids_retry_inside_their_window():
    for name in jobspec.PIPELINES:
        assert jobspec.RETRY_ON_FAILURE[name] == 2


def test_a_pipeline_must_have_distinct_steps():
    with pytest.raises(ValueError):
        _spec(_step("a"), _step("a"))
