"""Restart a streamer that has gone SILENT, not just one that has died.

Observed live on 2026-07-20: MEIC's streamer reconnected and then received no events for 8 minutes
while still reporting `running: true` and its own `stale_warning: false` (that flag only trips at
600s). The watchdog's liveness check asks whether the process is up, so nothing restarted it — MEIC
degraded to REST and the flies module refused every iteration on stale quotes. A socket that
connected and went quiet is invisible to a PID check.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from cherrypick.orchestrator import watchdog

pytestmark = pytest.mark.unit


def _mcfg(**streamer):
    return {
        "path": ".",
        "paper": {"kind": "self_healing", "task_name": "t"},
        "streamer": {"enabled": True, "auto_restart": True,
                     "status_argv": ["src/streamer.py", "--status"],
                     "start_argv": ["src/streamer.py"], **streamer},
    }


def _status(**over):
    base = {"running": True, "oldest_event_age_s": 5.0,
            "connected_since": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()}
    base.update(over)
    return base


@pytest.fixture()
def spy(monkeypatch):
    """Capture stop/start calls instead of touching a real process."""
    calls = {"stop": 0, "start": 0}

    def record(key):
        def _fn(*_a, **_k):
            calls[key] += 1
            return True
        return _fn

    monkeypatch.setattr(watchdog, "_stop_streamer", record("stop"))
    monkeypatch.setattr(watchdog, "_start_streamer", record("start"))
    return calls


def _run(monkeypatch, status, mcfg=None):
    class R:
        returncode = 0
        stdout = json.dumps(status)
    monkeypatch.setattr(watchdog, "_run_module", lambda *a, **k: R())
    monkeypatch.setattr(watchdog.tasks, "exists", lambda _n: True)
    return watchdog._check_meic("meic", mcfg or _mcfg(), in_session=True)


# --------------------------------------------------------------------------- the stall
def test_a_silent_but_running_streamer_is_restarted(monkeypatch, spy):
    """The exact failure observed: running=true, connected, no events for 8 minutes."""
    findings = _run(monkeypatch, _status(oldest_event_age_s=480.0))
    f = next(x for x in findings if x.key == "meic.streamer")
    assert f.status == watchdog.WARN
    assert "stalled" in f.title.lower() and "restarted" in f.title.lower()
    assert "480s" in f.message
    assert spy["start"] == 1


def test_the_stalled_streamer_is_stopped_before_relaunch(monkeypatch, spy):
    """The daemon is single-instance guarded, so relaunching without stopping is a no-op — the new
    process sees the old PID alive and refuses. The restart would silently do nothing."""
    _run(monkeypatch, _status(oldest_event_age_s=480.0))
    assert spy["stop"] == 1 and spy["start"] == 1


def test_a_healthy_streamer_is_left_alone(monkeypatch, spy):
    findings = _run(monkeypatch, _status(oldest_event_age_s=3.0))
    f = next(x for x in findings if x.key == "meic.streamer")
    assert f.status == watchdog.OK
    assert spy["start"] == 0


def test_a_just_restarted_streamer_is_not_restarted_again(monkeypatch, spy):
    """Resubscribing ~2,000 symbols takes a few seconds. Without a settling guard the next tick sees
    stale data and restarts again — forever."""
    fresh = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    findings = _run(monkeypatch, _status(oldest_event_age_s=480.0, connected_since=fresh))
    assert spy["start"] == 0
    f = next(x for x in findings if x.key == "meic.streamer")
    assert "restarted" not in f.title.lower()


def test_auto_restart_off_reports_but_does_not_act(monkeypatch, spy):
    findings = _run(monkeypatch, _status(oldest_event_age_s=480.0), _mcfg(auto_restart=False))
    f = next(x for x in findings if x.key == "meic.streamer")
    assert f.status == watchdog.WARN and "stalled" in f.title.lower()
    assert "auto_restart off" in f.message
    assert spy["start"] == 0


def test_threshold_is_configurable(monkeypatch, spy):
    findings = _run(monkeypatch, _status(oldest_event_age_s=100.0),
                    _mcfg(stale_restart_seconds=60))
    assert spy["start"] == 1
    assert "limit 60s" in next(x for x in findings if x.key == "meic.streamer").message


def test_a_dead_streamer_still_takes_the_old_path(monkeypatch, spy):
    """The original liveness restart must keep working — this adds a case, it does not replace one."""
    findings = _run(monkeypatch, _status(running=False))
    f = next(x for x in findings if x.key == "meic.streamer")
    assert "down" in f.title.lower() and spy["start"] == 1


# --------------------------------------------------------------------------- age parsing
def test_stale_age_prefers_the_numeric_field():
    assert watchdog._streamer_stale_age({"oldest_event_age_s": 12.5}) == 12.5
    assert watchdog._streamer_stale_age({"stale_age_s": 7.0}) == 7.0


def test_stale_age_falls_back_to_a_timestamp():
    """A module reporting only last_event_at must still be checkable."""
    seen = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
    age = watchdog._streamer_stale_age({"last_event_at": seen})
    assert age is not None and 290 < age < 310


def test_unreadable_status_never_triggers_a_restart(monkeypatch, spy):
    """No age means no evidence of a stall. Restarting on missing data would make a status-parsing
    bug look like a streamer outage and thrash a healthy feed."""
    assert watchdog._streamer_stale_age({}) is None
    assert watchdog._streamer_stale_age({"last_event_at": "not-a-date"}) is None
    findings = _run(monkeypatch, _status(oldest_event_age_s=None, last_event_at=None))
    assert spy["start"] == 0
    assert next(x for x in findings if x.key == "meic.streamer").status == watchdog.OK


def test_stall_check_is_session_only(monkeypatch, spy):
    """Quotes legitimately stop outside RTH — restarting all night would be pure churn."""
    class R:
        returncode = 0
        stdout = json.dumps(_status(oldest_event_age_s=99999.0))
    monkeypatch.setattr(watchdog, "_run_module", lambda *a, **k: R())
    monkeypatch.setattr(watchdog.tasks, "exists", lambda _n: True)
    findings = watchdog._check_meic("meic", _mcfg(), in_session=False)
    assert spy["start"] == 0
    assert not [f for f in findings if f.key == "meic.streamer"]
