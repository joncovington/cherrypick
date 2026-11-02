"""A held live IC is managed off the broker's REST quotes when the tick's snapshot cannot be built.

2026-11-02: DXLink returned 502 from 14:19 to 15:18 ET. Every live tick stopped at "no snapshot: no
price" and exited before it reached an open IC, so a side whose stop had triggered was closed well
past it once ticks resumed. The broker's REST reads kept succeeding the whole time.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta

import pytest
from cherrypick.core.clock import ET
from test_live_scaffold import DAY, LC, LP, SC, SP, FakeBroker, _config, _open_trade_row

from cherrypick.meic import live_loop, paper

NOW = datetime(2026, 7, 8, 11, 0, tzinfo=ET)


def _rest(age_seconds=5, *, under_age=5, quotes=None):
    stamp = (NOW - timedelta(seconds=age_seconds)).isoformat()
    quotes = quotes or {
        SP: (2.40, 2.50),  # short put: was 0.55 credit, now 2.5 to close
        LP: (0.10, 0.15),
        SC: (0.30, 0.35),
        LC: (0.05, 0.08),
    }
    return {
        "ok": True,
        "underlying": {"price": 584.0, "updated_at": (NOW - timedelta(seconds=under_age)).isoformat()},
        "quotes": {
            s: {"bid": b, "ask": a, "mid": round((b + a) / 2, 4), "updated_at": stamp}
            for s, (b, a) in quotes.items()
        },
    }


def test_fallback_snapshot_marks_held_legs_and_offers_no_entry():
    snap = live_loop.fallback_snapshot("QQQ", NOW, _rest())
    assert snap["candidates"] == [] and snap["data_fallback"] == "broker_rest"
    assert snap["date"] == DAY and snap["now_et"] == "11:00" and snap["underlying_price"] == 584.0
    assert snap["leg_quotes"][SP] == {"bid": 2.40, "ask": 2.50, "mid": 2.45}


def test_fallback_snapshot_drops_an_old_or_unstamped_quote():
    rest = _rest()
    rest["quotes"][LP]["updated_at"] = (NOW - timedelta(seconds=600)).isoformat()
    rest["quotes"][LC]["updated_at"] = None
    rest["quotes"][SC]["updated_at"] = "2026-07-08T10:59:59"  # no zone: refused, not guessed
    snap = live_loop.fallback_snapshot("QQQ", NOW, rest)
    assert set(snap["leg_quotes"]) == {SP}
    assert live_loop.fallback_snapshot("QQQ", NOW, _rest(under_age=600))["underlying_price"] is None


def test_fallback_snapshot_is_none_when_no_held_leg_is_fresh():
    """Nothing to manage with: the tick must still fail, so the watchdog's live-freshness check fires."""
    assert live_loop.fallback_snapshot("QQQ", NOW, _rest(age_seconds=600)) is None
    assert live_loop.fallback_snapshot("QQQ", NOW, {"ok": True, "quotes": {}}) is None


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    db_path = str(tmp_path / "live.db")
    subprocess.run(
        [sys.executable, "-m", "cherrypick.meic.db", "--db", db_path, "init_db"],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(live_loop._paths, "live_db_path", lambda: db_path)
    monkeypatch.setattr(live_loop._pl, "_now_et", lambda: NOW)
    monkeypatch.setattr(live_loop._stream_request, "register_live", lambda cfg: None)
    monkeypatch.setattr(live_loop, "_build_snapshot", lambda cfg, symbol: (None, "no price"))
    return db_path


def _tick(monkeypatch, rest, broker):
    calls = []

    def run_json(cmd, timeout=90):
        calls.append(cmd)
        assert "get_rest_quotes" in cmd, cmd
        return rest

    monkeypatch.setattr(live_loop._pl, "_run_json", run_json)
    monkeypatch.setattr(live_loop, "make_broker", lambda cfg, designated: broker)
    lines = []
    monkeypatch.setattr(live_loop, "_append_log", lines.append)
    code = live_loop._tick(_config(symbol="QQQ"), "TEST0001", [], True)
    return code, calls, lines


def test_a_tick_with_no_snapshot_still_stops_a_held_ic_off_rest_quotes(ledger, monkeypatch):
    """Shown to fail on the pre-change loop: `_tick` returned 1 at "no snapshot" and placed nothing."""
    paper._save_trade(_open_trade_row("QQQ"), ledger)
    broker = FakeBroker()
    code, calls, lines = _tick(monkeypatch, _rest(), broker)
    assert code == 0
    assert set(calls[0][calls[0].index("--options") + 1 :]) == {SP, LP, SC, LC}
    assert [p["live"] for p in broker.placed] == [True], "one real close, for the put side"
    row = live_loop._trade_row("OPEN1", ledger)
    assert row["put_stop_fill_status"] == "pending" and row["call_stop_fill_status"] is None
    summary = [line for line in lines if '"ok": true' in line][-1]
    assert '"data_fallback": "broker_rest"' in summary
    assert "market_data_fallback (no price)" in summary, "entries blocked, and why"
    assert any("CRITICAL" in line for line in lines)


def test_a_tick_with_no_snapshot_and_nothing_held_fails_as_before(ledger, monkeypatch):
    broker = FakeBroker()
    code, calls, _lines = _tick(monkeypatch, _rest(), broker)
    assert code == 1 and calls == [] and broker.placed == []


def test_a_tick_whose_rest_quotes_are_stale_fails_and_places_nothing(ledger, monkeypatch):
    paper._save_trade(_open_trade_row("QQQ"), ledger)
    broker = FakeBroker()
    code, _calls, _lines = _tick(monkeypatch, _rest(age_seconds=600), broker)
    assert code == 1 and broker.placed == []
