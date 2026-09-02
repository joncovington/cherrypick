"""Phase-0 watchdog reliability fixes, pinned.

Four independent faults with the same failure shape — a guardian that lies quietly:
(1) module-log freshness resolved against the checkout after logs moved to the shared
logs home, silently killing half the freshness signal; (2) the entry SLA had no grace
period, raising CRITICAL for a 15:45 run still inside its own 30-minute subprocess
window; (3) trade_notifier's read-modify-write raced between the 2-minute task and the
watchdog tick, replaying already-notified fills; (4) _check_eod marked the day fired
even when the detached launch failed, losing the digest with no signal.
"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import trade_notifier as tn
from cherrypick.orchestrator import watchdog as wd

pytestmark = pytest.mark.unit

_ET = ZoneInfo("America/New_York")


# --------------------------------------------------------------------- (1) freshness log path
def test_freshness_reads_the_shared_logs_home_not_the_checkout(monkeypatch, tmp_path):
    """A fresh log in ~/.cherrypick/logs/<name>/ must count as freshness even though the
    config string is checkout-relative and no file exists under the module root."""
    logs = tmp_path / "logs" / "flies"
    logs.mkdir(parents=True)
    (logs / "flies_paper.log").write_text("alive", encoding="utf-8")

    monkeypatch.setattr(wd.cfgmod, "module_logs_dir", lambda name: tmp_path / "logs" / name)
    monkeypatch.setattr(wd.cfgmod, "module_root", lambda mcfg, name=None: tmp_path / "checkout")
    monkeypatch.setattr(wd.tasks, "exists", lambda _n: True)

    mcfg = {"paper": {"kind": "self_healing", "task_name": "t", "log": "flies_paper.log"}}
    findings = wd._check_meic("flies", mcfg, in_session=True)
    fresh = next(f for f in findings if f.key == "flies.fresh")
    assert fresh.status == wd.OK
    assert "min old" in fresh.message


def test_freshness_still_warns_when_nothing_was_written(monkeypatch, tmp_path):
    monkeypatch.setattr(wd.cfgmod, "module_logs_dir", lambda name: tmp_path / "logs" / name)
    monkeypatch.setattr(wd.cfgmod, "module_root", lambda mcfg, name=None: tmp_path / "checkout")
    monkeypatch.setattr(wd.tasks, "exists", lambda _n: True)
    # Point the heartbeat at the tmp home too — without this, the developer machine's LIVE flies
    # loop keeps ~/.cherrypick/state/flies.heartbeat fresh and this test reads production state.
    monkeypatch.setattr(wd.core_home, "heartbeat_path", lambda name: tmp_path / "state" / f"{name}.heartbeat")

    mcfg = {"paper": {"kind": "self_healing", "task_name": "t", "log": "flies_paper.log"}}
    findings = wd._check_meic("flies", mcfg, in_session=True)
    fresh = next(f for f in findings if f.key == "flies.fresh")
    assert fresh.status == wd.WARN


def test_fresh_heartbeat_keeps_an_idle_loop_ok(monkeypatch, tmp_path):
    """The calendars flap of 2026-08-21: a healthy loop with no positions writes no DB rows and no
    log lines, so both conditional signals age out and `.fresh` WARNs for most of a session. The
    per-tick heartbeat (written unconditionally) must be enough on its own."""
    monkeypatch.setattr(wd.cfgmod, "module_logs_dir", lambda name: tmp_path / "logs" / name)
    monkeypatch.setattr(wd.cfgmod, "module_root", lambda mcfg, name=None: tmp_path / "checkout")
    monkeypatch.setattr(wd.tasks, "exists", lambda _n: True)
    monkeypatch.setattr(wd.core_home, "heartbeat_path", lambda name: tmp_path / "state" / f"{name}.heartbeat")

    hb = tmp_path / "state" / "calendars.heartbeat"
    hb.parent.mkdir(parents=True, exist_ok=True)
    hb.write_text("tick")

    mcfg = {"paper": {"kind": "self_healing", "task_name": "t", "log": "calendars_paper.log"}}
    findings = wd._check_meic("calendars", mcfg, in_session=True)
    fresh = next(f for f in findings if f.key == "calendars.fresh")
    assert fresh.status == wd.OK


# --------------------------------------------------------------------- (2) entry SLA grace
def _sla_findings(monkeypatch, tmp_path, now, paper_extra=None):
    monkeypatch.setattr(cfgmod, "STATE_DIR", tmp_path)
    paper = {"kind": "cherrypick_scheduled", "entry_time": "15:45", **(paper_extra or {})}
    findings = wd._check_earnings("earnings", {"paper": paper}, now, True)
    return [f for f in findings if f.key == "earnings.entry_sla"]


def test_entry_sla_holds_fire_inside_the_grace_window(monkeypatch, tmp_path):
    """15:55, no heartbeat yet: the 15:45 run may still be inside its 30-minute
    subprocess ceiling — no finding at all, not a CRITICAL."""
    now = datetime(2026, 7, 21, 15, 55, tzinfo=_ET)
    assert _sla_findings(monkeypatch, tmp_path, now) == []


def test_entry_sla_fires_after_the_grace_window(monkeypatch, tmp_path):
    now = datetime(2026, 7, 21, 16, 21, tzinfo=_ET)  # 15:45 + 35m grace = 16:20
    sla = _sla_findings(monkeypatch, tmp_path, now)
    assert sla and sla[0].status == wd.CRITICAL


def test_entry_sla_grace_is_configurable(monkeypatch, tmp_path):
    now = datetime(2026, 7, 21, 15, 46, tzinfo=_ET)
    sla = _sla_findings(monkeypatch, tmp_path, now, {"entry_sla_grace_minutes": 0})
    assert sla and sla[0].status == wd.CRITICAL


def test_entry_sla_ok_when_heartbeat_landed(monkeypatch, tmp_path):
    (tmp_path / "earnings_entry.last.json").write_text(
        json.dumps({"date": "2026-07-21", "ok": True}), encoding="utf-8"
    )
    now = datetime(2026, 7, 21, 16, 30, tzinfo=_ET)
    sla = _sla_findings(monkeypatch, tmp_path, now)
    assert sla and sla[0].status == wd.OK


# --------------------------------------------------------------------- (3) trade-notify lock
@pytest.fixture
def lock_env(monkeypatch, tmp_path):
    monkeypatch.setattr(tn, "_LOCK", tmp_path / "trade_notify.lock")
    monkeypatch.setattr(tn.cfgmod, "ensure_dirs", lambda: None)
    return tmp_path


def test_lock_round_trip(lock_env):
    assert tn._acquire_lock() is True
    assert tn._acquire_lock() is False  # second holder loses
    tn._release_lock()
    assert tn._acquire_lock() is True  # and can re-acquire after release
    tn._release_lock()


def test_run_skips_when_lock_is_held(lock_env):
    assert tn._acquire_lock()
    try:
        result = tn.run(cfg={})
        assert result["ok"] is True
        assert "lock" in result["skipped"]
    finally:
        tn._release_lock()


def test_stale_lock_is_taken_over(lock_env, monkeypatch):
    """A crashed holder must not wedge trade notification forever."""
    import os

    assert tn._acquire_lock()
    old = tn._LOCK.stat().st_mtime - tn._LOCK_STALE_SECONDS - 60
    os.utime(tn._LOCK, (old, old))
    assert tn._acquire_lock() is True
    tn._release_lock()


def test_run_releases_the_lock_even_on_failure(lock_env, monkeypatch):
    monkeypatch.setattr(tn.cfgmod, "load_config", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        tn.run()
    assert not tn._LOCK.exists()


# --------------------------------------------------------------------- (4) EOD launch failure
@pytest.fixture
def eod_env(tmp_path, monkeypatch):
    monkeypatch.setattr(wd, "_STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(wd.cfgmod, "ensure_dirs", lambda: None)
    logs = tmp_path / "logs"
    monkeypatch.setattr(wd.cfgmod, "module_logs_dir", lambda name: logs / name)
    monkeypatch.setattr(wd.cfgmod, "enabled_modules", lambda cfg: {"meic": {}})
    d = logs / "meic"
    d.mkdir(parents=True)
    (d / "paper-eod-2026-07-21.md").write_text("x", encoding="utf-8")
    notices: list[tuple] = []

    class _FakeNotifier:
        def __init__(self, _cfg):
            pass

        def notify(self, level, key, title, message):
            notices.append((level, key, title))

    monkeypatch.setattr(wd, "Notifier", _FakeNotifier)
    return {"tmp": tmp_path, "notices": notices}


def _cfg():
    return {
        "eod_digest": {"enabled": True, "deadline": "16:45"},
        "eod_insight": {"enabled": False},
        "modules": {},
    }
