"""An issued advice artifact that no loop applied is reported while the session can still be used.

This check replaced a scheduled AI checkpoint on 2026-08-26. The midday slot existed partly so
`advice_enacted` would be visible at 10am rather than in the evening verdict; across its entire
history it produced zero proposals and never once caught an enactment failure, while the question
it was meant to answer is deterministic and already had a verb. So the verb moved here.

The failure being guarded is the 2026-08-25 incident: five artifacts issued with zero rejections,
three applied and two not, and the two were the modules whose experiments had their most
informative session available.

Every assertion below was confirmed to fail against a check that reports nothing.
"""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from cherrypick.orchestrator import watchdog

ET = ZoneInfo("America/New_York")
MIDDAY = datetime(2026, 8, 25, 11, 0, tzinfo=ET)
CFG = {"advisor": {"enabled": True}}


class _Proc:
    def __init__(self, payload):
        self.stdout = json.dumps(payload) if payload is not None else ""
        self.stderr = ""
        self.returncode = 0


def _payload(**statuses):
    return {
        "ok": True,
        "session": "2026-08-25",
        "modules": {
            name: {"module": name, "status": status, "detail": f"{name} detail"}
            for name, status in statuses.items()
        },
    }


@pytest.fixture
def advisor(monkeypatch):
    """Patch the SUBPROCESS, not an import. The orchestrator drives the advisor by subprocess by
    rule, and a test that monkeypatches an import would be asserting against an arrangement this
    package is not allowed to have."""

    def install(payload):
        monkeypatch.setattr(watchdog, "_run_module", lambda *a, **k: _Proc(payload))

    return install


def test_a_dropped_artifact_is_reported(advisor):
    advisor(_payload(meic="not_enacted", earnings="not_enacted", flies="enacted"))
    [finding] = watchdog._check_advice_enactment(CFG, MIDDAY, True)
    assert finding.status == watchdog.WARN
    assert "2 module(s)" in finding.title
    assert "meic" in finding.message and "earnings" in finding.message


def test_a_clean_session_is_silent(advisor):
    """A watchdog that speaks on healthy days is a watchdog people mute."""
    advisor(_payload(meic="enacted", flies="enacted"))
    assert watchdog._check_advice_enactment(CFG, MIDDAY, True) == []


def test_no_artifact_is_not_a_failure(advisor):
    """The ordinary state of a module with no active experiment. Reporting it would warn about
    every module the advisor is not currently running, every day."""
    advisor(_payload(pmcc="no_artifact", bwb="no_artifact", curve="no_artifact"))
    assert watchdog._check_advice_enactment(CFG, MIDDAY, True) == []


def test_only_the_dropped_modules_are_named(advisor):
    advisor(_payload(meic="not_enacted", flies="enacted", bwb="no_artifact"))
    [finding] = watchdog._check_advice_enactment(CFG, MIDDAY, True)
    assert "meic" in finding.message
    assert "flies" not in finding.message and "bwb" not in finding.message


@pytest.mark.parametrize(
    "when,why",
    [
        (datetime(2026, 8, 25, 9, 0, tzinfo=ET), "before the loops have recorded a decision"),
        (datetime(2026, 8, 25, 17, 30, tzinfo=ET), "after the deep slot scores it anyway"),
    ],
)
def test_it_is_windowed(advisor, when, why):
    """A warning that arrives with the verdict is not an early warning, and one that arrives before
    the loops have started reports a decision nobody has had the chance to make."""
    advisor(_payload(meic="not_enacted"))
    assert watchdog._check_advice_enactment(CFG, when, True) == [], why


def test_it_is_silent_on_a_non_trading_day(advisor):
    advisor(_payload(meic="not_enacted"))
    assert watchdog._check_advice_enactment(CFG, MIDDAY, False) == []


def test_it_is_silent_when_the_advisor_is_disabled(advisor):
    advisor(_payload(meic="not_enacted"))
    assert watchdog._check_advice_enactment({"advisor": {"enabled": False}}, MIDDAY, True) == []


def test_an_unreadable_reply_reports_nothing_rather_than_guessing(advisor):
    """`ok: false` means the advisor could not answer. That is not evidence of a dropped artifact,
    and inventing one would train the reader to ignore this key."""
    advisor({"ok": False, "error": "no such session"})
    assert watchdog._check_advice_enactment(CFG, MIDDAY, True) == []
    advisor(None)
    assert watchdog._check_advice_enactment(CFG, MIDDAY, True) == []


def test_a_subprocess_that_throws_is_reported_not_swallowed(monkeypatch):
    """The check failing is itself worth knowing — silently returning [] here would look exactly
    like a clean session, which is the same defect the console's readOnlyDb fallback had."""

    def boom(*a, **k):
        raise OSError("advisor not installed")

    monkeypatch.setattr(watchdog, "_run_module", boom)
    [finding] = watchdog._check_advice_enactment(CFG, MIDDAY, True)
    assert finding.status == watchdog.WARN
    assert "unknown" in finding.title.lower()
