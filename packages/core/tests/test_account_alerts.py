"""cherrypick.core.live.account_alerts: the account's order events, stream first, REST behind it
(2026-11-02). Offline; the broker is a fake."""

from __future__ import annotations

import json

import pytest

from cherrypick.core.live import account_alerts as aa


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path / "home"))


class _Broker:
    """Each listen delivers the next pushed batch and advances the clock a slice; each REST read
    returns the next batch (or raises it)."""

    def __init__(self, pushed, rest, clock):
        self.pushed, self.rest, self.clock = list(pushed), list(rest), clock

    def wait_for_order_alerts(self, order_ids, timeout):
        self.clock["t"] += timeout
        return self.pushed.pop(0) if self.pushed else []

    def orders_today(self):
        batch = self.rest.pop(0) if self.rest else []
        if isinstance(batch, Exception):
            raise batch
        return batch


def _run(tmp_path, pushed, rest, slices, sleeps=None):
    clock = {"t": 0.0}
    stamps = iter(f"2026-11-02T10:{i:02d}:00-05:00" for i in range(60))
    conn = aa.connect(str(tmp_path / "inbox.db"))
    out = aa.run(
        _Broker(pushed, rest, clock),
        conn=conn,
        now_iso=lambda: next(stamps),
        clock=lambda: clock["t"],
        sleep=(sleeps.append if sleeps is not None else lambda s: None),
        listen_slice=30.0,
        max_seconds=30.0 * slices,
        log=lambda *_: None,
    )
    return out, conn


def _events(tmp_path):
    return aa.events_after(str(tmp_path / "inbox.db"), None)


def test_each_order_status_is_recorded_once_from_whichever_source_saw_it_first(tmp_path):
    out, _ = _run(
        tmp_path,
        pushed=[[{"order_id": 1, "status": "Live", "price": "-1.40"}], [{"order_id": 1, "status": "Filled"}]],
        rest=[
            [
                {"order_id": 1, "status": "Live"},
                {"order_id": 2, "status": "Received", "external_identifier": None},
            ]
        ],
        slices=2,
    )
    rows = [(e["order_id"], e["status"], e["source"]) for e in _events(tmp_path)]
    assert rows == [("1", "live", "stream"), ("2", "received", "rest"), ("1", "filled", "stream")]
    assert out["events"] == 3 and out["stream_missed_fills"] == []


def test_a_fill_only_rest_saw_is_counted_as_missed_by_the_stream(tmp_path):
    """Shown to fail without the REST read: order 7's fill never reaches the inbox at all."""
    out, _ = _run(
        tmp_path, pushed=[[], []], rest=[[{"order_id": 7, "status": "Filled", "price": "2.95"}], []], slices=2
    )
    assert [(e["order_id"], e["status"], e["source"]) for e in _events(tmp_path)] == [("7", "filled", "rest")]
    assert out["stream_missed_fills"] == ["7"]


def test_a_fill_the_next_slice_pushes_is_not_a_miss(tmp_path):
    # REST read it a moment after the slice ended; the stream delivered it in the next slice.
    out, _ = _run(
        tmp_path,
        pushed=[[], [{"order_id": 8, "status": "Filled"}]],
        rest=[[{"order_id": 8, "status": "Filled"}], []],
        slices=2,
    )
    assert out["stream_missed_fills"] == []


def test_a_failing_rest_read_is_counted_and_the_service_keeps_listening(tmp_path):
    out, _ = _run(
        tmp_path,
        pushed=[[], [{"order_id": 3, "status": "Live"}]],
        rest=[RuntimeError("502"), []],
        slices=2,
    )
    assert out["rest_failures"] == 1 and out["last_rest_ok_at"] is not None
    assert [e["order_id"] for e in _events(tmp_path)] == ["3"]
    status = aa.read_status()
    assert status["day"] == "2026-11-02" and status["rest_failures"] == 1


def test_a_stream_that_fails_at_once_pauses_instead_of_spinning(tmp_path):
    clock = {"t": 0.0}
    sleeps = []

    class Dead(_Broker):
        def wait_for_order_alerts(self, order_ids, timeout):
            return []  # failed closed, instantly

        def orders_today(self):
            clock["t"] += 10.0
            return []

    aa.run(
        Dead([], [], clock),
        conn=aa.connect(str(tmp_path / "inbox.db")),
        clock=lambda: clock["t"],
        sleep=sleeps.append,
        max_seconds=25.0,
        log=lambda *_: None,
    )
    assert sleeps and all(s == aa.FAILED_SLICE_PAUSE_SECONDS for s in sleeps)


def test_events_page_by_sequence_so_one_second_never_hides_an_event(tmp_path):
    conn = aa.connect(str(tmp_path / "inbox.db"))
    aa.record(conn, {"order_id": 1, "status": "Live"}, "stream", "2026-11-02T10:00:00")
    first = aa.events_after(str(tmp_path / "inbox.db"), None)
    aa.record(conn, {"order_id": 1, "status": "Filled"}, "stream", "2026-11-02T10:00:00")  # same second
    later = aa.events_after(str(tmp_path / "inbox.db"), first[-1]["seq"])
    assert [e["status"] for e in later] == ["filled"]


def test_the_service_never_has_a_live_gate_open():
    broker = aa.make_broker("cherrypick-test-service")
    assert broker._live_gates() == ["account-alerts is read-only"]


def test_status_prints_the_status_file(capsys):
    aa._write_status({"day": "2026-11-02", "events": 4})
    assert aa.main(["--status"]) == 0
    assert json.loads(capsys.readouterr().out)["events"] == 4
