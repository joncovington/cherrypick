"""Daily and monthly jobs that failed or never ran (item 5 of the 2026-10-08 audit). The supervisor
recorded both and told no one; the watchdog now says so, once per kind."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from cherrypick.orchestrator import watchdog as wd

NOW = datetime(2026, 10, 8, 23, 30, tzinfo=timezone.utc)
HOUR_AGO = (NOW - timedelta(hours=1)).isoformat()
TWO_DAYS_AGO = (NOW - timedelta(days=2)).isoformat()


@pytest.fixture
def managed_state(tmp_path, monkeypatch):
    monkeypatch.setattr(wd, "_STATE_FILE", tmp_path / "watchdog_state.json")


def warnings(jobs):
    return [f for f in wd._fixed_time_job_findings(jobs, NOW) if f.status != wd.OK]


def job(**kw):
    return {"kind": "daily", "enabled": True, "last_fire_day": "2026-10-08", **kw}


def test_a_failed_job_with_no_retry_left_is_reported_with_its_error():
    jobs = {
        "technicals-land": job(last_exit_code=1, last_exit_at=HOUR_AGO, last_error="x\nDoltError: no clone")
    }
    (f,) = warnings(jobs)
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
    assert warnings(jobs) == []
    # an empty list is an OK finding per kind, so the end of a warning is announced once
    assert {(f.key, f.status) for f in wd._fixed_time_job_findings(jobs, NOW)} == {
        ("jobs.failed", wd.OK),
        ("jobs.missed", wd.OK),
    }


def test_missed_jobs_are_one_finding_even_for_a_job_that_alerts_on_its_own_failure():
    jobs = {
        "review-final": job(missed=HOUR_AGO),
        "morning-factpack": job(missed=HOUR_AGO),
        "log-archive": {"kind": "monthly", "enabled": True, "last_fire_month": "2026-10", "missed": HOUR_AGO},
        "stale": job(missed=TWO_DAYS_AGO),
    }
    (f,) = warnings(jobs)
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


def _ticks(seq, start=NOW):
    """Run the notifier over a sequence of job tables, ten minutes apart; what was sent."""
    sent = []

    class Rec:
        def notify(self, level, key, title, message):
            sent.append((level, key, title))
            return None  # a double that returns nothing counts as delivered

    for i, jobs in enumerate(seq):
        wd._process_notifications(
            wd._fixed_time_job_findings(jobs, start), Rec(), 60, start + timedelta(minutes=10 * i)
        )
    return sent


def test_a_shrinking_list_is_not_reposted_and_its_end_is_announced_once(managed_state):
    """2026-10-09: after an outage, the missed list shrank 19 -> 17 -> 8 -> 7 through the day and each
    hour reposted it. Now: one post, silence while it shrinks (even past the hour), one all-clear."""
    three = {n: job(missed=HOUR_AGO) for n in ("a", "b", "c")}
    two = {n: job(missed=HOUR_AGO) for n in ("a", "b")}
    sent = _ticks([three, three, two, *([two] * 8), {}])
    assert [s for s in sent if s[1] == "jobs.missed"] == [
        ("WARN", "jobs.missed", "3 scheduled job(s) did not run"),
        ("INFO", "jobs.missed", "Recovered: Missed scheduled jobs"),
    ]


def test_a_new_incident_is_posted_even_when_the_job_is_already_listed(managed_state):
    first = {"pull": job(last_exit_code=1, last_exit_at=HOUR_AGO)}
    again = {"pull": job(last_exit_code=1, last_exit_at=(NOW - timedelta(minutes=5)).isoformat())}
    other = {**first, "post": job(last_exit_code=1, last_exit_at=HOUR_AGO)}
    sent = [s for s in _ticks([first, first, again, other]) if s[1] == "jobs.failed"]
    assert [s[2] for s in sent] == [
        "1 scheduled job(s) failed",
        "1 scheduled job(s) failed",  # the same job, a new failure
        "2 scheduled job(s) failed",  # a new job joined
    ]


def test_a_list_announced_before_members_existed_is_not_reposted_on_upgrade(managed_state):
    wd._save_state(
        {"jobs.missed": {"status": "WARN", "first_seen": NOW.isoformat(), "last_notified": NOW.isoformat()}}
    )
    three = {n: job(missed=HOUR_AGO) for n in ("a", "b", "c")}
    four = {**three, "d": job(missed=HOUR_AGO)}
    sent = [s[2] for s in _ticks([three, three, four]) if s[1] == "jobs.missed"]
    assert sent == ["4 scheduled job(s) did not run"]  # quiet on upgrade, but a newcomer still posts
