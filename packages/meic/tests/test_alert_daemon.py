"""MEIC's order-alert daemon (2026-10-15): when it starts a confirming tick, and its arm companion."""

from __future__ import annotations

from cherrypick.core import live as _live
from test_live_scaffold import _config

from cherrypick.meic import alert_daemon, live_loop


def _fire(alert, *, known=("ORD1",), clock=100.0):
    spawned = []
    did = alert_daemon.on_alert(
        alert, known=lambda: list(known), spawn=lambda: spawned.append(1), clock_fn=lambda: clock
    )
    return did, spawned


def setup_function(_fn):
    alert_daemon._last_spawn["at"] = None


def test_a_fill_on_a_meic_order_starts_a_confirming_tick():
    did, spawned = _fire({"order_id": "ORD1", "status": "Filled"})
    assert did and spawned == [1]


def test_a_working_order_or_another_loops_order_starts_nothing():
    assert _fire({"order_id": "ORD1", "status": "Live"}) == (False, [])
    assert _fire({"order_id": "FLY9", "status": "Filled"}) == (False, [])


def test_ticks_are_spaced():
    assert _fire({"order_id": "ORD1", "status": "Filled"}, clock=100.0)[0]
    assert not _fire({"order_id": "ORD1", "status": "Cancelled"}, clock=105.0)[0]
    assert _fire({"order_id": "ORD1", "status": "Cancelled"}, clock=111.0)[0]


def test_the_companion_is_named_only_when_switched_on():
    assert live_loop.alert_daemon_companion(_config()) is None
    comp = live_loop.alert_daemon_companion(_config(use_order_alert_daemon=True))
    assert comp["argv"] == ["-m", "cherrypick.meic.alert_daemon"] and comp["until"] == "17:00"


def test_arming_puts_the_companion_on_the_record(monkeypatch):
    monkeypatch.setattr(live_loop, "_spawn_first_tick", lambda: None)
    monkeypatch.setattr(_live, "supervisor_heartbeat_fresh", lambda *a, **k: True)
    out = live_loop.install_task(_config(use_order_alert_daemon=True))
    assert out["ok"] and out["companions"] == ["meic-alert-daemon"]
    live_loop.uninstall_task()
