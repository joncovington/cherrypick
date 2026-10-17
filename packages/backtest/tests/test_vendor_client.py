"""The vendor client's own rules, without the vendor: every call carries a deadline, refusals map to
stable reasons, and only genuinely transient pushback is retried."""

from __future__ import annotations

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
