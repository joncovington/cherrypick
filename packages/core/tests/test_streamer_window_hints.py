"""ChainStreamer's per-symbol window_strike_count_for hook: a widened hint must be picked up on the
next poll iteration even without a qualifying price move (see the `_symbol_refresher` recompute
condition -- `strike_count != prev_strike_count` is the second OR clause alongside the price-move
check)."""

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
        self.unsubscribed = []

    async def subscribe(self, event_type, symbols):
        self.subscribed.append((event_type, list(symbols)))

    async def unsubscribe(self, event_type, symbols):
        self.unsubscribed.append((event_type, list(symbols)))


def _chain(center=100, spread=80):
    return {f"S{i}": _Opt(f"S{i}", center + i) for i in range(-spread, spread + 1)}


def _engine_and_state(tmp_path, window_strike_count_for=None):
    engine = ChainStreamer(
        session_factory=lambda: None,
        db_path=tmp_path / "cache.db",
        symbols=["XSP"],
        window_strike_count=20,
        window_strike_count_for=window_strike_count_for,
    )
    conn = streamcache.connect(tmp_path / "cache.db")
    conn.execute(
        "INSERT INTO stream_trades(symbol, last, change, volume, updated_at) VALUES (?,?,?,?,?)",
        ("XSP", 100.0, 0.0, 0.0, time.time()),
    )
    conn.commit()
    state = _State(conn, ["XSP"])
    state.chains["XSP"] = _chain()
    return engine, state


async def _run_one_pass(engine, state, streamer, monkeypatch):
    """Drive _symbol_refresher's while-loop for exactly one iteration: bypass the network chain
    fetch (state.chains is already seeded) and stop on the first `asyncio.sleep` call."""

    async def fake_fetch(_self, _symbol, _state):
        return state.chains["XSP"]

    async def stop_after_one(_seconds):
        state.stop_event.set()

    monkeypatch.setattr(ChainStreamer, "_fetch_dte0_chain_with_retry", fake_fetch)
    monkeypatch.setattr(asyncio, "sleep", stop_after_one)
    await engine._symbol_refresher(streamer, state, "XSP", "Quote", "Greeks", "Summary", "Trade")


def test_default_strike_count_used_when_no_hook(tmp_path, monkeypatch):
    engine, state = _engine_and_state(tmp_path)
    asyncio.run(_run_one_pass(engine, state, _FakeStreamer(), monkeypatch))
    assert len(state.window_syms["XSP"]) == 2 * 20 + 1
    assert state.window_strike_counts["XSP"] == 20


def test_hook_widens_the_window(tmp_path, monkeypatch):
    engine, state = _engine_and_state(tmp_path, window_strike_count_for=lambda sym: 60)
    asyncio.run(_run_one_pass(engine, state, _FakeStreamer(), monkeypatch))
    assert len(state.window_syms["XSP"]) == 2 * 60 + 1
    assert state.window_strike_counts["XSP"] == 60


def test_widened_hint_is_picked_up_without_a_price_move(tmp_path, monkeypatch):
    hints = {"width": 20}
    engine, state = _engine_and_state(tmp_path, window_strike_count_for=lambda sym: hints["width"])

    asyncio.run(_run_one_pass(engine, state, _FakeStreamer(), monkeypatch))
    assert len(state.window_syms["XSP"]) == 2 * 20 + 1

    # No price change at all -- only the hint widens.
    hints["width"] = 40
    state.stop_event = asyncio.Event()
    asyncio.run(_run_one_pass(engine, state, _FakeStreamer(), monkeypatch))
    assert len(state.window_syms["XSP"]) == 2 * 40 + 1
    assert state.window_strike_counts["XSP"] == 40


def test_unchanged_hint_and_price_does_not_resubscribe(tmp_path, monkeypatch):
    engine, state = _engine_and_state(tmp_path, window_strike_count_for=lambda sym: 20)
    streamer = _FakeStreamer()
    asyncio.run(_run_one_pass(engine, state, streamer, monkeypatch))
    first_subscribe_count = len(streamer.subscribed)

    state.stop_event = asyncio.Event()
    asyncio.run(_run_one_pass(engine, state, streamer, monkeypatch))
    # Same price, same hint -> the recompute condition's third clause never fires, no new subscribe.
    assert len(streamer.subscribed) == first_subscribe_count
