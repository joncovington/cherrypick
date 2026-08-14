"""ChainStreamer's expirations_for hook: extra requested expirations get their own chain rows,
health rows and ATM windows beside the nearest-expiration window — re-read every pass (growth needs
no restart), skipped when they duplicate the nearest window, and retired once they roll past or
leave the request. `expirations_for=None` must be byte-identical to the historical behavior."""

import asyncio
import time

from cherrypick.core import streamcache
from cherrypick.core.streamer import ChainStreamer, _State


class _Opt:
    def __init__(self, sym, strike, expiration, underlying="XSP"):
        self.streamer_symbol = sym
        self.strike_price = strike
        self._expiration = expiration
        self._underlying = underlying

    def model_dump(self, mode="json"):
        return {
            "streamer_symbol": self.streamer_symbol,
            "strike_price": self.strike_price,
            "expiration_date": self._expiration,
            "underlying_symbol": self._underlying,
        }


class _FakeStreamer:
    def __init__(self):
        self.subscribed = []
        self.unsubscribed = []

    async def subscribe(self, event_type, symbols):
        self.subscribed.append((event_type, list(symbols)))

    async def unsubscribe(self, event_type, symbols):
        self.unsubscribed.append((event_type, list(symbols)))

    def unsubscribed_symbols(self):
        out = set()
        for _etype, syms in self.unsubscribed:
            out.update(syms)
        return out


def _slice(expiration, tag, center=100, spread=30):
    return {
        f"{tag}{i}": _Opt(f"{tag}{i}", center + i, expiration) for i in range(-spread, spread + 1)
    }


NEAREST = "2099-01-10"
FRI = "2099-01-15"
MON = "2099-01-18"


def _engine_and_state(tmp_path, expirations_for=None, protected_symbols=None, full_chain=None):
    engine = ChainStreamer(
        session_factory=lambda: None,
        db_path=tmp_path / "cache.db",
        symbols=["XSP"],
        window_strike_count=10,
        expirations_for=expirations_for,
        protected_symbols=protected_symbols,
    )
    conn = streamcache.connect(tmp_path / "cache.db")
    conn.execute(
        "INSERT INTO stream_trades(symbol, last, change, volume, updated_at) VALUES (?,?,?,?,?)",
        ("XSP", 100.0, 0.0, 0.0, time.time()),
    )
    conn.commit()
    state = _State(conn, ["XSP"])
    state.chains["XSP"] = _slice(NEAREST, "N")
    if full_chain is not None:
        engine._canned_full_chain = full_chain
    return engine, state


async def _run_one_pass(engine, state, streamer, monkeypatch):
    """Drive _symbol_refresher for exactly one iteration: the nearest chain is seeded, the full
    chain is canned (no network), and the first `asyncio.sleep` stops the loop."""

    async def fake_nearest(_self, _symbol, _state):
        return state.chains["XSP"]

    async def fake_full(self, _symbol):
        return dict(getattr(self, "_canned_full_chain", {}))

    async def stop_after_one(_seconds):
        state.stop_event.set()

    monkeypatch.setattr(ChainStreamer, "_fetch_dte0_chain_with_retry", fake_nearest)
    monkeypatch.setattr(ChainStreamer, "_fetch_full_chain", fake_full)
    monkeypatch.setattr(asyncio, "sleep", stop_after_one)
    await engine._symbol_refresher(streamer, state, "XSP", "Quote", "Greeks", "Summary", "Trade")


def test_none_hook_is_legacy_behavior(tmp_path, monkeypatch):
    engine, state = _engine_and_state(tmp_path)

    calls = {"full": 0}

    async def fail_full(_self, _symbol):
        calls["full"] += 1
        return {}

    monkeypatch.setattr(ChainStreamer, "_fetch_full_chain", fail_full)
    streamer = _FakeStreamer()

    async def fake_nearest(_self, _symbol, _state):
        return state.chains["XSP"]

    async def stop_after_one(_seconds):
        state.stop_event.set()

    monkeypatch.setattr(ChainStreamer, "_fetch_dte0_chain_with_retry", fake_nearest)
    monkeypatch.setattr(asyncio, "sleep", stop_after_one)
    asyncio.run(engine._symbol_refresher(streamer, state, "XSP", "Quote", "Greeks", "Summary", "Trade"))

    assert calls["full"] == 0
    assert list(state.window_syms) == ["XSP"]  # only the nearest window, no composite keys


def test_extra_dates_get_windows_chain_rows_and_health(tmp_path, monkeypatch):
    full = {NEAREST: _slice(NEAREST, "N"), FRI: _slice(FRI, "F"), MON: _slice(MON, "M")}
    engine, state = _engine_and_state(
        tmp_path, expirations_for=lambda sym: [FRI, MON], full_chain=full
    )
    streamer = _FakeStreamer()
    asyncio.run(_run_one_pass(engine, state, streamer, monkeypatch))

    assert len(state.window_syms[f"XSP@{FRI}"]) == 2 * 10 + 1
    assert len(state.window_syms[f"XSP@{MON}"]) == 2 * 10 + 1
    for date_, tag in ((FRI, "F"), (MON, "M")):
        row = state.conn.execute(
            "SELECT expiration, underlying_symbol FROM stream_chain WHERE streamer_symbol = ?",
            (f"{tag}0",),
        ).fetchone()
        assert row["expiration"] == date_
        assert row["underlying_symbol"] == "XSP"
        health = state.conn.execute(
            "SELECT chain_loaded_at, chain_fetch_error FROM stream_symbol_health WHERE symbol = ?",
            (f"XSP@{date_}",),
        ).fetchone()
        assert health["chain_loaded_at"] is not None
        assert health["chain_fetch_error"] is None


