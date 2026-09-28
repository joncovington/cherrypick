"""The earnings-calendar read after a Dolt pull, and the watchdog warning built on it.

On 2026-09-27 a pull succeeded but the calendar read straight after it failed once; the null it
wrote sat in the state file until the next day's pull, so the watchdog warned every hour about a
calendar that answered fine minutes later -- and could not say why, because the error was
swallowed. These pin the fix: the read retries, a failure is recorded with its reason, and the
warning quotes it and names the way to clear it.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import watchdog

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "refresh_dolt_data.py"


def _module():
    spec = importlib.util.spec_from_file_location("refresh_dolt_data", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rdd = _module()


def _flaky(*answers):
    calls = iter(answers)

    def read():
        answer = next(calls)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return read


def test_a_read_that_fails_once_after_a_pull_is_retried():
    read = _flaky(ConnectionError("server settling"), "2026-11-06")
    assert rdd.calendar_max_date(read, attempts=3, pause=lambda: None) == ("2026-11-06", None)


def test_a_read_that_keeps_failing_records_why():
    read = _flaky(*(ConnectionError("refused"),) * 3)
    value, error = rdd.calendar_max_date(read, attempts=3, pause=lambda: None)
    assert value is None and error == "ConnectionError: refused"


def test_an_empty_calendar_is_an_error_not_a_date():
    value, error = rdd.calendar_max_date(_flaky(None, None), attempts=2, pause=lambda: None)
    assert value is None and "empty" in error


def _state(**fields):
    path = cfgmod.state_file("dolt_data.json")  # where the watchdog reads it
    path.write_text(json.dumps({"refreshed_at": "2026-09-27T16:13:30+00:00", **fields}), encoding="utf-8")


def _calendar_finding():
    cfg = {"modules": {"earnings": {"enabled": True}}}
    found = [f for f in watchdog._check_earnings_calendar(cfg) if f.key == "earnings.calendar"]
    return found[0] if found else None


def test_the_warning_quotes_the_recorded_error_and_names_the_recheck():
    _state(earnings_calendar_max_date=None, earnings_calendar_error="ConnectionError: refused")
    finding = _calendar_finding()
    assert finding is not None and finding.status == watchdog.WARN
    assert "ConnectionError: refused" in finding.message and "--recheck" in finding.message
