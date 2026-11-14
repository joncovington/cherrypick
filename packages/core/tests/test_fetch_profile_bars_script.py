"""The Market Profile bar recorder (scripts/fetch_profile_bars.py): which contract a future is fetched
from, what it asks the feed for, which events it drops, and what the file keeps -- above all that a
roll never overwrites the old contract's bars with the new one's prices. The SDK is never imported:
the fake streamer is passed in. Prices are invented."""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_profile_bars.py"


def _module():
    spec = importlib.util.spec_from_file_location("fetch_profile_bars", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fpb = _module()
NOW = datetime(2026, 11, 13, 22, 15, tzinfo=UTC)  # 17:15 ET
T0 = datetime(2026, 11, 13, 14, 30, tzinfo=UTC)  # 09:30 ET
BAR = {"open": 1.0, "high": 2.0, "low": 1.0, "close": 2.0, "volume": 10.0}
FMAP = {
    "contracts": {
        "ES": [{"symbol": "/ESZ6", "streamer_symbol": "/ESZ26:XCME", "active_month": True}],
        "NQ": [
            {"symbol": "/NQZ6", "streamer_symbol": "/NQZ26:XCME", "active_month": False},
            {"symbol": "/NQH7", "streamer_symbol": "/NQH27:XCME", "active_month": True},
        ],
    }
}


def _ms(when: datetime) -> int:
    return int(when.timestamp() * 1000)


def _ev(label, when, close=15.0, flags=0, **over):
    px = {"open": close - 0.1, "high": close + 0.2, "low": close - 0.3, "close": close, "volume": 7}
    px.update(over)
    return SimpleNamespace(event_symbol=label, event_flags=flags, time=_ms(when), **px)


class FakeStreamer:
    calls: list[dict] = []
    script: list = []

    def __init__(self, session):
        self.events = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def subscribe_candle(self, symbols, **kwargs):
        FakeStreamer.calls.append({"symbols": list(symbols), **kwargs})
        self.events = list(FakeStreamer.script)

    async def get_event(self, _kind):
        if not self.events:
            await asyncio.sleep(3600)
        return self.events.pop(0)


FAKE_SDK = (FakeStreamer, object)


def _fetch(events, symbols=("SPY",)):
    fpb.QUIET_GAP_S = 0.05
    FakeStreamer.calls, FakeStreamer.script = [], events
    return asyncio.run(fpb.fetch(None, {s: T0 for s in symbols}, FAKE_SDK))


def test_a_future_is_fetched_from_the_maps_active_month_and_cash_from_itself():
    feed = fpb.contracts(FMAP)
    assert feed["/ES"] == "/ESZ26:XCME"
    assert feed["/NQ"] == "/NQH27:XCME"  # the active month, not the first listed
    assert {feed[s] for s in fpb.CASH} == set(fpb.CASH)
    with pytest.raises(KeyError):
        fpb.contracts({"contracts": {"ES": FMAP["contracts"]["ES"]}})  # no NQ: refuse, never guess


def test_the_request_is_one_minute_with_extended_hours():
    _fetch([_ev("SPY{=m}", T0)])
    (call,) = FakeStreamer.calls
    assert call["interval"] == "1m" and call["extended_trading_hours"] is True


def test_both_spellings_of_a_one_minute_label_are_kept_and_other_widths_dropped():
    bars, _ = _fetch([_ev("SPY{=m}", T0), _ev("SPY{=1m}", T0 + timedelta(minutes=1)), _ev("SPY{=5m}", T0)])
    assert sorted(bars["SPY"]) == [_ms(T0), _ms(T0 + timedelta(minutes=1))]


def test_zero_missing_and_removed_candles_are_dropped_and_missing_volume_is_zero():
    events = [
        _ev("SPY{=m}", T0, volume=None),
        _ev("SPY{=m}", T0 + timedelta(minutes=1), open=0.0),
        _ev("SPY{=m}", T0 + timedelta(minutes=2), high=None),
        _ev("SPY{=m}", T0 + timedelta(minutes=3)),
        _ev("SPY{=m}", T0 + timedelta(minutes=3), flags=fpb.REMOVE),
        _ev("OTHER{=m}", T0),
    ]
    bars, _ = _fetch(events)
    assert list(bars["SPY"]) == [_ms(T0)] and bars["SPY"][_ms(T0)]["volume"] == 0.0
    assert "OTHER" not in bars


def test_snap_snip_marks_the_symbol_clipped():
    _, clipped = _fetch([_ev("SPY{=m}", T0, flags=fpb.SNAP_SNIP), _ev("QQQ{=m}", T0)], symbols=("SPY", "QQQ"))
    assert clipped == {"SPY"}


def test_a_symbol_at_the_feeds_cap_is_clipped_without_the_flag(monkeypatch):
    # 2026-11-14: 40 days of SPX came back as exactly 8,000 bars ending SNAP_END, with no SNAP_SNIP
    monkeypatch.setattr(fpb, "FEED_CAP", 3)
    full = [_ev("SPY{=m}", T0 + timedelta(minutes=i)) for i in range(3)]
    short = [_ev("QQQ{=m}", T0 + timedelta(minutes=i)) for i in range(2)]
    _, clipped = _fetch(full + short, symbols=("SPY", "QQQ"))
    assert clipped == {"SPY"}


def test_a_bar_still_forming_is_not_stored():
    conn = fpb.connect(":memory:")
    done_bar = NOW - timedelta(minutes=1)  # ends exactly now
    forming = NOW - timedelta(seconds=30)
    offered = fpb.store_bars(conn, "SPY", "SPY", {_ms(done_bar): BAR, _ms(forming): BAR}, NOW)
    assert offered == [_ms(done_bar)]
    assert conn.execute("SELECT COUNT(*) FROM profile_bars").fetchone()[0] == 1


def test_a_refetched_bar_from_the_same_contract_replaces_the_one_held():
    conn = fpb.connect(":memory:")
    fpb.store_bars(conn, "/ES", "/ESZ26:XCME", {_ms(T0): {**BAR, "close": 1.5}}, NOW)
    fpb.store_bars(
        conn, "/ES", "/ESZ26:XCME", {_ms(T0): {**BAR, "high": 3.0, "close": 2.5, "volume": 99.0}}, NOW
    )
    rows = conn.execute("SELECT et_day, et_time, high, close, volume, contract FROM profile_bars").fetchall()
    assert rows == [("2026-11-13", "09:30", 3.0, 2.5, 99.0, "/ESZ26:XCME")]


def test_a_roll_never_overwrites_the_old_contracts_bars():
    conn = fpb.connect(":memory:")
    fpb.store_bars(conn, "/ES", "/ESZ26:XCME", {_ms(T0): {**BAR, "close": 6000.0}}, NOW)
    # after the roll, the restatement window refetches the same minute from the next contract
    later = T0 + timedelta(days=1)
    fpb.store_bars(
        conn,
        "/ES",
        "/ESH27:XCME",
        {_ms(T0): {**BAR, "close": 6050.0}, _ms(later): {**BAR, "close": 6060.0}},
        NOW + timedelta(days=1),
    )
    rows = conn.execute("SELECT ts_ms, contract, close FROM profile_bars ORDER BY ts_ms").fetchall()
    assert rows == [(_ms(T0), "/ESZ26:XCME", 6000.0), (_ms(later), "/ESH27:XCME", 6060.0)]


def test_first_run_goes_to_full_depth_and_later_runs_only_restate():
    held_ts = _ms(NOW - timedelta(days=1))
    assert fpb.start_for(None, NOW) == NOW - timedelta(days=fpb.DEPTH_DAYS)
    assert fpb.start_for(held_ts, NOW) == NOW - timedelta(days=1 + fpb.RESTATE_DAYS)
    assert fpb.start_for(held_ts, NOW, full=True) == NOW - timedelta(days=fpb.DEPTH_DAYS)


def test_run_stores_futures_under_their_names_from_the_contract_fetched():
    fpb.QUIET_GAP_S = 0.05
    conn = fpb.connect(":memory:")
    fpb.store_bars(conn, "SPX", "SPX", {_ms(T0): BAR}, NOW)
    FakeStreamer.calls = []
    FakeStreamer.script = [_ev("SPX{=m}", T0), _ev("/NQH27:XCME{=m}", T0), _ev("/ESZ26:XCME{=m}", T0)]
    summary = asyncio.run(fpb.run(None, conn, NOW, FMAP, sdk=FAKE_SDK))
    starts = {sym: c["start_time"] for c in FakeStreamer.calls for sym in c["symbols"]}
    assert starts["SPX"] == T0 - timedelta(days=fpb.RESTATE_DAYS)
    assert starts["/NQH27:XCME"] == NOW - timedelta(days=fpb.DEPTH_DAYS)
    held = dict(conn.execute("SELECT symbol, contract FROM profile_bars"))
    assert held == {"SPX": "SPX", "/NQ": "/NQH27:XCME", "/ES": "/ESZ26:XCME"}
    by = {r["symbol"]: r for r in summary}
    assert by["SPX"]["deep"] is False and by["/NQ"]["deep"] is True and by["SPY"]["received"] == 0


def test_any_symbol_with_no_bars_fails_the_run():
    full = [{"symbol": s, "received": 10} for s in fpb.SYMBOLS]
    assert fpb.outcome(full) == {"ok": True, "empty": []}
    one_gone = [r if r["symbol"] != "NDX" else {"symbol": "NDX", "received": 0} for r in full]
    assert fpb.outcome(one_gone) == {"ok": False, "empty": ["NDX"]}
