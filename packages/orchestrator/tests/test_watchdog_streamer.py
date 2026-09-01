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
        wd,
        "_run_module",
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
    _status(
        monkeypatch,
        {"running": True, "oldest_event_age_s": 999, "connected_since": "2020-01-01T00:00:00+00:00"},
    )
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


def test_chain_fetch_error_triggers_restart_even_with_healthy_aggregate_ages(monkeypatch, calls, tmp_path):
    # running=true, aggregate ages fresh (other symbols ticking fine), but XSP's chain fetch failed --
    # the 2026-07-31 incident this check exists for.
    _status(
        monkeypatch,
        {
            "running": True,
            "oldest_event_age_s": 1.0,
            "underlyings_stale_age_s": 1.0,
            "connected_since": "2020-01-01T00:00:00+00:00",
            "chain_fetch_errors": {"XSP": "Couldn't parse response: <html>"},
        },
    )
    findings = wd._check_streamer_health("streamer", tmp_path, _spec())
    assert "stalled" in findings[0].title
    assert "XSP" in findings[0].message
    assert calls["stop"] and calls["start"] == [["run.py"]]


def test_chain_fetch_error_no_auto_restart_just_warns(monkeypatch, calls, tmp_path):
    _status(
        monkeypatch,
        {
            "running": True,
            "oldest_event_age_s": 1.0,
            "underlyings_stale_age_s": 1.0,
            "connected_since": "2020-01-01T00:00:00+00:00",
            "chain_fetch_errors": {"XSP": "boom"},
        },
    )
    findings = wd._check_streamer_health("streamer", tmp_path, _spec(auto_restart=False))
    assert findings[0].title == "Streamer stalled"
    assert calls["start"] == []


def test_dead_underlying_triggers_restart_even_with_healthy_aggregate_ages(monkeypatch, calls, tmp_path):
    # running=true, every aggregate fresh (SPX ticking fine), but TQQQ's own spot subscription died
    # mid-flight -- the 2026-08-17..21 incident this check exists for: four sessions dead behind a
    # live SPX, pmcc reading no_long_chain, and nothing restarted.
    _status(
        monkeypatch,
        {
            "running": True,
            "oldest_event_age_s": 1.0,
            "underlyings_stale_age_s": 1.0,
            "connected_since": "2020-01-01T00:00:00+00:00",
            "chain_fetch_errors": {},
            "dead_underlyings": {"TQQQ": 311302.0},
        },
    )
    findings = wd._check_streamer_health("streamer", tmp_path, _spec())
    assert "stalled" in findings[0].title
    assert "TQQQ" in findings[0].message and "dead spot subscription" in findings[0].message
    assert calls["stop"] and calls["start"] == [["run.py"]]


def test_dead_underlying_no_auto_restart_just_warns(monkeypatch, calls, tmp_path):
    _status(
        monkeypatch,
        {
            "running": True,
            "oldest_event_age_s": 1.0,
            "underlyings_stale_age_s": 1.0,
            "connected_since": "2020-01-01T00:00:00+00:00",
            "dead_underlyings": {"TQQQ": 900.5},
        },
    )
    findings = wd._check_streamer_health("streamer", tmp_path, _spec(auto_restart=False))
    assert findings[0].title == "Streamer stalled"
    assert calls["start"] == []


def test_dead_underlyings_absent_degrades_cleanly(monkeypatch, calls, tmp_path):
    # A producer on code from before this field must not trip anything -- the running 08-17
    # producer is exactly that during the fault it can't see.
    _status(
        monkeypatch,
        {
            "running": True,
            "oldest_event_age_s": 1.0,
            "underlyings_stale_age_s": 1.0,
            "chain_fetch_errors": {},
        },
    )
    findings = wd._check_streamer_health("streamer", tmp_path, _spec())
    assert findings[0].status == wd.OK
    assert calls["start"] == [] and calls["stop"] == []


