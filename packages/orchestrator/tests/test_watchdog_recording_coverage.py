"""A recorder series that stops mid-session is reported once the session is over.

The failure being guarded is 2026-10-13: from 14:53 the gex recorder kept no spot trail and no
chain quotes (it found no SPX spot) while its GEX regime series and heartbeat carried on, so every
liveness check read healthy and the last hour of quote history was lost without a word.

The module is driven by SUBPROCESS, so the tests patch `_run_module`, never an import.
"""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from cherrypick.orchestrator import watchdog
from cherrypick.orchestrator.watchdog import OK, WARN

ET = ZoneInfo("America/New_York")
EVENING = datetime(2026, 10, 13, 18, 0, tzinfo=ET)
CFG = {"services": [{"id": "gex-recorder", "enabled": True, "path": "../gex", "status_argv": ["run.py"]}]}

STOPPED = {
    "ok": True,
    "session": "2026-10-13",
    "complete": False,
    "failing": ["quotes:SPX", "spot:SPX"],
    "series": {
        "spot:SPX": {"ok": False, "gaps": [{"from": "14:53:36", "to": "16:00:00", "seconds": 3983.9}]},
        "quotes:SPX": {"ok": False, "gaps": [{"from": "14:53:36", "to": "16:00:00", "seconds": 3983.9}]},
        "gex_regime:SPX": {"ok": True, "gaps": []},
    },
}


class _Proc:
    def __init__(self, payload, code=0):
        self.stdout = json.dumps(payload) if payload is not None else ""
        self.stderr = ""
        self.returncode = code


@pytest.fixture
def gex(monkeypatch):
    calls = []

    def install(payload, code=0):
        def run(root, argv, timeout=25):
            calls.append(list(argv))
            if isinstance(payload, Exception):
                raise payload
            return _Proc(payload, code)

        monkeypatch.setattr(watchdog, "_run_module", run)
        return calls

    return install


@pytest.mark.parametrize(
    "now, session",
    [
        (datetime(2026, 10, 14, 16, 20, tzinfo=ET), "2026-10-14"),
        (datetime(2026, 10, 14, 16, 10, tzinfo=ET), "2026-10-13"),  # too soon after the close
        (datetime(2026, 10, 14, 9, 0, tzinfo=ET), "2026-10-13"),
        (datetime(2026, 10, 17, 12, 0, tzinfo=ET), "2026-10-16"),  # a Saturday
        (datetime(2026, 11, 27, 13, 20, tzinfo=ET), "2026-11-27"),  # an early close at 13:00
    ],
)
def test_the_session_judged_is_the_last_one_finished(now, session):
    assert watchdog._last_judgeable_session(now) == session


def test_a_stopped_series_is_reported_with_its_gap_and_one_member_each(gex):
    gex(STOPPED)
    [f] = watchdog._check_recording_coverage(CFG, EVENING)
    assert f.status == WARN and f.key == "recording.coverage"
    assert set(f.members) == {"2026-10-13:quotes:SPX", "2026-10-13:spot:SPX"}
    assert "14:53:36-16:00:00" in f.message and "gex_regime" not in f.message


def test_the_verdict_is_judged_once_per_session(gex):
    calls = gex(STOPPED)
    watchdog._check_recording_coverage(CFG, EVENING)
    watchdog._check_recording_coverage(CFG, EVENING.replace(hour=20))
    assert calls == [["run.py", "coverage", "--session", "2026-10-13"]], "cached after the first verdict"


def test_a_complete_session_reads_ok(gex):
    gex({"ok": True, "session": "2026-10-13", "complete": True, "failing": [], "series": {}})
    [f] = watchdog._check_recording_coverage(CFG, EVENING)
    assert f.status == OK and not f.members


def test_a_check_that_cannot_run_says_so_and_asks_again(gex):
    calls = gex(TimeoutError("slow disk"))
    [f] = watchdog._check_recording_coverage(CFG, EVENING)
    assert f.status == WARN and "unknown" in f.title.lower()
    watchdog._check_recording_coverage(CFG, EVENING)
    assert len(calls) == 2, "an unknown verdict is never cached"


def test_no_recorder_configured_means_no_check(gex):
    calls = gex(STOPPED)
    assert watchdog._check_recording_coverage({"services": []}, EVENING) == []
    assert calls == []
