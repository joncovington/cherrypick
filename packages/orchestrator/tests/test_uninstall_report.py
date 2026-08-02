"""`cherrypick uninstall`'s human-readable report -- was a raw JSON dump, now matches doctor's
`[ OK ]`/`[FAIL]` scan format so a walk-away user can tell at a glance whether every task actually
went away, per the deferred follow-up raised 2026-08-02."""

from __future__ import annotations

import pytest

import cherrypick.cli as cli

pytestmark = pytest.mark.unit


def test_all_ok_reports_all_removed():
    results = {
        "meic.paper_task": {"ok": True, "detail": "removed"},
        "watchdog_task": {"ok": True, "detail": "not registered: cherrypick-watchdog"},
    }
    report, worst = cli._format_uninstall_report(results)
    assert worst == 0
    assert "Result: ALL REMOVED" in report
    assert "[ OK ] meic.paper_task" in report
    assert "[ OK ] watchdog_task" in report


def test_a_failure_is_marked_and_reported():
    results = {
        "meic.paper_task": {"ok": True, "detail": "removed"},
        "flies.entry_task_name": {"ok": False, "detail": "access denied"},
    }
    report, worst = cli._format_uninstall_report(results)
    assert worst == 1
    assert "Result: FAILURES -- action needed" in report
    assert "[FAIL] flies.entry_task_name" in report
    assert "access denied" in report


def test_names_what_it_deliberately_leaves_running():
    report, _ = cli._format_uninstall_report({})
    assert "streamer" in report
    assert "dashboard" in report
    assert "Dolt" in report
