"""cherrypick.core.live.alerts: the order-alert inbox and the daemon loop (2026-10-15)."""

from __future__ import annotations

from cherrypick.core.live import alerts


class _Stream:
    """Delivers each batch on one listen call, then nothing."""

    def __init__(self, *batches):
        self.batches = list(batches)

    def wait_for_order_alerts(self, order_ids, timeout):
        return self.batches.pop(0) if self.batches else []


def _clock():
    t = {"now": 0.0}

    def tick():
        t["now"] += 10.0
        return t["now"]

    return tick


def _daemon(tmp_path):
    return alerts.AlertDaemon("meic", str(tmp_path / "data"), str(tmp_path / "logs" / "d.log"))


def test_the_daemon_records_every_alert_and_tells_its_listener(tmp_path):
    seen = []
    d = _daemon(tmp_path)
    stream = _Stream(
        [{"order_id": 1, "status": "Live"}], [{"order_id": 1, "status": "Filled", "price": "0.31"}]
    )
    stamps = iter(f"2026-10-15T12:3{i}:00" for i in range(10))
    out = d.run(
        stream,
        deadline_seconds=None,
        max_seconds=40,
        on_alert=seen.append,
        now_iso=lambda: next(stamps),
        clock_fn=_clock(),
    )
    assert out["alerts_seen"] == 2 and [a["status"] for a in seen] == ["Live", "Filled"]
    fills = alerts.fills_after(d.inbox_path, None)
    assert [(f["order_id"], f["price"]) for f in fills] == [("1", "0.31")]
    assert d.status()["alerts_seen"] == 2


def test_a_failing_listener_never_stops_the_stream(tmp_path):
    d = _daemon(tmp_path)

    def boom(_alert):
        raise RuntimeError("listener broke")

    stream = _Stream([{"order_id": 1, "status": "Filled"}], [{"order_id": 2, "status": "Filled"}])
    stamps = iter(f"2026-10-15T12:4{i}:00" for i in range(10))
    out = d.run(
        stream,
        deadline_seconds=None,
        max_seconds=40,
        on_alert=boom,
        now_iso=lambda: next(stamps),
        clock_fn=_clock(),
    )
    assert out["alerts_seen"] == 2


def test_fills_after_reads_only_fills_after_the_checkpoint(tmp_path):
    path = str(tmp_path / "inbox.db")
    conn = alerts.connect(path)
    alerts.record_alert(conn, {"order_id": 1, "status": "Filled"}, "2026-10-15T12:00:00")
    alerts.record_alert(conn, {"order_id": 2, "status": "Cancelled"}, "2026-10-15T12:01:00")
    alerts.record_alert(conn, {"order_id": 3, "status": "Filled"}, "2026-10-15T12:02:00")
    conn.close()
    assert [r["order_id"] for r in alerts.fills_after(path, None)] == ["1", "3"]
    assert [r["order_id"] for r in alerts.fills_after(path, "2026-10-15T12:00:00")] == ["3"]
    assert alerts.fills_after(str(tmp_path / "absent.db"), None) == []
