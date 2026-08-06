"""Tests for the order-alert inbox and the daemon that fills it.

No broker, no websocket, no network: the daemon's only outward call is
`broker.wait_for_order_alerts`, which is injected as a fake here.
"""

from __future__ import annotations

import pytest

from cherrypick.flies import alert_daemon, alerts_db


@pytest.fixture()
def inbox(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    conn = alerts_db.connect(str(tmp_path / "live_alerts.db"))
    yield conn
    conn.close()


def _alert(order_id="ORD1", status="Filled", price="1.05", filled=True, cancellable=False):
    return {
        "order_id": order_id,
        "status": status,
        "price": price,
        "filled": filled,
        "cancellable": cancellable,
    }


# --------------------------------------------------------------------------- the inbox
def test_inbox_is_wal_mode(inbox):
    """WAL is the whole reason this is a separate file from live_trades.db -- 1 writer (the
    daemon) and N readers (tick, watcher) who never block each other."""
    assert inbox.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_record_and_read_round_trip(inbox):
    alerts_db.record_alert(inbox, _alert(), "2026-07-31T10:00:00-04:00")
    got = alerts_db.alerts_since(inbox, ["ORD1"], None)
    assert got == [
        {
            "order_id": "ORD1",
            "status": "Filled",
            "price": "1.05",
            "filled": True,
            "cancellable": False,
            "received_at": "2026-07-31T10:00:00-04:00",
        }
    ]


def test_alerts_since_is_exclusive_and_ordered(inbox):
    alerts_db.record_alert(inbox, _alert(status="Live", filled=False), "2026-07-31T10:00:00-04:00")
    alerts_db.record_alert(inbox, _alert(status="Filled"), "2026-07-31T10:00:05-04:00")
    # A checkpoint at the first row's timestamp must not re-deliver that row.
    got = alerts_db.alerts_since(inbox, ["ORD1"], "2026-07-31T10:00:00-04:00")
    assert [a["status"] for a in got] == ["Filled"]
    # No checkpoint (a freshly spawned watcher) sees everything, oldest first.
    assert [a["status"] for a in alerts_db.alerts_since(inbox, ["ORD1"], None)] == ["Live", "Filled"]


def test_alerts_since_ignores_other_orders(inbox):
    """The account isn't exclusive to this ledger -- an alert for someone else's manual trade
    must never surface as one of ours."""
    alerts_db.record_alert(inbox, _alert(order_id="ORD-MINE"), "2026-07-31T10:00:00-04:00")
    alerts_db.record_alert(inbox, _alert(order_id="ORD-THEIRS"), "2026-07-31T10:00:01-04:00")
    got = alerts_db.alerts_since(inbox, ["ORD-MINE"], None)
    assert [a["order_id"] for a in got] == ["ORD-MINE"]


def test_alerts_since_with_no_order_ids_is_empty(inbox):
    alerts_db.record_alert(inbox, _alert(), "2026-07-31T10:00:00-04:00")
    assert alerts_db.alerts_since(inbox, [], None) == []


def test_duplicate_redelivery_is_ignored_not_an_error(inbox):
    """A websocket redelivering the same alert must not error out the daemon's listen loop."""
    alerts_db.record_alert(inbox, _alert(), "2026-07-31T10:00:00-04:00")
    alerts_db.record_alert(inbox, _alert(), "2026-07-31T10:00:00-04:00")
    assert len(alerts_db.alerts_since(inbox, ["ORD1"], None)) == 1


def test_prune_drops_only_older_rows(inbox):
    alerts_db.record_alert(inbox, _alert(order_id="OLD"), "2026-07-30T10:00:00-04:00")
    alerts_db.record_alert(inbox, _alert(order_id="NEW"), "2026-07-31T10:00:00-04:00")
    removed = alerts_db.prune_before(inbox, "2026-07-31")
    assert removed == 1
    assert [a["order_id"] for a in alerts_db.alerts_since(inbox, ["OLD", "NEW"], None)] == ["NEW"]


# --------------------------------------------------------------------------- the daemon loop
class FakeAlertBroker:
    """Returns one canned batch per wait_for_order_alerts call, then empty batches forever."""

    def __init__(self, batches=None):
        self.calls = []
        self._batches = list(batches or [])

    def wait_for_order_alerts(self, order_ids, timeout_seconds):
        self.calls.append((set(order_ids), timeout_seconds))
        return self._batches.pop(0) if self._batches else []


def _cfg(**live):
    return {"live": {"arm": "gex", "symbol": "XSP", **live}}


def test_daemon_records_alerts_into_the_inbox(inbox):
    broker = FakeAlertBroker(batches=[[_alert(order_id="ORD-A")], [_alert(order_id="ORD-B")]])
    ticks = iter(range(0, 1000))
    out = alert_daemon.run_daemon(
        _cfg(),
        broker=broker,
        conn=inbox,
        listen_slice=1,
        max_seconds=3,
        clock_fn=lambda: next(ticks),
    )
    assert out["ok"] and out["alerts_seen"] == 2
    got = alerts_db.alerts_since(inbox, ["ORD-A", "ORD-B"], None)
    assert {a["order_id"] for a in got} == {"ORD-A", "ORD-B"}


def test_daemon_subscribes_to_the_account_not_specific_orders(inbox):
    """The daemon has no ledger view and shouldn't need one -- it records whatever the account
    reports and lets readers decide which order ids they care about."""
    broker = FakeAlertBroker()
    ticks = iter(range(0, 1000))
    alert_daemon.run_daemon(
        _cfg(), broker=broker, conn=inbox, listen_slice=1, max_seconds=2, clock_fn=lambda: next(ticks)
    )
    assert broker.calls and all(order_ids == set() for order_ids, _ in broker.calls)


def test_daemon_survives_an_inbox_write_failure(inbox, monkeypatch):
    """One bad row must not kill the stream -- the daemon keeps listening."""
    calls = {"n": 0}

    def flaky(conn, alert, received_at):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("disk hiccup")
        return None

    monkeypatch.setattr(alerts_db, "record_alert", flaky)
    broker = FakeAlertBroker(batches=[[_alert(order_id="ORD-A")], [_alert(order_id="ORD-B")]])
    ticks = iter(range(0, 1000))
    out = alert_daemon.run_daemon(
        _cfg(), broker=broker, conn=inbox, listen_slice=1, max_seconds=3, clock_fn=lambda: next(ticks)
    )
    # First write raised and was skipped; the second still counted -> the loop survived.
    assert out["alerts_seen"] == 1


def test_daemon_stops_at_the_disarm_deadline(inbox, monkeypatch):
    """A missed explicit stop must not leave an authenticated session connected overnight."""
    from cherrypick.flies import clock as clockmod

    # Pretend it's one minute before disarm: the deadline should bound the run to ~60s. A real
    # datetime, not a stub -- clock.now_iso() calls now_et() too (for the prune cutoff and each
    # alert's received_at), so anything thinner breaks on .isoformat().
    fixed = clockmod.now_et().replace(hour=16, minute=59, second=0, microsecond=0)
    monkeypatch.setattr(clockmod, "now_et", lambda: fixed)
    broker = FakeAlertBroker()
    ticks = iter(range(0, 1000))
    alert_daemon.run_daemon(
        _cfg(disarm_time="17:00"),
        broker=broker,
        conn=inbox,
        listen_slice=1,
        clock_fn=lambda: next(ticks),
    )
    # Bounded by the 60s disarm deadline (not by max_seconds, which isn't set here).
    assert len(broker.calls) <= 61


def test_status_reports_not_running_when_no_pid_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    out = alert_daemon.status()
    assert out["running"] is False and out["pid"] is None


def test_stop_is_a_no_op_when_not_running(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    out = alert_daemon.stop()
    assert out["ok"] is True and out["stopped"] is False
