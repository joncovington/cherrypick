"""Daily and monthly jobs that failed or never ran (item 5 of the 2026-10-08 audit). The supervisor
recorded both and told no one; the watchdog now says so, once per kind."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cherrypick.orchestrator import watchdog as wd

NOW = datetime(2026, 10, 8, 23, 30, tzinfo=timezone.utc)
HOUR_AGO = (NOW - timedelta(hours=1)).isoformat()
TWO_DAYS_AGO = (NOW - timedelta(days=2)).isoformat()


def job(**kw):
    return {"kind": "daily", "enabled": True, "last_fire_day": "2026-10-08", **kw}


def test_a_failed_job_with_no_retry_left_is_reported_with_its_error():
    jobs = {
        "technicals-land": job(last_exit_code=1, last_exit_at=HOUR_AGO, last_error="x\nDoltError: no clone")
    }
    (f,) = wd._fixed_time_job_findings(jobs, NOW)
    assert f.key == "jobs.failed" and f.status == wd.WARN
    assert "technicals-land (exit 1: DoltError: no clone)" in f.message


def test_what_is_not_a_failure_worth_a_message():
    jobs = {
        "retrying": job(last_exit_code=1, last_exit_at=HOUR_AGO, last_fire_day=None),  # retry pending
        "suite-backup": job(last_exit_code=1, last_exit_at=HOUR_AGO),  # alerts on its own
        "stopped": job(last_exit_code=-15, last_exit_at=HOUR_AGO, last_exit_requested=True),
        "old": job(last_exit_code=1, last_exit_at=TWO_DAYS_AGO),
        "running": job(last_exit_code=1, last_exit_at=HOUR_AGO, running_pid=123),
        "ticker": {"kind": "interval", "enabled": True, "last_exit_code": 1, "last_exit_at": HOUR_AGO},
        "off": job(enabled=False, last_exit_code=1, last_exit_at=HOUR_AGO),
        "fine": job(last_exit_code=0, last_exit_at=HOUR_AGO),
    }
    assert wd._fixed_time_job_findings(jobs, NOW) == []


def test_missed_jobs_are_one_finding_even_for_a_job_that_alerts_on_its_own_failure():
    jobs = {
        "review-final": job(missed=HOUR_AGO),
        "morning-factpack": job(missed=HOUR_AGO),
        "log-archive": {"kind": "monthly", "enabled": True, "last_fire_month": "2026-10", "missed": HOUR_AGO},
        "stale": job(missed=TWO_DAYS_AGO),
    }
    (f,) = wd._fixed_time_job_findings(jobs, NOW)
    assert f.key == "jobs.missed" and f.title.startswith("3 ")
    assert "log-archive, morning-factpack, review-final" in f.message


def test_every_watchdog_check_is_called_somewhere():
    # A check that is defined and tested but never wired into the pass is coverage that cannot fire
    # (removing `_check_fixed_time_jobs(now)` from run() passed every other test, 2026-10-08).
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(wd))
    defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("_check_")}
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert defined - called == set()
