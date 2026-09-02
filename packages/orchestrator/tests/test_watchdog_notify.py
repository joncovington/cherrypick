"""Reliability tests: the watchdog notification state machine.

This is the core of the walk-away guarantee — a failure must notify, a persistent failure must not
spam, and a recovery must be announced once. The Stage-0 manual test (delete task -> CRITICAL ->
notify -> reinstall -> Recovered) is encoded here against a fake notifier and an injected clock.
"""

from datetime import datetime, timedelta, timezone

import pytest

from cherrypick.orchestrator import watchdog
from cherrypick.orchestrator.watchdog import Finding, _process_notifications

pytestmark = pytest.mark.unit


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Point the watchdog state file at a temp path so tests don't touch real state."""
    monkeypatch.setattr(watchdog, "_STATE_FILE", tmp_path / "watchdog_state.json")
    return tmp_path


def _crit():
    return Finding("meic.task", watchdog.CRITICAL, "MEIC paper task missing", "not registered")


def _ok():
    return Finding("meic.task", watchdog.OK, "MEIC paper task", "registered")


def test_new_critical_notifies_once(isolated_state, fake_notifier):
    t0 = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    _process_notifications([_crit()], fake_notifier, renotify_minutes=60, now=t0)
    assert len(fake_notifier.sent) == 1
    assert fake_notifier.sent[0]["level"] == watchdog.CRITICAL
    assert fake_notifier.sent[0]["key"] == "meic.task"


def test_dedup_within_renotify_window(isolated_state, fake_notifier):
    t0 = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    _process_notifications([_crit()], fake_notifier, renotify_minutes=60, now=t0)
    # 10 minutes later, still broken -> must NOT re-notify.
    _process_notifications([_crit()], fake_notifier, renotify_minutes=60, now=t0 + timedelta(minutes=10))
    assert len(fake_notifier.sent) == 1


def test_renotify_after_window(isolated_state, fake_notifier):
    t0 = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    _process_notifications([_crit()], fake_notifier, renotify_minutes=60, now=t0)
    _process_notifications([_crit()], fake_notifier, renotify_minutes=60, now=t0 + timedelta(minutes=61))
    assert len(fake_notifier.sent) == 2


def test_status_escalation_notifies(isolated_state, fake_notifier):
    t0 = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    warn = Finding("meic.streamer", watchdog.WARN, "Streamer down", "not running")
    crit = Finding("meic.streamer", watchdog.CRITICAL, "Streamer down", "still not running")
    _process_notifications([warn], fake_notifier, renotify_minutes=60, now=t0)
    _process_notifications([crit], fake_notifier, renotify_minutes=60, now=t0 + timedelta(minutes=5))
    assert [s["level"] for s in fake_notifier.sent] == [watchdog.WARN, watchdog.CRITICAL]


def test_recovery_notifies_once_then_silent(isolated_state, fake_notifier):
    t0 = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    _process_notifications([_crit()], fake_notifier, renotify_minutes=60, now=t0)
    # Recovered.
    _process_notifications([_ok()], fake_notifier, renotify_minutes=60, now=t0 + timedelta(minutes=5))
    # Still healthy on the next pass -> no further notifications.
    _process_notifications([_ok()], fake_notifier, renotify_minutes=60, now=t0 + timedelta(minutes=10))
    levels = [s["level"] for s in fake_notifier.sent]
    assert levels == [watchdog.CRITICAL, "INFO"]
    assert fake_notifier.sent[-1]["title"].startswith("Recovered")


def test_healthy_from_start_is_silent(isolated_state, fake_notifier):
    t0 = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    _process_notifications([_ok()], fake_notifier, renotify_minutes=60, now=t0)
    assert fake_notifier.sent == []


# --------------------------------------------------------------------------- drawdown (drift) alert
from cherrypick.orchestrator import report as report_mod  # noqa: E402


def _rep(suite_net, *, trades=1, meic_net=None, meic_ok=True, meic_trades=1):
    modules = {}
    if meic_net is not None:
        modules["meic"] = {"ok": meic_ok, "trades": meic_trades, "net_pnl": meic_net}
    return {"ok": True, "suite": {"trades": trades, "net_pnl": suite_net}, "modules": modules}


def test_drawdown_disabled_when_unconfigured():
    assert watchdog._check_drawdown({"watchdog": {}}) == []


def test_drawdown_warn_at_floor(monkeypatch):
    monkeypatch.setattr(report_mod, "run", lambda cfg: _rep(-1000.0))
    cfg = {"watchdog": {"drawdown": {"suite_floor": -1000, "critical_multiplier": 2}}}
    fs = watchdog._check_drawdown(cfg)
    assert [f.key for f in fs] == ["drawdown.suite"]
    assert fs[0].status == watchdog.WARN


def test_drawdown_critical_at_multiple(monkeypatch):
    monkeypatch.setattr(report_mod, "run", lambda cfg: _rep(-2000.0))
    cfg = {"watchdog": {"drawdown": {"suite_floor": -1000, "critical_multiplier": 2}}}
    fs = watchdog._check_drawdown(cfg)
    assert fs[0].status == watchdog.CRITICAL


def test_drawdown_silent_above_floor(monkeypatch):
    monkeypatch.setattr(report_mod, "run", lambda cfg: _rep(-500.0))
    cfg = {"watchdog": {"drawdown": {"suite_floor": -1000}}}
    assert watchdog._check_drawdown(cfg) == []


def test_drawdown_per_module_floor(monkeypatch):
    monkeypatch.setattr(report_mod, "run", lambda cfg: _rep(0.0, trades=0, meic_net=-800.0))
    cfg = {"watchdog": {"drawdown": {"module_floors": {"meic": -750}, "critical_multiplier": 2}}}
    fs = watchdog._check_drawdown(cfg)
    assert [f.key for f in fs] == ["drawdown.meic"]
    assert fs[0].status == watchdog.WARN


def test_drawdown_skips_module_with_no_trades(monkeypatch):
    monkeypatch.setattr(report_mod, "run", lambda cfg: _rep(0.0, trades=0, meic_net=-800.0, meic_trades=0))
    cfg = {"watchdog": {"drawdown": {"module_floors": {"meic": -750}}}}
    assert watchdog._check_drawdown(cfg) == []
