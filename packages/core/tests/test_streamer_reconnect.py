"""A reconnected socket must be re-subscribed from scratch.

`_State` outlives the DXLink connection (it holds the cache handle and the chains), but four of its
fields describe one SOCKET's subscriptions. Both subscribe paths work by diffing against them, so
carrying them across a reconnect made every delta empty and left the new connection subscribed to
nothing — a producer that reported "connected", never re-established the underlying Trade feed, and
sat with a frozen spot until the watchdog recycled it ~240s later. Observed in production on
2026-08-20 (two recycles) and on the three days before it.
"""

import asyncio
import time

from cherrypick.core import streamcache
from cherrypick.core.streamer import ChainStreamer, _State


class _Opt:
    def __init__(self, sym, strike):
        self.streamer_symbol = sym
        self.strike_price = strike


class _FakeStreamer:
    def __init__(self):
        self.subscribed = []

    async def subscribe(self, event_type, symbols):
        self.subscribed.append((getattr(event_type, "__name__", str(event_type)), list(symbols)))

    async def unsubscribe(self, event_type, symbols):
        pass

    def names_for(self, event_name):
        out = []
        for name, syms in self.subscribed:
            if name == event_name:
                out.extend(syms)
        return out


class Trade:
    pass


class Quote:
    pass


class Greeks:
    pass


class Summary:
    pass


def _engine_and_state(tmp_path):
    engine = ChainStreamer(
        session_factory=lambda: None,
        db_path=tmp_path / "cache.db",
        symbols=["SPX", "SPY"],
        window_strike_count=20,
    )
    conn = streamcache.connect(tmp_path / "cache.db")
    for sym, px in (("SPX", 7680.0), ("SPY", 766.0)):
        conn.execute(
            "INSERT INTO stream_trades(symbol, last, change, volume, updated_at) VALUES (?,?,?,?,?)",
            (sym, px, 0.0, 0.0, time.time()),
        )
    conn.commit()
    state = _State(conn, ["SPX", "SPY"])
    state.chains["SPX"] = {f"S{i}": _Opt(f"S{i}", 7680 + i) for i in range(-40, 41)}
    return engine, state


def test_a_reconnect_resubscribes_the_underlying_feeds(tmp_path):
    engine, state = _engine_and_state(tmp_path)

    first = _FakeStreamer()
    asyncio.run(
        engine._apply_subscriptions(first, state, engine._subscriptions(), Trade, Quote, Greeks, Summary)
    )
    assert sorted(first.names_for("Trade")) == ["SPX", "SPY"]

    # The socket dies and a new one replaces it. It carries no subscriptions of its own.
    state.reconnect_count += 1
    state.reset_for_new_connection()

    second = _FakeStreamer()
    asyncio.run(
        engine._apply_subscriptions(second, state, engine._subscriptions(), Trade, Quote, Greeks, Summary)
    )

    assert sorted(second.names_for("Trade")) == ["SPX", "SPY"], (
        "the new socket must be told about the underlyings again — without this the producer "
        "reports connected while spot never ticks"
    )
    assert sorted(second.names_for("Summary")) == ["SPX", "SPY"]


def test_without_the_reset_the_new_socket_gets_nothing(tmp_path):
    """Pins the mechanism itself, so a future refactor cannot quietly reintroduce it."""
    engine, state = _engine_and_state(tmp_path)

    first = _FakeStreamer()
    asyncio.run(
        engine._apply_subscriptions(first, state, engine._subscriptions(), Trade, Quote, Greeks, Summary)
    )

    second = _FakeStreamer()  # reconnect WITHOUT the reset
    asyncio.run(
        engine._apply_subscriptions(second, state, engine._subscriptions(), Trade, Quote, Greeks, Summary)
    )

    assert second.names_for("Trade") == [], "diffing against stale state is what produced the bug"


def test_run_stream_resets_before_it_subscribes(tmp_path, monkeypatch):
    """Covers the CALL SITE, not just the method.

    Driving `_run_stream` with a fake socket is worth the setup: a test that only exercises
    `reset_for_new_connection` still passes when the one line invoking it is deleted, which is
    exactly the regression that matters.
    """
    import tastytrade
    import tastytrade.dxfeed as dxfeed

    engine, state = _engine_and_state(tmp_path)

    # Stand in for a previous connection's subscriptions.
    state.subscribed = {"Trade": ["SPX", "SPY"], "Quote": [], "Greeks": [], "Summary": ["SPX", "SPY"]}
    state.window_syms["SPX"] = ["S1", "S2"]
    state.centers["SPX"] = 7680.0
    state.reconnect_count = 1

    socket = _FakeStreamer()

    async def _empty(_event_type):
        return
        yield  # pragma: no cover — makes this an async generator

    socket.listen = _empty

    class _FakeDXLink:
        def __init__(self, _session):
            pass

        async def __aenter__(self):
            return socket

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr(tastytrade, "DXLinkStreamer", _FakeDXLink)
    for name in ("Candle", "Greeks", "Quote", "Summary", "Trade"):
        monkeypatch.setattr(dxfeed, name, type(name, (), {}), raising=False)

    state.stop_event.set()  # every task exits on its first check; _watch_stop unwinds the group
    try:
        asyncio.run(engine._run_stream(state))
    except (asyncio.CancelledError, BaseExceptionGroup):
        pass

    assert sorted(socket.names_for("Trade")) == ["SPX", "SPY"], (
        "_run_stream must forget the dead socket's subscriptions before it diffs against them"
    )


def test_the_reset_clears_the_window_so_it_re_centres(tmp_path):
    """The window path skips entirely while `centers` says the price has not moved, and subscribes
    only a delta against `window_syms` when it does run. Both must be forgotten with the socket."""
    _engine, state = _engine_and_state(tmp_path)
    state.window_syms["SPX"] = ["S1", "S2", "S3"]
    state.centers["SPX"] = 7680.0
    state.window_strike_counts["SPX"] = 20

    state.reset_for_new_connection()

    assert state.window_syms == {}
    assert state.centers == {}
    assert state.window_strike_counts == {}
    assert state.chains, "chains are data, not subscription state — they must survive"
