"""The recorder's published-liveness contract: the loop beats a heartbeat file, and --status turns
a silent beat into `stalled: true` — the signal the orchestrator's watchdog recycles on. A pid
check alone reads a wedged loop as healthy (the 2026-07-23 shape with a different cause), which is
the gap this closes. The stall guard was verified by breaking it on purpose (the house rule)."""

from __future__ import annotations

import os
import time

import pytest

from cherrypick.gex import service


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    c = {"history_db_path": tmp_path / "gex_history.db", "symbols": ["SPX"]}
    # A live pid: status must get past the process check to reach the heartbeat read.
    (tmp_path / "recorder.pid").write_text("12345", encoding="utf-8")
    monkeypatch.setattr(service, "_pid_alive", lambda pid: True)
    return c


def test_beat_writes_and_refreshes_the_heartbeat(cfg):
    service._beat(cfg)
    hb = service._recorder_heartbeat_path(cfg)
    assert hb.exists()
    first = hb.stat().st_mtime
    os.utime(hb, (first - 500, first - 500))
    service._beat(cfg)
    assert hb.stat().st_mtime > first - 500


def test_fresh_heartbeat_is_not_stalled(cfg):
    service._beat(cfg)
    status = service.recorder_status(cfg)
    assert status["running"] is True
    assert status["stalled"] is False
    assert status["heartbeat_age_seconds"] < service.RECORDER_STALL_SECONDS


def test_silent_heartbeat_is_stalled(cfg):
    service._beat(cfg)
    hb = service._recorder_heartbeat_path(cfg)
    old = time.time() - service.RECORDER_STALL_SECONDS - 60
    os.utime(hb, (old, old))
    status = service.recorder_status(cfg)
    assert status["running"] is True
    assert status["stalled"] is True


def test_missing_heartbeat_degrades_to_not_stalled(cfg):
    """A daemon that publishes no heartbeat (pre-heartbeat build, unwritable disk) is simply not
    silence-supervised — restarting on 'I can't tell' is the failure the convention fixes."""
    status = service.recorder_status(cfg)
    assert status["running"] is True
    assert status["stalled"] is False
    assert "heartbeat_age_seconds" not in status


def test_dead_process_reports_not_running_without_stall_key(cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_pid_alive", lambda pid: False)
    status = service.recorder_status(cfg)
    assert status == {"ok": True, "running": False, "pid": None}
