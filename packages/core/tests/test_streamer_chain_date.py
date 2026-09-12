"""Which expiration is "0DTE", and when it rolls.

The 2026-09-10 incident: the nightly DXLink drop reconnected in-process at 23:58 ET on the 9th,
the chain fetch picked the expiration NEAREST the machine's local calendar date by absolute
distance -- the 9th itself, expired eight hours earlier -- and because the process survived the
night nothing refetched on the 10th. Every pure stream-cache consumer saw only a 1DTE chain and
the flies module refused all 4,662 entry attempts as `no_0dte_expiration`. So: the choice is the
first expiration on or after the ET session date, and a chain loaded on one session date is
refetched the moment the session date changes.
"""

import asyncio
import time
from datetime import date, datetime

import pytest

from cherrypick.core import streamcache
from cherrypick.core import streamer as streamer_mod
from cherrypick.core.streamer import ChainStreamer, _State


class _Opt:
    def __init__(self, sym, strike, expiration, underlying="SPX"):
        self.streamer_symbol = sym
        self.strike_price = strike
        self.expiration_date = date.fromisoformat(expiration)
        self._underlying = underlying

    def model_dump(self, mode="json"):
        return {
            "streamer_symbol": self.streamer_symbol,
            "strike_price": self.strike_price,
            "expiration_date": self.expiration_date.isoformat(),
            "underlying_symbol": self._underlying,
        }


def _chain_keyed_by_date(*dates):
    return {date.fromisoformat(d): [_Opt(f"{d}-{i}", 100 + i, d) for i in range(-2, 3)] for d in dates}


def _engine(tmp_path):
    return ChainStreamer(session_factory=lambda: None, db_path=tmp_path / "cache.db", symbols=["SPX"])


def _fix_et_today(monkeypatch, iso):
    """Pin the ET session date the engine reads, independent of the machine's clock or zone."""
    monkeypatch.setattr(ChainStreamer, "_session_date", staticmethod(lambda: iso))


def _fake_provider(monkeypatch, chain):
    import tastytrade.instruments as instruments

    async def _get(_session, _underlying):
        return chain

    monkeypatch.setattr(instruments, "get_option_chain", _get)


# --------------------------------------------------------------------------- the choice
def test_picks_the_first_expiration_on_or_after_the_et_session_date(tmp_path, monkeypatch):
    # Late on the 9th ET: the 9th is NEARER to the calendar date than the 10th is, and it is
    # already expired. The 10th is the session the chain has to serve.
    _fix_et_today(monkeypatch, "2026-09-10")
    _fake_provider(monkeypatch, _chain_keyed_by_date("2026-09-09", "2026-09-10", "2026-09-11"))
    chain = asyncio.run(_engine(tmp_path)._fetch_dte0_chain("SPX"))
    assert {o.expiration_date.isoformat() for o in chain.values()} == {"2026-09-10"}


def test_yesterday_is_never_chosen_even_when_it_is_the_only_nearer_date(tmp_path, monkeypatch):
    _fix_et_today(monkeypatch, "2026-09-10")
    _fake_provider(monkeypatch, _chain_keyed_by_date("2026-09-09", "2026-09-14"))
    chain = asyncio.run(_engine(tmp_path)._fetch_dte0_chain("SPX"))
    assert {o.expiration_date.isoformat() for o in chain.values()} == {"2026-09-14"}


def test_a_chain_with_only_past_expirations_is_an_error_not_a_silent_stale_load(tmp_path, monkeypatch):
    _fix_et_today(monkeypatch, "2026-09-10")
    _fake_provider(monkeypatch, _chain_keyed_by_date("2026-09-08", "2026-09-09"))
    with pytest.raises(ValueError, match="2026-09-10"):
        asyncio.run(_engine(tmp_path)._fetch_dte0_chain("SPX"))


def test_session_date_is_the_et_calendar_date():
    # A plain sanity pin: the default reads ET, so a Mountain-time host at 21:58 local is already
    # on the next ET day after 22:00 MT -- but at 21:58 MT on the 9th it is 23:58 ET, still the 9th.
    assert streamer_mod.ChainStreamer._session_date() == datetime.now(tz=streamer_mod._ET).date().isoformat()


