"""`run.py bootstrap`: the universe's evening jobs, once, with the supervisor's own commands."""

from __future__ import annotations

from test_jobspec import derive, suite_cfg

from cherrypick.orchestrator import bootstrap


def test_every_bootstrap_job_is_one_the_schedule_derives_in_dependency_order():
    """A renamed job would silently drop out of the installer's first run; it fails here instead."""
    jobs = derive(suite_cfg())[0]
    steps = bootstrap.plan(jobs, "python")
    assert [s.job for s in steps] == list(bootstrap.BOOTSTRAP_JOBS)
    assert all(s.skip is None for s in steps), [(s.job, s.skip) for s in steps if s.skip]
    by_id = {j.id: j for j in jobs}
    for s in steps:
        assert s.argv[0] == "python" and s.argv[1:] == tuple(by_id[s.job].argv)[1:]
    # The order the jobs read each other in is the order their own schedule keeps.
    times = [by_id[j].at_et for j in bootstrap.BOOTSTRAP_JOBS[1:]]
    assert times == sorted(times)


def test_a_job_the_config_has_off_is_skipped_and_said_so():
    cfg = suite_cfg(technicals={"enabled": False})
    steps = {s.job: s for s in bootstrap.plan(derive(cfg)[0], "python")}
    assert steps["technicals-securities"].skip and "technicals" in steps["technicals-securities"].skip
    assert steps["technicals-securities"].argv is None


def test_a_failed_step_is_reported_and_the_rest_still_run():
    steps = bootstrap.plan(derive(suite_cfg())[0], "python")
    ran = []

    def runner(s):
        ran.append(s.job)
        return (1, "boom") if s.job == "technicals-dollar-volume" else (0, "fine")

    out = bootstrap.run(steps, runner)
    assert ran == list(bootstrap.BOOTSTRAP_JOBS)
    assert out["ok"] is False
    assert [r["ok"] for r in out["steps"]] == [True, False, True, True, True]
    assert out["steps"][1]["tail"] == "boom"
