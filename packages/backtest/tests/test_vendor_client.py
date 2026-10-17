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