def test_no_chain_fetch_errors_and_healthy_ages_is_ok(monkeypatch, calls, tmp_path):
    _status(monkeypatch, {"running": True, "chain_fetch_errors": {}})
    findings = wd._check_streamer_health("streamer", tmp_path, _spec())
    assert findings[0].status == wd.OK
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


def test_producer_off_hours_emits_ok_liveness_finding_without_restart(monkeypatch, calls, tmp_path):
    _status(monkeypatch, {"running": True})
    cfg = {"streamer": _spec(enabled=True, path=str(tmp_path))}
    findings = wd._check_producer(cfg, in_session=False)
    assert len(findings) == 1
    assert findings[0].status == wd.OK
    assert findings[0].key == "streamer"
    assert "off-hours" in findings[0].message
    assert calls["start"] == [] and calls["stop"] == []


def test_producer_off_hours_down_is_ok_not_warn(monkeypatch, calls, tmp_path):
    _status(monkeypatch, {"running": False})
    cfg = {"streamer": _spec(enabled=True, path=str(tmp_path))}
    findings = wd._check_producer(cfg, in_session=False)
    assert findings[0].status == wd.OK
    assert "not running" in findings[0].message
    assert calls["start"] == [] and calls["stop"] == []


def test_producer_off_hours_missing_checkout_is_ok_not_warn(tmp_path):
    cfg = {"streamer": _spec(enabled=True, path=str(tmp_path / "nope"))}
    findings = wd._check_producer(cfg, in_session=False)
    assert findings[0].status == wd.OK
    assert str(tmp_path) not in findings[0].message


def test_producer_active_when_enabled(monkeypatch, calls, tmp_path):
    _status(monkeypatch, {"running": True})
    cfg = {"streamer": _spec(enabled=True, path=str(tmp_path))}
    findings = wd._check_producer(cfg, in_session=True)
    assert len(findings) == 1 and findings[0].status == wd.OK and findings[0].key == "streamer"


def test_producer_missing_checkout_warns(tmp_path):
    cfg = {"streamer": _spec(enabled=True, path=str(tmp_path / "nope"))}
    findings = wd._check_producer(cfg, in_session=True)
    assert findings[0].status == wd.WARN and "checkout missing" in findings[0].title
    # this Finding's message is rendered verbatim into the served/static dashboard's Findings
    # panel (dashboard._findings_html) -- must never carry the absolute checkout path.
    assert str(tmp_path) not in findings[0].message


def test_service_missing_checkout_warns_without_leaking_path(tmp_path):
    cfg = {"services": [{"id": "gex-recorder", "enabled": True, "path": str(tmp_path / "nope")}]}
    findings = wd._check_services(cfg)
    assert findings[0].status == wd.WARN and "checkout missing" in findings[0].title
    assert str(tmp_path) not in findings[0].message


def _service_cfg(tmp_path, **overrides):
    svc = {"id": "gex-recorder", "enabled": True, "path": str(tmp_path), **_spec(**overrides)}
    return {"services": [svc]}


def test_service_stalled_is_recycled_stop_then_start(monkeypatch, calls, tmp_path):
    """Alive-but-wedged (the service's own heartbeat went silent): a plain start would lose to the
    wedged pid's single-instance lock, so the watchdog must stop THEN start."""
    _status(monkeypatch, {"running": True, "stalled": True, "heartbeat_age_seconds": 400})
    findings = wd._check_services(_service_cfg(tmp_path))
    assert findings[0].status == wd.WARN and "stalled — recycled" in findings[0].title
    assert "400" in findings[0].message
    assert len(calls["stop"]) == 1 and calls["start"] == [["run.py"]]


def test_service_stalled_without_auto_restart_only_warns(monkeypatch, calls, tmp_path):
    _status(monkeypatch, {"running": True, "stalled": True})
    findings = wd._check_services(_service_cfg(tmp_path, auto_restart=False))
    assert findings[0].status == wd.WARN and findings[0].title == "gex-recorder stalled"
    assert calls["stop"] == [] and calls["start"] == []