# --------------------------------------------------------------------------- the roll
def test_loaded_chain_records_its_expiration_in_symbol_health(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    conn = streamcache.connect(tmp_path / "cache.db")
    state = _State(conn, ["SPX"])

    async def _fetch(_self, _symbol):
        return {o.streamer_symbol: o for o in _chain_keyed_by_date("2026-09-10")[date(2026, 9, 10)]}

    monkeypatch.setattr(ChainStreamer, "_fetch_dte0_chain", _fetch)
    asyncio.run(engine._fetch_dte0_chain_with_retry("SPX", state))
    row = conn.execute("SELECT chain_expiration FROM stream_symbol_health WHERE symbol = 'SPX'").fetchone()
    assert row["chain_expiration"] == "2026-09-10"


def test_refresher_refetches_the_chain_when_the_session_date_rolls(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    conn = streamcache.connect(tmp_path / "cache.db")
    conn.execute(
        "INSERT INTO stream_trades(symbol, last, change, volume, updated_at) VALUES (?,?,?,?,?)",
        ("SPX", 100.0, 0.0, 0.0, time.time()),
    )
    conn.commit()
    state = _State(conn, ["SPX"])

    fetches: list[str] = []
    # The first two reads (the initial load, then the first pass) are the 9th; the date then rolls.
    reads = {"n": 0}

    def _date():
        reads["n"] += 1
        return "2026-09-09" if reads["n"] <= 2 else "2026-09-10"

    monkeypatch.setattr(ChainStreamer, "_session_date", staticmethod(_date))

    async def _fetch(_self, _symbol, _state):
        today = state.chain_dates.get("probe", "2026-09-09")
        fetches.append(today)
        exp = "2026-09-09" if len(fetches) == 1 else "2026-09-10"
        return {o.streamer_symbol: o for o in _chain_keyed_by_date(exp)[date.fromisoformat(exp)]}

    passes = {"n": 0}

    async def _sleep(seconds):
        # Only the refresher's own poll sleep counts a pass; _send_subs paces with sleeps of its own.
        if seconds != engine.window_poll_s:
            return
        passes["n"] += 1
        if len(fetches) >= 2 or passes["n"] >= 10:
            state.stop_event.set()

    class _Streamer:
        async def subscribe(self, *_a):
            return None

        async def unsubscribe(self, *_a):
            return None

    monkeypatch.setattr(ChainStreamer, "_fetch_dte0_chain_with_retry", _fetch)
    monkeypatch.setattr(asyncio, "sleep", _sleep)
    asyncio.run(engine._symbol_refresher(_Streamer(), state, "SPX", "Quote", "Greeks", "Summary", "Trade"))

    # One initial fetch on the 9th, one refetch when the date read the 10th -- and the window was
    # rebuilt onto the new chain rather than left pointing at expired symbols.
    assert len(fetches) == 2
    assert state.chain_dates["SPX"] == "2026-09-10"
    assert all(s.startswith("2026-09-10") for s in state.window_syms["SPX"])


def test_refresher_does_not_refetch_within_the_same_session_date(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    conn = streamcache.connect(tmp_path / "cache.db")
    conn.execute(
        "INSERT INTO stream_trades(symbol, last, change, volume, updated_at) VALUES (?,?,?,?,?)",
        ("SPX", 100.0, 0.0, 0.0, time.time()),
    )
    conn.commit()
    state = _State(conn, ["SPX"])
    _fix_et_today(monkeypatch, "2026-09-10")
    fetches = {"n": 0}

    async def _fetch(_self, _symbol, _state):
        fetches["n"] += 1
        return {o.streamer_symbol: o for o in _chain_keyed_by_date("2026-09-10")[date(2026, 9, 10)]}

    passes = {"n": 0}

    async def _sleep(seconds):
        if seconds != engine.window_poll_s:
            return
        passes["n"] += 1
        if passes["n"] >= 3:
            state.stop_event.set()

    class _Streamer:
        async def subscribe(self, *_a):
            return None

        async def unsubscribe(self, *_a):
            return None

    monkeypatch.setattr(ChainStreamer, "_fetch_dte0_chain_with_retry", _fetch)
    monkeypatch.setattr(asyncio, "sleep", _sleep)
    asyncio.run(engine._symbol_refresher(_Streamer(), state, "SPX", "Quote", "Greeks", "Summary", "Trade"))
    assert fetches["n"] == 1
