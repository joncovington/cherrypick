"""The pre-open supervision pass: streamer liveness only, tight interval, short window.

What this protects: the full tick runs every 10 minutes and streamer supervision starts at 09:15, so
the first supervising tick of the day could land ~09:25 — minutes before the 09:30–09:35 opening
range, which cannot be reconstructed once missed.

The properties worth pinning are about what it *doesn't* do: it must not duplicate the streamer
logic, must not write the heartbeat, and must not act on a day the market is closed.
"""

from datetime import datetime

import pytest

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import timeutil
from cherrypick.orchestrator import watchdog as wd

pytestmark = pytest.mark.unit

STREAMER = {
    "enabled": True,
    "path": "../streamer",
    "status_argv": ["run.py", "--status"],
    "start_argv": ["run.py"],
    "auto_restart": True,
}


def _et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=timeutil._tz("America/New_York"))


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """A streamer checkout that exists, a captured notify path, and a stubbed health check."""
    root = tmp_path / "streamer"
    root.mkdir()
    monkeypatch.setattr(cfgmod, "module_root", lambda spec, name=None: root)

    calls = []
    monkeypatch.setattr(
        wd,
        "_check_streamer_health",
        lambda label, r, spec: calls.append(label) or [wd.Finding(label, wd.OK, "Streamer", "running")],
    )
    sent = []
    monkeypatch.setattr(wd, "_process_notifications", lambda f, n, r, **kw: sent.extend(f))
    monkeypatch.setattr(wd, "_log_findings", lambda f, o: None)
    monkeypatch.setattr(timeutil, "now_et", lambda tz="America/New_York": _et(2026, 8, 6, 9, 4))
    monkeypatch.setattr(timeutil, "load_holidays", lambda *a, **k: {"2026-09-07"})
    return {"root": root, "checked": calls, "sent": sent}


def _cfg(**over):
    cfg = {"streamer": dict(STREAMER), "watchdog": {"preopen": {"enabled": True}}, "notify": {}}
    cfg.update(over)
    return cfg


# --------------------------------------------------------------------------- what it checks
def test_it_reuses_the_streamer_health_check(wired):
    """Not a copy: `_check_streamer_health` carries the 2026-07-20 silence-restart lesson (a
    live-but-quiet socket reporting running=true), and a second copy would drift from it."""
    res = wd.run_preopen(_cfg())
    assert wired["checked"] == ["streamer"]
    assert res["overall"] == wd.OK


def test_it_falls_back_to_a_modules_own_streamer(wired):
    """Before the standalone-producer cutover a module owns the streamer. The pre-open window is
    worth protecting either way, so it follows the same producer resolution the full tick uses."""
    cfg = _cfg(streamer={"enabled": False})
    cfg["modules"] = {"meic": {"enabled": True, "streamer": dict(STREAMER)}}
    wd.run_preopen(cfg)
    assert wired["checked"] == ["meic.streamer"]


def test_findings_go_through_the_normal_notify_path(wired, monkeypatch):
    """A dead streamer at 09:04 must alert exactly like one at 11:04 — same dedup, same re-notify."""
    monkeypatch.setattr(
        wd,
        "_check_streamer_health",
        lambda label, r, spec: [wd.Finding(label, wd.WARN, "Streamer down", "not running")],
    )
    res = wd.run_preopen(_cfg())
    assert res["overall"] == wd.WARN
    assert [f.title for f in wired["sent"]] == ["Streamer down"]


# --------------------------------------------------------------------------- what it refuses to do
def test_it_writes_no_heartbeat(wired, tmp_path, monkeypatch):
    """The full tick owns the heartbeat. A second writer would make "when did the watchdog last run"
    ambiguous — and the dashboard reads that file for health."""
    beat = tmp_path / "watchdog.last.json"
    monkeypatch.setattr(wd, "_HEARTBEAT", beat)
    wd.run_preopen(_cfg())
    assert not beat.exists()


def test_a_closed_market_stops_at_the_door(wired, monkeypatch):
    """`schtasks /SC MINUTE` has no day filter, so weekends and holidays reach this command. It
    gates on the same calendar as everything else rather than restarting a streamer nobody needs."""
    monkeypatch.setattr(timeutil, "now_et", lambda tz="America/New_York": _et(2026, 9, 7, 9, 4))
    res = wd.run_preopen(_cfg())
    assert res["skipped"] == "not a trading day"
    assert wired["checked"] == []


def test_disabled_does_nothing(wired):
    res = wd.run_preopen(_cfg(watchdog={"preopen": {"enabled": False}}))
    assert res["skipped"] == "preopen not enabled"
    assert wired["checked"] == []


def test_no_streamer_configured_is_a_skip_not_a_finding(wired):
    """A suite with no producer at all should stay quiet rather than invent a WARN every 2 minutes
    for 35 minutes."""
    res = wd.run_preopen(_cfg(streamer={"enabled": False}, modules={}))
    assert res["skipped"] == "no streamer configured"
    assert wired["sent"] == []


# --------------------------------------------------------------------------- config
def test_preopen_is_on_by_default():
    """It protects a window that cannot be recovered once missed, so opting IN would put the
    protection behind a step nobody takes until after it has cost them a session."""
    s = cfgmod.preopen_settings({})
    assert s["enabled"] is True
    assert (s["start"], s["end"]) == ("09:00", "09:35")
    assert s["interval_minutes"] == 2
    assert s["task_name"] == "cherrypick-preopen"


def test_preopen_window_covers_the_gap_it_exists_for():
    """The failure it closes: supervision from 09:15 on a 10-minute tick can first land ~09:25, and
    a restart needs the 240s settling window before quotes are trustworthy."""
    s = cfgmod.preopen_settings({})
    start_h, start_m = (int(x) for x in s["start"].split(":"))
    end_h, end_m = (int(x) for x in s["end"].split(":"))
    assert (start_h, start_m) < (9, 15)  # earlier than the full tick's own session gate
    assert (end_h, end_m) >= (9, 35)  # through the close of the opening-range window
