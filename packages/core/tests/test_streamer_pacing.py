"""The subscription pacing choke point (`_send_subs`).

The 2026-08-24 incident: the producer held ~12,000 subscriptions, re-sent all of them on every
reconnect as fast as the socket would accept, and DXLink killed the socket ~5s later with "Your
subscription rate is too high" — 79 reconnects in a morning, every trading module starved. A burst
that trips the limit cannot recover by retrying the identical burst, so the loop was unrecoverable
by construction.

Two properties are load-bearing and each was verified by breaking it:

* large lists are CHUNKED, so one message never carries the whole book; and
* messages are paced against a GLOBAL clock, not a per-call one — the window paths subscribe the
  same add-list to four event types back to back, and per-call pacing would let exactly those four
  stack into the burst this exists to prevent.
"""

from __future__ import annotations

import asyncio

import pytest

from cherrypick.core.streamer import ChainStreamer


class _FakeStreamer:
    """Records every subscription message and when it was sent."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, int]] = []  # (op, event, count)
        self.times: list[float] = []

    async def subscribe(self, cls, symbols):
        self.sent.append(("sub", getattr(cls, "__name__", str(cls)), len(symbols)))
        self.times.append(asyncio.get_running_loop().time())

    async def unsubscribe(self, cls, symbols):
        self.sent.append(("unsub", getattr(cls, "__name__", str(cls)), len(symbols)))
        self.times.append(asyncio.get_running_loop().time())


class Quote:  # stand-ins for the SDK event classes
    pass


class Greeks:
    pass


def _engine(**kw):
    return ChainStreamer(
        session_factory=lambda: None, db_path=":memory:", symbols=["SPX"], **kw
    )


def test_large_subscription_is_chunked_never_one_message():
    eng = _engine(subscribe_chunk=200, subscribe_pace_s=0.0)
    fake = _FakeStreamer()
    syms = [f"S{i}" for i in range(1000)]
    asyncio.run(eng._send_subs(fake, Quote, syms))
    assert [c for _, _, c in fake.sent] == [200] * 5
    assert sum(c for _, _, c in fake.sent) == 1000  # nothing dropped


def test_empty_list_sends_nothing():
    eng = _engine(subscribe_pace_s=0.0)
    fake = _FakeStreamer()
    asyncio.run(eng._send_subs(fake, Quote, []))
    assert fake.sent == []


def test_unsubscribe_is_paced_too():
    eng = _engine(subscribe_chunk=100, subscribe_pace_s=0.0)
    fake = _FakeStreamer()
    asyncio.run(eng._send_subs(fake, Quote, [f"S{i}" for i in range(250)], remove=True))
    assert [op for op, _, _ in fake.sent] == ["unsub"] * 3


def test_pacing_is_global_across_calls_not_per_call():
    """THE guard. Four event types subscribed back to back must not stack: the pace applies
    between messages whichever call emitted them."""
    pace = 0.05
    eng = _engine(subscribe_chunk=1000, subscribe_pace_s=pace)
    fake = _FakeStreamer()

    async def scenario():
        # One add-list, four event types — the window path's exact shape.
        for cls in (Quote, Greeks, Quote, Greeks):
            await eng._send_subs(fake, cls, ["A", "B", "C"])

    asyncio.run(scenario())
    assert len(fake.times) == 4
    gaps = [b - a for a, b in zip(fake.times, fake.times[1:], strict=False)]
    assert all(g >= pace * 0.9 for g in gaps), f"messages stacked: {gaps}"


def test_pacing_applies_between_chunks_of_one_call():
    pace = 0.05
    eng = _engine(subscribe_chunk=2, subscribe_pace_s=pace)
    fake = _FakeStreamer()
    asyncio.run(eng._send_subs(fake, Quote, ["A", "B", "C", "D", "E"]))
    assert len(fake.times) == 3
    gaps = [b - a for a, b in zip(fake.times, fake.times[1:], strict=False)]
    assert all(g >= pace * 0.9 for g in gaps), f"chunks stacked: {gaps}"


def test_zero_pace_is_allowed_and_still_chunks():
    """Pacing off is a valid configuration (tests, a consumer with a tiny book) and must not
    silently disable chunking with it."""
    eng = _engine(subscribe_chunk=10, subscribe_pace_s=0.0)
    fake = _FakeStreamer()
    asyncio.run(eng._send_subs(fake, Quote, [f"S{i}" for i in range(35)]))
    assert [c for _, _, c in fake.sent] == [10, 10, 10, 5]


@pytest.mark.parametrize("chunk", [0, -5])
def test_chunk_size_is_floored_at_one(chunk):
    """A zero or negative chunk would divide the book into infinite empty slices."""
    assert _engine(subscribe_chunk=chunk).subscribe_chunk == 1
