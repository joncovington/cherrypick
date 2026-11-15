"""The gold bar recorder (scripts/fetch_gold_bars.py): which contract /GC is fetched from and how far
back, what it asks the feed for, which events it drops, the session day a daily bar is filed under,
and that a roll never overwrites the old month's bars with the new one's prices. The SDK is never
imported: the fake streamer is passed in. Prices are invented."""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_gold_bars.py"


def _module():
    spec = importlib.util.spec_from_file_location("fetch_gold_bars", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fgb = _module()
NOW = datetime(2026, 11, 14, 1, 15, tzinfo=UTC)  # 20:15 ET on 11-13
T0 = datetime(2026, 11, 13, 14, 0, tzinfo=UTC)  # 09:00 ET
DAY = datetime(2026, 11, 13, 0, 0, tzinfo=UTC)  # the feed's stamp for the 11-13 session
BAR = {"open": 1.0, "high": 2.0, "low": 1.0, "close": 2.0, "volume": 10.0}
FMAP = {
    "contracts": {
        "GC": [
            {"symbol": "/GCZ6", "streamer_symbol": "/GCZ26:XCEC", "active_month": False},
            {"symbol": "/GCG7", "streamer_symbol": "/GCG27:XCEC", "active_month": True},
        ]
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


def _fetch(events, symbols=("GLD",), interval="1h"):
    fgb.QUIET_GAP_S = 0.05
    FakeStreamer.calls, FakeStreamer.script = [], events
    return asyncio.run(fgb.fetch(None, {s: T0 for s in symbols}, interval, FAKE_SDK))


def test_gc_is_the_maps_active_month_and_the_others_are_their_own_symbols():
    feed = fgb.contracts(FMAP)
    assert feed == {"/GC": "/GCG27:XCEC", "/GC:XCEC": "/GC:XCEC", "GLD": "GLD"}
    with pytest.raises(KeyError):
        fgb.contracts({"contracts": {}})  # no GC: refuse, never guess


def test_every_request_includes_extended_hours():
    _fetch([_ev("GLD{=h}", T0)])
    _fetch([_ev("GLD{=d}", DAY)], interval="1d")
    assert all(c["extended_trading_hours"] is True for c in FakeStreamer.calls)


def test_both_spellings_of_an_hour_are_kept_and_other_widths_dropped():
    events = [
        _ev("GLD{=h}", T0),
        _ev("GLD{=1h}", T0 + timedelta(hours=1)),
        _ev("GLD{=d}", DAY),
        _ev("GLD{=4h}", T0),
    ]
    bars, _ = _fetch(events)
    assert sorted(bars["GLD"]) == [_ms(T0), _ms(T0 + timedelta(hours=1))]


def test_zero_missing_and_removed_candles_are_dropped_and_missing_volume_is_zero():
    events = [
        _ev("GLD{=h}", T0, volume=None),
        _ev("GLD{=h}", T0 + timedelta(hours=1), open=0.0),
        _ev("GLD{=h}", T0 + timedelta(hours=2), high=None),
        _ev("GLD{=h}", T0 + timedelta(hours=3)),
        _ev("GLD{=h}", T0 + timedelta(hours=3), flags=fgb.REMOVE),
        _ev("OTHER{=h}", T0),
    ]
    bars, _ = _fetch(events)
    assert list(bars["GLD"]) == [_ms(T0)] and bars["GLD"][_ms(T0)]["volume"] == 0.0
    assert "OTHER" not in bars


def test_snap_snip_marks_the_symbol_clipped():
    _, clipped = _fetch(
        [_ev("GLD{=h}", T0, flags=fgb.SNAP_SNIP), _ev("/GC:XCEC{=h}", T0)], symbols=("GLD", "/GC:XCEC")
    )
    assert clipped == {"GLD"}


def test_a_bar_still_forming_is_not_stored():
    conn = fgb.connect(":memory:")
    done_bar = NOW - timedelta(hours=1)  # ends exactly now
    forming = NOW - timedelta(minutes=30)
    offered = fgb.store_bars(conn, "GLD", "1h", "GLD", {_ms(done_bar): BAR, _ms(forming): BAR}, NOW)
    assert offered == [_ms(done_bar)]
    today = fgb.store_bars(conn, "GLD", "1d", "GLD", {_ms(DAY + timedelta(days=1)): BAR}, NOW)
    assert today == []  # 11-14's daily bar has not closed at 20:15 on 11-13


def test_a_daily_bar_is_filed_under_its_session_not_the_et_evening_it_is_stamped():
    conn = fgb.connect(":memory:")
    fgb.store_bars(conn, "GLD", "1d", "GLD", {_ms(DAY): BAR}, NOW)
    fgb.store_bars(conn, "GLD", "1h", "GLD", {_ms(T0): BAR}, NOW)
    rows = dict(conn.execute("SELECT interval, et_day || ' ' || et_time FROM gold_bars"))
    assert rows == {"1d": "2026-11-13 19:00", "1h": "2026-11-13 09:00"}


def test_a_refetched_bar_from_the_same_contract_replaces_the_one_held():
    conn = fgb.connect(":memory:")
    fgb.store_bars(conn, "/GC", "1h", "/GCG27:XCEC", {_ms(T0): {**BAR, "close": 1.5}}, NOW)
    fgb.store_bars(
        conn, "/GC", "1h", "/GCG27:XCEC", {_ms(T0): {**BAR, "high": 3.0, "close": 2.5, "volume": 99.0}}, NOW
    )
    rows = conn.execute("SELECT high, close, volume, contract FROM gold_bars").fetchall()
    assert rows == [(3.0, 2.5, 99.0, "/GCG27:XCEC")]


def test_a_roll_never_overwrites_the_old_months_bars():
    conn = fgb.connect(":memory:")
    fgb.store_bars(conn, "/GC", "1h", "/GCZ26:XCEC", {_ms(T0): {**BAR, "close": 4000.0}}, NOW)
    later = T0 + timedelta(days=1)
    fgb.store_bars(
        conn,
        "/GC",
        "1h",
        "/GCG27:XCEC",
        {_ms(T0): {**BAR, "close": 4030.0}, _ms(later): {**BAR, "close": 4040.0}},
        NOW + timedelta(days=1),
    )
    rows = conn.execute("SELECT ts_ms, contract, close FROM gold_bars ORDER BY ts_ms").fetchall()
    assert rows == [(_ms(T0), "/GCZ26:XCEC", 4000.0), (_ms(later), "/GCG27:XCEC", 4040.0)]


def test_first_run_goes_deep_except_for_a_futures_month_and_later_runs_only_restate():
    held_ts = _ms(NOW - timedelta(days=1))
    for interval in fgb.WIDTH_MS:
        assert fgb.start_for(None, interval, NOW) == NOW - timedelta(days=fgb.DEPTH_DAYS[interval])
        assert fgb.start_for(held_ts, interval, NOW) == NOW - timedelta(days=1 + fgb.RESTATE_DAYS)
        assert fgb.start_for(held_ts, interval, NOW, full=True) == NOW - timedelta(
            days=fgb.DEPTH_DAYS[interval]
        )
        # a month that just became the front traded thinly, at another price, before it was
        assert fgb.start_for(None, interval, NOW, deep=False) == NOW - timedelta(days=fgb.RESTATE_DAYS)
        assert fgb.start_for(held_ts, interval, NOW, full=True, deep=False) == NOW - timedelta(
            days=fgb.RESTATE_DAYS
        )


def test_run_stores_gc_under_its_name_from_the_month_fetched_and_never_keeps_it_deep():
    fgb.QUIET_GAP_S = 0.05
    conn = fgb.connect(":memory:")
    fgb.store_bars(conn, "GLD", "1h", "GLD", {_ms(T0): BAR}, NOW)
    old = NOW - timedelta(days=60)  # the active month's own history, from before it was the front
    FakeStreamer.calls = []
    FakeStreamer.script = [
        _ev("GLD{=h}", T0),
        _ev("/GCG27:XCEC{=h}", old),
        _ev("/GCG27:XCEC{=h}", T0),
        _ev("/GCJ27:XCEC{=h}", T0),
        _ev("/GC:XCEC{=h}", T0),
    ]
    summary = asyncio.run(fgb.run_interval(None, conn, "1h", NOW, FMAP, sdk=FAKE_SDK))
    starts = {sym: c["start_time"] for c in FakeStreamer.calls for sym in c["symbols"]}
    assert starts["GLD"] == T0 - timedelta(days=fgb.RESTATE_DAYS)
    assert starts["/GC:XCEC"] == NOW - timedelta(days=fgb.DEPTH_DAYS["1h"])
    # /GC and its dated contract share the feed symbol: asked once, from the dated contract's depth
    assert starts["/GCG27:XCEC"] == NOW - timedelta(days=fgb.DEPTH_DAYS["1h"])
    assert sum(sym == "/GCG27:XCEC" for c in FakeStreamer.calls for sym in c["symbols"]) == 1
    rows = conn.execute("SELECT symbol, ts_ms, contract FROM gold_bars").fetchall()
    held = {}
    for sym, ts, contract in rows:
        held.setdefault(sym, set()).add((ts, contract))
    assert held["/GC"] == {(_ms(T0), "/GCG27:XCEC")}, "/GC keeps only its own window"
    assert held["/GCG27:XCEC"] == {(_ms(old), "/GCG27:XCEC"), (_ms(T0), "/GCG27:XCEC")}
    assert held["/GCJ27:XCEC"] == {(_ms(T0), "/GCJ27:XCEC")}  # the next month is recorded too
    by = {r["symbol"]: r for r in summary}
    assert by["GLD"]["deep"] is False and by["/GC"]["deep"] is True
    assert all(r["required"] for r in summary)


def test_contract_symbols_and_the_cycle():
    assert fgb.parse_contract("/GCZ26:XCEC") == ("Z", 2026)
    with pytest.raises(ValueError):
        fgb.parse_contract("/GC:XCEC")
    assert fgb.contract_symbol("J", 2026) == "/GCJ26:XCEC"
    assert fgb.next_in_cycle("/GCZ26:XCEC") == "/GCG27:XCEC"  # December rolls into next February
    assert fgb.next_in_cycle("/GCJ26:XCEC") == "/GCM26:XCEC"
    assert fgb.next_in_cycle("/GCH26:XCEC") == "/GCJ26:XCEC"  # an off-cycle month finds the next cycle one
    got = fgb.cycle_contracts("/GCZ26:XCEC", 2025)
    assert got[0] == "/GCG25:XCEC" and got[-1] == "/GCG27:XCEC" and len(got) == 6 + 6 + 1
    assert fgb.dated_contracts(FMAP) == ["/GCG27:XCEC", "/GCJ27:XCEC"]
    back = fgb.dated_contracts(FMAP, backfill=True)
    assert back[0] == fgb.contract_symbol("G", fgb.BACKFILL_FROM_YEAR) and back[-1] == "/GCJ27:XCEC"


def test_a_backfill_records_only_dated_contracts_at_full_depth_and_needs_none():
    fgb.QUIET_GAP_S = 0.05
    conn = fgb.connect(":memory:")
    fgb.store_bars(conn, "/GCJ26:XCEC", "1d", "/GCJ26:XCEC", {_ms(DAY): BAR}, NOW)  # held: still deep
    FakeStreamer.calls = []
    FakeStreamer.script = [_ev("/GCJ26:XCEC{=d}", DAY - timedelta(days=300)), _ev("GLD{=d}", DAY)]
    summary = asyncio.run(fgb.run_interval(None, conn, "1d", NOW, FMAP, backfill=True, sdk=FAKE_SDK))
    asked = [sym for c in FakeStreamer.calls for sym in c["symbols"]]
    assert set(asked) == set(fgb.dated_contracts(FMAP, backfill=True))
    assert all(c["start_time"] == NOW - timedelta(days=fgb.DEPTH_DAYS["1d"]) for c in FakeStreamer.calls)
    assert {s for (s,) in conn.execute("SELECT DISTINCT symbol FROM gold_bars")} == {"/GCJ26:XCEC"}
    assert conn.execute("SELECT COUNT(*) FROM gold_bars").fetchone()[0] == 2
    assert not any(r["required"] for r in summary)
    assert fgb.outcome(summary)["ok"] is True  # contracts with nothing are listed, never a failure


def test_any_series_the_run_needs_with_no_bars_fails_it():
    full = [{"symbol": s, "interval": i, "received": 10} for s in fgb.SYMBOLS for i in fgb.WIDTH_MS]
    assert fgb.outcome(full) == {"ok": True, "empty": []}
    one_gone = [r if (r["symbol"], r["interval"]) != ("GLD", "1d") else {**r, "received": 0} for r in full]
    assert fgb.outcome(one_gone) == {"ok": False, "empty": ["GLD 1d"]}
    spare = [*full, {"symbol": "/GCG16:XCEC", "interval": "1d", "received": 0, "required": False}]
    assert fgb.outcome(spare) == {"ok": True, "empty": ["/GCG16:XCEC 1d"]}
