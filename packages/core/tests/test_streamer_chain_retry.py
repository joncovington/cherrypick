"""_fetch_dte0_chain_with_retry: a transient chain-fetch failure retries with backoff instead of
permanently disabling the symbol's window on the first error."""

import asyncio

from cherrypick.core import streamcache
from cherrypick.core.streamer import ChainStreamer, _State


async def _no_sleep(_seconds):
    """Stand-in for asyncio.sleep so retry-backoff tests run instantly."""
    return None


def _engine(tmp_path, symbols=("XSP",)):
    return ChainStreamer(
        session_factory=lambda: None,
        db_path=tmp_path / "cache.db",
        symbols=list(symbols),
    )


def _state(tmp_path, symbols=("XSP",)):
    conn = streamcache.connect(tmp_path / "cache.db")
    return _State(conn, list(symbols))


def test_retry_succeeds_after_transient_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    engine = _engine(tmp_path)
    state = _state(tmp_path)

    calls = {"n": 0}

    async def _fetch(_self, symbol):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("Couldn't parse response: <html>")
        return {"XSP_C600": object()}

    monkeypatch.setattr(ChainStreamer, "_fetch_dte0_chain", _fetch)

    chain = asyncio.run(engine._fetch_dte0_chain_with_retry("XSP", state))

    assert list(chain) == ["XSP_C600"]
    assert calls["n"] == 3
    row = state.conn.execute(
        "SELECT chain_loaded_at, chain_fetch_error FROM stream_symbol_health WHERE symbol = 'XSP'"
    ).fetchone()
    assert row["chain_fetch_error"] is None
    assert row["chain_loaded_at"] is not None


def test_retry_gives_up_after_max_attempts_and_records_the_error(tmp_path, monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    engine = _engine(tmp_path)
    state = _state(tmp_path)

    calls = {"n": 0}

    async def _always_fails(_self, symbol):
        calls["n"] += 1
        raise ValueError("Couldn't parse response: <html>")

    monkeypatch.setattr(ChainStreamer, "_fetch_dte0_chain", _always_fails)

    chain = asyncio.run(engine._fetch_dte0_chain_with_retry("XSP", state))

    assert chain is None
    assert calls["n"] == 6  # _CHAIN_FETCH_MAX_ATTEMPTS
    row = state.conn.execute(
        "SELECT chain_loaded_at, chain_fetch_error FROM stream_symbol_health WHERE symbol = 'XSP'"
    ).fetchone()
    assert row["chain_fetch_error"] == "Couldn't parse response: <html>"
    assert row["chain_loaded_at"] is None


def test_symbol_refresher_disables_window_when_retries_exhausted(tmp_path, monkeypatch):
    """Regression guard: a permanently-failing chain fetch must still leave the window disabled
    (existing behavior) rather than looping forever."""
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    engine = _engine(tmp_path)
    state = _state(tmp_path)

    async def _always_fails(_self, symbol):
        raise ValueError("boom")

    monkeypatch.setattr(ChainStreamer, "_fetch_dte0_chain", _always_fails)

    asyncio.run(engine._symbol_refresher(None, state, "XSP", None, None, None, None))

    assert "XSP" not in state.chains
    assert "XSP" not in state.window_syms
