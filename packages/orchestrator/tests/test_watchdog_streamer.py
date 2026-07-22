"""The shared streamer health check (`_check_streamer_health`) and the top-level producer watchdog
(`_check_producer`). This is the silence-based restart contract MEIC's streamer and the standalone
producer share — the load-bearing bit of the walk-away guarantee — so it's exercised directly.
"""

import json
from datetime import datetime, timezone

import pytest

from cherrypick.orchestrator import watchdog as wd

pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout


@pytest.fixture
def calls(monkeypatch):
    """Record restart side-effects instead of shelling out."""
    rec = {"start": [], "stop": []}
    monkeypatch.setattr(wd, "_start_streamer", lambda root, argv: (rec["start"].append(argv), True)[1])
    monkeypatch.setattr(wd, "_stop_streamer", lambda root, spec: (rec["stop"].append(spec), True)[1])
    return rec


def _spec(**overrides):
    spec = {
        "status_argv": ["run.py", "--status"],
        "start_argv": ["run.py"],
        "stop_argv": ["run.py", "--stop"],
        "auto_restart": True,
        "stale_restart_seconds": 240,
    }
    spec.update(overrides)
    return spec


def _status(monkeypatch, payload, returncode=0):
    monkeypatch.setattr(
        wd, "_run_module",
        lambda root, argv, timeout=15: _Result(returncode, json.dumps(payload)),
    )


def test_running_fresh_is_ok(monkeypatch, calls, tmp_path):
    _status(monkeypatch, {"running": True})
    findings = wd._check_streamer_health("streamer", tmp_path, _spec())
    assert len(findings) == 1 and findings[0].status == wd.OK
    assert calls["start"] == [] and calls["stop"] == []


def test_down_triggers_restart(monkeypatch, calls, tmp_path):
    _status(monkeypatch, {"running": False})
    findings = wd._check_streamer_health("streamer", tmp_path, _spec())
    assert findings[0].status == wd.WARN and "was down" in findings[0].title
    assert calls["start"] == [["run.py"]] and calls["stop"] == []


def test_down_no_auto_restart_just_warns(monkeypatch, calls, tmp_path):
    _status(monkeypatch, {"running": False})
    findings = wd._check_streamer_health("streamer", tmp_path, _spec(auto_restart=False))
    assert findings[0].status == wd.WARN and findings[0].title == "Streamer down"
    assert calls["start"] == []


def test_stalled_stops_then_restarts(monkeypatch, calls, tmp_path):
    # running=true but silent for 999s, connected long ago (not settling) -> stop then start.
    _status(monkeypatch, {"running": True, "oldest_event_age_s": 999,
                          "connected_since": "2020-01-01T00:00:00+00:00"})
    findings = wd._check_streamer_health("streamer", tmp_path, _spec())
    assert "stalled" in findings[0].title
    assert calls["stop"] and calls["start"] == [["run.py"]]


def test_stalled_but_settling_does_not_restart(monkeypatch, calls, tmp_path):
    # Just reconnected (connection age < limit): stale but still resubscribing — must NOT restart-loop.
    recent = datetime.now(timezone.utc).isoformat()
    _status(monkeypatch, {"running": True, "oldest_event_age_s": 999, "connected_since": recent})
    findings = wd._check_streamer_health("streamer", tmp_path, _spec())
    assert findings[0].title == "Streamer stalled"  # reported, but not restart-looped while warming up
    assert calls["start"] == [] and calls["stop"] == []


def test_status_unreadable_is_unknown(monkeypatch, calls, tmp_path):
    _status(monkeypatch, {}, returncode=1)
    findings = wd._check_streamer_health("streamer", tmp_path, _spec())
    assert findings[0].title == "Streamer status unknown"
    assert calls["start"] == []


def test_label_is_used(monkeypatch, calls, tmp_path):
    _status(monkeypatch, {"running": True})
    findings = wd._check_streamer_health("meic.streamer", tmp_path, _spec())
    assert findings[0].key == "meic.streamer"


# --------------------------------------------------------------------------- top-level producer
def test_producer_dormant_without_config():
    assert wd._check_producer({}, in_session=True) == []
    assert wd._check_producer({"streamer": {"enabled": False}}, in_session=True) == []


def test_producer_not_checked_off_hours(tmp_path):
    cfg = {"streamer": _spec(enabled=True, path=str(tmp_path))}
    assert wd._check_producer(cfg, in_session=False) == []


def test_producer_active_when_enabled(monkeypatch, calls, tmp_path):
    _status(monkeypatch, {"running": True})
    cfg = {"streamer": _spec(enabled=True, path=str(tmp_path))}
    findings = wd._check_producer(cfg, in_session=True)
    assert len(findings) == 1 and findings[0].status == wd.OK and findings[0].key == "streamer"


def test_producer_missing_checkout_warns(tmp_path):
    cfg = {"streamer": _spec(enabled=True, path=str(tmp_path / "nope"))}
    findings = wd._check_producer(cfg, in_session=True)
    assert findings[0].status == wd.WARN and "checkout missing" in findings[0].title
