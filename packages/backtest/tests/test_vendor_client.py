"""The vendor client's own rules, without the vendor: every call carries a deadline, refusals map to
stable reasons, and only genuinely transient pushback is retried."""

from __future__ import annotations

import os
import sys
import types

import pytest

from cherrypick.backtest.vendor import VendorRefusal
from cherrypick.backtest.vendor import thetadata as td


class _Stub:
    def __init__(self):
        self.calls = []

    def GetSomething(self, request, **kwargs):  # noqa: N802 -- gRPC's naming
        self.calls.append(kwargs)
        return "reply"

    channel = "not callable"


def test_every_stub_call_gets_a_deadline_unless_it_brings_its_own():
    stub = _Stub()
    wrapped = td._DeadlineStub(stub, 300.0)
    assert wrapped.GetSomething("req") == "reply"
    assert wrapped.GetSomething("req", timeout=5) == "reply"
    assert stub.calls == [{"timeout": 300.0}, {"timeout": 5}]
    assert wrapped.channel == "not callable"


def test_refusals_map_to_stable_reasons():
    def reason(text, cls=Exception):
        return td._classify(cls(text))[0]

    assert reason("StatusCode.UNAUTHENTICATED") == "unauthenticated"
    assert (
        reason('details = "Requesting an option endpoint requiring a professional subscription"')
        == "permission_denied"
    )
    assert reason("Stream removed (End of TCP stream)") == "stream_removed"
    assert reason("something else") == "vendor_error"


def test_only_transient_pushback_is_retried():
    assert td._transient(Exception("StatusCode.UNAVAILABLE: connection reset"))
    assert td._transient(Exception("StatusCode.DEADLINE_EXCEEDED"))
    assert not td._transient(Exception("StatusCode.UNAVAILABLE: Stream removed (End of TCP stream)"))
    assert not td._transient(Exception("StatusCode.UNAUTHENTICATED"))


def test_a_second_client_is_refused_while_another_process_holds_the_session(tmp_path, monkeypatch):
    other = os.getpid() + 1
    lock = tmp_path / "thetadata-session.lock"
    lock.write_text(str(other), encoding="utf-8")
    monkeypatch.setattr(td.looplock, "pid_alive", lambda pid: pid == other)
    monkeypatch.setattr(td.looplock, "pid_reused", lambda pid, mtime: False)
    with pytest.raises(VendorRefusal) as refused:
        td.take_session(lock, wait_seconds=0)
    assert refused.value.reason == "session_in_use" and str(other) in refused.value.detail
    td.release_session(lock)  # never releases another process's lock
    assert lock.read_text(encoding="utf-8") == str(other)


class _Clock:
    """time.monotonic and time.sleep for the wait: sleeping moves the clock, nothing really sleeps."""

    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def monotonic(self):
        return self.now

    def sleep(self, s):
        assert s > 0, "a wait that sleeps zero is a busy loop"
        assert len(self.sleeps) < 1000, "the wait never ended"
        self.sleeps.append(s)
        self.now += s


