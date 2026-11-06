"""The VIX term-structure bar fetch (scripts/fetch_vol_curve_bars.py): what it asks the feed for,
which events it drops, and what the file keeps. The SDK is never imported: the fake streamer is
passed in. Prices are invented."""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_vol_curve_bars.py"


def _module():
    spec = importlib.util.spec_from_file_location("fetch_vol_curve_bars", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fvc = _module()
NOW = datetime(2026, 11, 5, 21, 30, tzinfo=UTC)  # 16:30 ET
T0 = datetime(2026, 11, 5, 14, 30, tzinfo=UTC)  # 09:30 ET
BAR = {"open": 1.0, "high": 2.0, "low": 1.0, "close": 2.0}


def _ms(when: datetime) -> int:
    return int(when.timestamp() * 1000)


def _ev(label, when, close=15.0, flags=0, **over):
    px = {"open": close - 0.1, "high": close + 0.2, "low": close - 0.3, "close": close}
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


def _fetch(events, interval="5m", symbols=("VIX",)):
    fvc.QUIET_GAP_S = 0.05
    FakeStreamer.calls, FakeStreamer.script = [], events
    return asyncio.run(fvc.fetch(None, {s: T0 for s in symbols}, interval, FAKE_SDK))


def test_both_spellings_of_the_label_reach_the_right_interval():
    five, _ = _fetch([_ev("VIX{=5m}", T0), _ev("VIX{=5m,tho=true}", T0 + timedelta(minutes=5))], "5m")
    assert sorted(five["VIX"]) == [_ms(T0), _ms(T0 + timedelta(minutes=5))]
    # dxFeed drops a multiplier of 1: a one-hour bar arrives as {=h}, a one-minute one as {=m}.
    hour, _ = _fetch([_ev("VIX{=h}", T0), _ev("VIX{=1h}", T0 + timedelta(hours=1)), _ev("VIX{=m}", T0)], "1h")
    assert sorted(hour["VIX"]) == [_ms(T0), _ms(T0 + timedelta(hours=1))]
    assert fvc.period_key("VIX{=m}") == "1m" and fvc.period_key("VIX") is None


def test_the_request_is_regular_hours():
    _fetch([_ev("VIX{=5m}", T0)])
    (call,) = FakeStreamer.calls
    assert call["extended_trading_hours"] is False and call["interval"] == "5m"


def test_zero_missing_and_removed_candles_are_dropped():
    events = [
        _ev("VIX{=5m}", T0),
        _ev("VIX{=5m}", T0 + timedelta(minutes=5), open=0.0),
        _ev("VIX{=5m}", T0 + timedelta(minutes=10), high=None),
        _ev("VIX{=5m}", T0 + timedelta(minutes=15), flags=fvc.REMOVE),
        _ev("OTHER{=5m}", T0 + timedelta(minutes=20)),
    ]
    bars, _ = _fetch(events)
    assert list(bars["VIX"]) == [_ms(T0)] and "OTHER" not in bars


def test_a_remove_takes_back_a_bar_already_received():
    bars, _ = _fetch([_ev("VIX{=5m}", T0), _ev("VIX{=5m}", T0, flags=fvc.REMOVE)])
    assert bars.get("VIX", {}) == {}


def test_snap_snip_marks_the_symbol_clipped():
    events = [_ev("VIX{=5m}", T0, flags=fvc.SNAP_SNIP), _ev("VVIX{=5m}", T0)]
    _, clipped = _fetch(events, symbols=("VIX", "VVIX"))
    assert clipped == {"VIX"}


def test_a_bar_still_forming_is_not_stored():
    conn = fvc.connect(":memory:")
    last_done = NOW - timedelta(minutes=5)  # ends exactly now
    forming = NOW - timedelta(minutes=4)
    bars = {_ms(T0): BAR, _ms(last_done): BAR, _ms(forming): BAR}
    done = fvc.store_bars(conn, "VIX", "5m", bars, NOW)
    assert done == [_ms(T0), _ms(last_done)]
    assert conn.execute("SELECT COUNT(*) FROM vol_curve_bars").fetchone()[0] == 2


def test_a_refetched_bar_replaces_the_one_held_and_carries_its_et_time():
    conn = fvc.connect(":memory:")
    fvc.store_bars(conn, "VIX", "5m", {_ms(T0): {**BAR, "close": 1.5}}, NOW)
    fvc.store_bars(conn, "VIX", "5m", {_ms(T0): {**BAR, "high": 3.0, "close": 2.5}}, NOW)
    rows = conn.execute("SELECT et_day, et_time, high, close FROM vol_curve_bars").fetchall()
    assert rows == [("2026-11-05", "09:30", 3.0, 2.5)]


def test_first_run_goes_to_full_depth_and_later_runs_only_restate():
    held_ts = _ms(NOW - timedelta(days=1))
    assert fvc.start_for(None, "5m", NOW) == NOW - timedelta(days=fvc.DEPTH_DAYS["5m"])
    assert fvc.start_for(None, "1h", NOW) == NOW - timedelta(days=fvc.DEPTH_DAYS["1h"])
    assert fvc.start_for(held_ts, "5m", NOW) == NOW - timedelta(days=1 + fvc.RESTATE_DAYS)
    assert fvc.start_for(held_ts, "5m", NOW, full=True) == NOW - timedelta(days=fvc.DEPTH_DAYS["5m"])


def test_run_asks_deep_for_a_series_never_held_and_shallow_for_one_held():
    fvc.QUIET_GAP_S = 0.05
    conn = fvc.connect(":memory:")
    fvc.store_bars(conn, "VIX", "5m", {_ms(T0): BAR}, NOW)
    FakeStreamer.calls, FakeStreamer.script = [], [_ev("VIX{=5m}", T0), _ev("VIX{=h}", T0)]
    summary = asyncio.run(fvc.run(None, conn, NOW, sdk=FAKE_SDK))
    five = [c for c in FakeStreamer.calls if c["interval"] == "5m"]
    starts = {sym: c["start_time"] for c in five for sym in c["symbols"]}
    assert starts["VIX"] == T0 - timedelta(days=fvc.RESTATE_DAYS)
    assert starts["SPX"] == NOW - timedelta(days=fvc.DEPTH_DAYS["5m"])
    vix = next(r for r in summary if r["symbol"] == "VIX" and r["interval"] == "5m")
    spx = next(r for r in summary if r["symbol"] == "SPX" and r["interval"] == "5m")
    assert vix["deep"] is False and spx["deep"] is True and vix["written"] == 1


def test_an_empty_series_warns_unless_it_is_a_tenor_the_state_is_read_from():
    def row(sym, interval, received):
        return {"symbol": sym, "interval": interval, "received": received}

    full = [row(s, iv, 10) for s in fvc.SYMBOLS for iv in fvc.WIDTH_MS]
    assert fvc.outcome(full) == {"ok": True, "empty": [], "required_empty": []}
    quiet = [r if (r["symbol"], r["interval"]) != ("VIX1D", "5m") else row("VIX1D", "5m", 0) for r in full]
    got = fvc.outcome(quiet)
    assert got["ok"] and got["empty"] == ["VIX1D 5m"] and got["required_empty"] == []
    for sym in fvc.REQUIRED:
        gone = [r if (r["symbol"], r["interval"]) != (sym, "1h") else row(sym, "1h", 0) for r in full]
        got = fvc.outcome(gone)
        assert not got["ok"] and got["required_empty"] == [f"{sym} 1h"]
