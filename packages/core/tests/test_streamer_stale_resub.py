"""A subscription that dies WITHOUT a reconnect must be resubscribed by the producer itself.

`_apply_subscriptions` diffs wanted against `state.subscribed` — the producer's own memory of what
it asked for — so a server-side drop leaves the memory intact and the delta empty forever. The
reconnect fix (`reset_for_new_connection`) only clears that memory on a NEW connection. Observed in
production 2026-08-17..21: TQQQ's trade subscription died mid-flight and streamed nothing for four
sessions while SPX ticked happily beside it (its window re-centering re-touches subscriptions
constantly — the accidental self-heal the other symbols never had). `_reheal_stale_trades` is the
deliberate version: any Trade symbol whose CACHE row goes stale during regular hours is
resubscribed, because the cache is what consumers actually see.
"""

import asyncio
import time

from cherrypick.core import streamcache
from cherrypick.core import streamer as streamer_mod
from cherrypick.core.streamer import ChainStreamer, _State


class _FakeStreamer:
    def __init__(self):
        self.subscribed = []

    async def subscribe(self, event_type, symbols):
        self.subscribed.append((getattr(event_type, "__name__", str(event_type)), list(symbols)))

    async def unsubscribe(self, event_type, symbols):
        pass


class Trade:
    pass


def _engine_and_state(tmp_path, ages):
    engine = ChainStreamer(
        session_factory=lambda: None,
        db_path=tmp_path / "cache.db",
        symbols=list(ages),
        window_strike_count=20,
    )
    conn = streamcache.connect(tmp_path / "cache.db")
    now = time.time()
    for sym, age in ages.items():
        conn.execute(
            "INSERT INTO stream_trades(symbol, last, change, volume, updated_at) VALUES (?,?,?,?,?)",
            (sym, 100.0, 0.0, 0.0, now - age),
        )
    conn.commit()
    state = _State(conn, list(ages))
    state.subscribed["Trade"] = list(ages)
    return engine, state


def test_stale_symbol_is_resubscribed_fresh_one_is_not(tmp_path, monkeypatch):
    monkeypatch.setattr(streamer_mod, "_resub_clock_gate", lambda: True)
    engine, state = _engine_and_state(tmp_path, {"SPX": 5.0, "TQQQ": 3600.0})

    fake = _FakeStreamer()
    asyncio.run(engine._reheal_stale_trades(fake, state, Trade))
    assert fake.subscribed == [("Trade", ["TQQQ"])]


def test_resub_is_rate_limited_per_symbol(tmp_path, monkeypatch):
    # A symbol the feed has nothing for must not be hammered every poll: one attempt per
    # _STALE_TRADE_RESUB_MIN_GAP_S, so a wedged symbol costs a log line every ten minutes.
    monkeypatch.setattr(streamer_mod, "_resub_clock_gate", lambda: True)
    engine, state = _engine_and_state(tmp_path, {"TQQQ": 3600.0})

    fake = _FakeStreamer()
    asyncio.run(engine._reheal_stale_trades(fake, state, Trade))
    asyncio.run(engine._reheal_stale_trades(fake, state, Trade))
    assert fake.subscribed == [("Trade", ["TQQQ"])]


def test_no_resub_outside_regular_hours(tmp_path, monkeypatch):
    # Overnight quiet is legitimate — the SPX index is frozen outside RTH by design.
    monkeypatch.setattr(streamer_mod, "_resub_clock_gate", lambda: False)
    engine, state = _engine_and_state(tmp_path, {"SPX": 7200.0})

    fake = _FakeStreamer()
    asyncio.run(engine._reheal_stale_trades(fake, state, Trade))
    assert fake.subscribed == []


def test_symbol_with_no_row_yet_is_treated_as_stale(tmp_path, monkeypatch):
    # Subscribed but never wrote: from the consumers' side that IS a dead feed, and a resubscribe
    # is idempotent, so heal it the same way.
    monkeypatch.setattr(streamer_mod, "_resub_clock_gate", lambda: True)
    engine, state = _engine_and_state(tmp_path, {"SPX": 5.0})
    state.subscribed["Trade"] = ["SPX", "UPRO"]

    fake = _FakeStreamer()
    asyncio.run(engine._reheal_stale_trades(fake, state, Trade))
    assert fake.subscribed == [("Trade", ["UPRO"])]