def test_a_client_waits_for_the_holder_to_finish_asking_again_every_poll(tmp_path, monkeypatch):
    other = os.getpid() + 1
    lock = tmp_path / "thetadata-session.lock"
    lock.write_text(str(other), encoding="utf-8")
    clock = _Clock()
    monkeypatch.setattr(td.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(td.time, "sleep", clock.sleep)
    monkeypatch.setattr(td.looplock, "pid_reused", lambda pid, mtime: False)
    # The holder finishes after two polls (three minutes in).
    monkeypatch.setattr(td.looplock, "pid_alive", lambda pid: pid == other and clock.now < 150)
    lines = []
    assert td.take_session(lock, poll_seconds=60, log=lines.append) == lock
    assert clock.sleeps == [60, 60, 60] and len(lines) == 3 and str(other) in lines[0]
    assert lock.read_text(encoding="utf-8") == str(os.getpid())
    td.release_session(lock)


def test_the_wait_ends_with_a_refusal_when_the_holder_never_finishes(tmp_path, monkeypatch):
    other = os.getpid() + 1
    lock = tmp_path / "thetadata-session.lock"
    lock.write_text(str(other), encoding="utf-8")
    clock = _Clock()
    monkeypatch.setattr(td.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(td.time, "sleep", clock.sleep)
    monkeypatch.setattr(td.looplock, "pid_alive", lambda pid: pid == other)
    monkeypatch.setattr(td.looplock, "pid_reused", lambda pid, mtime: False)
    with pytest.raises(VendorRefusal) as refused:
        td.take_session(lock, wait_seconds=600, poll_seconds=60)
    assert refused.value.reason == "session_in_use"
    assert sum(clock.sleeps) == 600  # waited exactly the cap, never past it
    assert lock.read_text(encoding="utf-8") == str(other)  # and never stole it


def test_a_dead_holders_session_is_reclaimed_and_released(tmp_path, monkeypatch):
    lock = tmp_path / "thetadata-session.lock"
    lock.write_text("999999", encoding="utf-8")
    monkeypatch.setattr(td.looplock, "pid_alive", lambda pid: False)
    assert td.take_session(lock) == lock and lock.read_text(encoding="utf-8") == str(os.getpid())
    assert td.take_session(lock) == lock  # the holder may take it again
    td.release_session(lock)
    assert not lock.exists()


def test_the_lock_lives_in_the_state_folder_never_the_licensed_one():
    """Purge removes the licensed directory; it must never remove a live session's lock."""
    from cherrypick.core import home

    assert td.session_lock_path().parent == home.state_dir()


def test_the_lock_is_taken_before_the_login(monkeypatch):
    """A refused second client must never have logged in: the login is what ends the first."""
    logins = []

    class Lib:
        def __init__(self, **kw):
            logins.append(kw)
            self.stub = _Stub()

    monkeypatch.setitem(sys.modules, "thetadata", types.SimpleNamespace(ThetaClient=Lib))
    monkeypatch.setattr(td.credential, "read_key", lambda s: "k")

    def refuse(path=None, **_kw):
        raise VendorRefusal("session_in_use", "held")

    monkeypatch.setattr(td, "take_session", refuse)
    with pytest.raises(VendorRefusal):
        td.ThetaDataClient({"vendor": {"transport": "python"}})
    assert logins == []


class _FakeLibClient:
    """Stands in for the library's client: the first one's call stalls, a reconnected one answers."""

    made: list = []

    def __init__(self, existing_authorized_client=None, dataframe_type=None, stalls=False):
        self.stub = _Stub()
        self.stalls = stalls
        self.reused = existing_authorized_client
        _FakeLibClient.made.append(self)

    def option_list_expirations(self, **kw):
        if self.stalls:
            raise Exception("StatusCode.DEADLINE_EXCEEDED")
        return "answer"


def test_a_stalled_call_reconnects_on_the_same_session_and_is_retried(monkeypatch):
    monkeypatch.setattr(td.time, "sleep", lambda _s: None)
    _FakeLibClient.made = []
    client = td.ThetaDataClient.__new__(td.ThetaDataClient)
    client._client_class = _FakeLibClient
    client._client = _FakeLibClient(stalls=True)
    lines = []
    client.on_retry = lines.append
    assert client._call("option_list_expirations", symbol="SPXW") == "answer"
    first, fresh = _FakeLibClient.made
    assert fresh.reused is first, "a reconnect reuses the session: no second login"
    assert isinstance(fresh.stub, td._DeadlineStub), "and keeps the deadline"
    assert lines == ["retry option_list_expirations in 2s (stalled; reconnecting)"]


def test_a_daily_path_is_asked_for_in_chunks_and_dated_by_the_report_not_the_last_trade(monkeypatch):
    """A whole chain over two months is dropped by the vendor at 60 s, so the range goes in
    week-sized chunks; a chunk before the expiry was listed holds nothing and is skipped."""
    from datetime import date, datetime

    import pandas as pd

    asked = []

    def call(method, **kw):
        asked.append((kw["start_date"], kw["end_date"]))
        if kw["start_date"] == date(2024, 1, 1):
            raise VendorRefusal("no_data", "No data found")
        day = datetime.combine(kw["end_date"], datetime.min.time())
        return pd.DataFrame(
            [{"created": day, "last_trade": datetime(2023, 12, 1), "strike": 100.0, "right": "put",
              "bid": 1.0, "ask": 1.2, "close": float("nan"), "volume": 0, "bid_size": 1, "ask_size": 1}]
        )  # fmt: skip

    client = td.ThetaDataClient.__new__(td.ThetaDataClient)
    monkeypatch.setattr(client, "_call", call, raising=False)
    rows = client.eod_chain_range("SPY", "2024-01-01", "2024-01-20", "2024-02-16")
    assert asked == [(date(2024, 1, 1), date(2024, 1, 7)), (date(2024, 1, 8), date(2024, 1, 14)),
                     (date(2024, 1, 15), date(2024, 1, 20))]  # fmt: skip
    assert [r["session"] for r in rows] == ["2024-01-14", "2024-01-20"]
    assert rows[0]["right"] == "PUT" and rows[0]["close"] is None


def test_a_daily_path_with_nothing_anywhere_is_the_vendors_no_data(monkeypatch):
    def call(method, **kw):
        raise VendorRefusal("no_data", "No data found")

    client = td.ThetaDataClient.__new__(td.ThetaDataClient)
    monkeypatch.setattr(client, "_call", call, raising=False)
    with pytest.raises(VendorRefusal) as refused:
        client.eod_chain_range("SPY", "2024-01-01", "2024-01-20", "2024-02-16")
    assert refused.value.reason == "no_data"