def test_service_running_not_stalled_takes_the_stale_config_path(monkeypatch, calls, tmp_path):
    _status(monkeypatch, {"running": True, "stalled": False})
    monkeypatch.setattr(
        wd, "_recycle_if_stale", lambda svc, root, sid: wd.Finding(f"service.{sid}", wd.OK, sid, "running")
    )
    findings = wd._check_services(_service_cfg(tmp_path))
    assert findings[0].status == wd.OK
    assert calls["stop"] == [] and calls["start"] == []


def test_service_without_stall_key_is_unchanged_behavior(monkeypatch, calls, tmp_path):
    """A service that never learned to publish `stalled` (or a pre-heartbeat daemon) keeps the
    original contract: running means healthy, nothing is touched."""
    _status(monkeypatch, {"running": True})
    monkeypatch.setattr(
        wd, "_recycle_if_stale", lambda svc, root, sid: wd.Finding(f"service.{sid}", wd.OK, sid, "running")
    )
    findings = wd._check_services(_service_cfg(tmp_path))
    assert findings[0].status == wd.OK
    assert calls["stop"] == [] and calls["start"] == []


# --- reconnect churn: the state no other check can see --------------------------------


def _churn(monkeypatch, tmp_path, label="streamer"):
    from cherrypick.orchestrator import config as cfgmod

    monkeypatch.setattr(cfgmod, "state_file", lambda name: tmp_path / name)
    return label


def test_churn_needs_a_baseline_before_it_can_judge(monkeypatch, tmp_path):
    """The first observation only records — a cumulative counter says nothing on its own."""
    label = _churn(monkeypatch, tmp_path)
    assert wd._streamer_churn_finding(label, {"reconnect_count": 40}) is None


def test_churn_warns_on_a_fast_reconnect_rate(monkeypatch, tmp_path):
    """2026-08-24's shape: the producer reported running and streamed between kills, so every
    other branch passed while it reconnected ~60x/hour."""
    label = _churn(monkeypatch, tmp_path)
    wd._streamer_churn_finding(label, {"reconnect_count": 40})
    # Rewind the baseline 10 minutes and jump the counter.
    p = tmp_path / "streamer-reconnects.json"
    state = json.loads(p.read_text())
    state[label]["at"] -= 600
    p.write_text(json.dumps(state))

    f = wd._streamer_churn_finding(label, {"reconnect_count": 50})
    assert f is not None and f.status == wd.WARN
    assert "churn" in f.title.lower()
    assert "10 reconnect" in f.message


def test_churn_is_quiet_for_an_ordinary_reconnect(monkeypatch, tmp_path):
    """A healthy day takes the odd reconnect; only a sustained RATE is the signal."""
    label = _churn(monkeypatch, tmp_path)
    wd._streamer_churn_finding(label, {"reconnect_count": 40})
    p = tmp_path / "streamer-reconnects.json"
    state = json.loads(p.read_text())
    state[label]["at"] -= 3600
    p.write_text(json.dumps(state))
    assert wd._streamer_churn_finding(label, {"reconnect_count": 42}) is None


def test_churn_rebaselines_when_the_counter_resets(monkeypatch, tmp_path):
    """A daemon restart zeroes the counter; a negative delta is a new baseline, never churn."""
    label = _churn(monkeypatch, tmp_path)
    wd._streamer_churn_finding(label, {"reconnect_count": 80})
    assert wd._streamer_churn_finding(label, {"reconnect_count": 0}) is None
    state = json.loads((tmp_path / "streamer-reconnects.json").read_text())
    assert state[label]["count"] == 0


def test_churn_ignores_a_status_without_the_counter(monkeypatch, tmp_path):
    label = _churn(monkeypatch, tmp_path)
    assert wd._streamer_churn_finding(label, {"running": True}) is None