def test_growth_is_served_on_the_next_pass_without_restart(tmp_path, monkeypatch):
    full = {NEAREST: _slice(NEAREST, "N"), FRI: _slice(FRI, "F"), MON: _slice(MON, "M")}
    wanted = {"dates": [FRI]}
    engine, state = _engine_and_state(
        tmp_path, expirations_for=lambda sym: wanted["dates"], full_chain=full
    )
    streamer = _FakeStreamer()
    asyncio.run(_run_one_pass(engine, state, streamer, monkeypatch))
    assert f"XSP@{MON}" not in state.window_syms

    wanted["dates"] = [FRI, MON]  # the request file grew — no restart, next pass serves it
    state.stop_event = asyncio.Event()
    asyncio.run(_run_one_pass(engine, state, streamer, monkeypatch))
    assert len(state.window_syms[f"XSP@{MON}"]) == 2 * 10 + 1


def test_departed_date_is_retired_minus_protected(tmp_path, monkeypatch):
    full = {NEAREST: _slice(NEAREST, "N"), FRI: _slice(FRI, "F"), MON: _slice(MON, "M")}
    wanted = {"dates": [FRI, MON]}
    protected = {"F0", "F1"}  # e.g. open calendar legs a leg_source still declares
    engine, state = _engine_and_state(
        tmp_path,
        expirations_for=lambda sym: wanted["dates"],
        protected_symbols=lambda: protected,
        full_chain=full,
    )
    streamer = _FakeStreamer()
    asyncio.run(_run_one_pass(engine, state, streamer, monkeypatch))
    assert f"XSP@{FRI}" in state.window_syms

    wanted["dates"] = [MON]
    state.stop_event = asyncio.Event()
    asyncio.run(_run_one_pass(engine, state, streamer, monkeypatch))
    assert f"XSP@{FRI}" not in state.window_syms
    assert f"XSP@{MON}" in state.window_syms
    gone = streamer.unsubscribed_symbols()
    assert "F5" in gone
    assert protected & gone == set()


def test_past_dates_are_dropped(tmp_path, monkeypatch):
    full = {NEAREST: _slice(NEAREST, "N"), "2000-01-14": _slice("2000-01-14", "P")}
    engine, state = _engine_and_state(
        tmp_path, expirations_for=lambda sym: ["2000-01-14"], full_chain=full
    )
    asyncio.run(_run_one_pass(engine, state, _FakeStreamer(), monkeypatch))
    assert [k for k in state.window_syms if "@" in k] == []


def test_unlisted_date_records_health_error_not_a_window(tmp_path, monkeypatch):
    full = {NEAREST: _slice(NEAREST, "N")}  # the wanted Friday is not listed yet
    engine, state = _engine_and_state(tmp_path, expirations_for=lambda sym: [FRI], full_chain=full)
    asyncio.run(_run_one_pass(engine, state, _FakeStreamer(), monkeypatch))

    assert f"XSP@{FRI}" not in state.window_syms
    health = state.conn.execute(
        "SELECT chain_fetch_error FROM stream_symbol_health WHERE symbol = ?", (f"XSP@{FRI}",)
    ).fetchone()
    assert health["chain_fetch_error"] == "expiration not listed"


def test_date_duplicating_the_nearest_window_is_skipped(tmp_path, monkeypatch):
    # On a 0DTE Friday the nearest window already serves the requested Friday — a second window on
    # the same symbols would fight the first over subscriptions.
    nearest_slice = _slice(NEAREST, "N")
    full = {NEAREST: nearest_slice}
    engine, state = _engine_and_state(
        tmp_path, expirations_for=lambda sym: [NEAREST], full_chain=full
    )
    asyncio.run(_run_one_pass(engine, state, _FakeStreamer(), monkeypatch))
    assert f"XSP@{NEAREST}" not in state.window_syms


def test_junk_from_the_hook_costs_the_pass_not_the_task(tmp_path, monkeypatch):
    def bad_hook(sym):
        raise OSError("registry unreadable")

    engine, state = _engine_and_state(tmp_path, expirations_for=bad_hook, full_chain={})
    streamer = _FakeStreamer()
    asyncio.run(_run_one_pass(engine, state, streamer, monkeypatch))
    # The nearest window still built; nothing raised out of the refresher.
    assert len(state.window_syms["XSP"]) == 2 * 10 + 1


def test_window_syms_except_unions_all_other_windows(tmp_path):
    engine, state = _engine_and_state(tmp_path)
    state.window_syms = {"XSP": ["A", "B"], f"XSP@{FRI}": ["C"], f"XSP@{MON}": ["D"]}
    assert engine._window_syms_except(state, f"XSP@{FRI}") == {"A", "B", "D"}
    assert engine._window_syms_except(state, "XSP") == {"C", "D"}
