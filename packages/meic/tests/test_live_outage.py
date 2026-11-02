"""The live tick when its market data fails (2026-11-02).

DXLink refused every connection from 14:19 to 15:18 ET. The full snapshot needs a spot price for the
entry menu, so every tick exited before fill confirmation or stop management ran, and a held put
side was stopped 79 minutes late at $7.15 against its $3.60 trigger. These pin the manage-only
path: a failed entry snapshot blocks entries, and the tick still manages what it holds on the held
legs' own quotes (stream cache, then broker REST). Offline; the broker is a fake.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
from test_live_scaffold import (  # noqa: E402
    DAY,
    LC,
    LP,
    OCC,
    SC,
    SP,
    FakeBroker,
    _config,
    _init_db,
    _open_trade_row,
)

from cherrypick.meic import live_loop, paper  # noqa: E402

ET = ZoneInfo("America/New_York")
NOW = datetime.fromisoformat(f"{DAY}T11:00:00").replace(tzinfo=ET)

# The QQQ put side's cost to close has blown through its stop (as in _stopping_snapshot).
STOPPING = {
    SP: {"bid": 2.40, "ask": 2.50, "mid": 2.45},
    LP: {"bid": 0.10, "ask": 0.15, "mid": 0.125},
    SC: {"bid": 0.30, "ask": 0.35, "mid": 0.325},
    LC: {"bid": 0.05, "ask": 0.08, "mid": 0.065},
}


class RestBroker(FakeBroker):
    """FakeBroker with the REST quote read. `options` is keyed by OCC symbol, as the broker's own
    endpoint is; `None` stands for the REST read failing too."""

    def __init__(self, options=None, spot=590.0, **kw):
        super().__init__(**kw)
        self._options = options
        self._spot = spot
        self.rest_calls = []

    def rest_quotes(self, *, options=(), indices=(), timeout_seconds=10.0):
        self.rest_calls.append({"options": list(options), "indices": list(indices)})
        if self._options is None:
            return {"options": {}, "indices": {}}
        return {
            "options": {o: q for o, q in self._options.items() if o in options},
            "indices": {i: self._spot for i in indices},
        }


def _rest_stopping():
    return {OCC[s]: q for s, q in STOPPING.items()}


def _held(tmp_path):
    db_path = _init_db(tmp_path)
    paper._save_trade(_open_trade_row("QQQ"), db_path)
    return db_path


def test_manage_snapshot_prices_the_held_legs_from_rest_when_the_cache_is_dark(tmp_path):
    db_path = _held(tmp_path)
    broker = RestBroker(options=_rest_stopping())
    snap, report = live_loop._manage_snapshot(
        "QQQ", db_path, broker, now=NOW, cache_path=tmp_path / "no_cache.db"
    )
    assert report["open_trades"] == 1 and report["legs"] == 4
    assert report["unpriced"] == [] and report["sources"] == {"stream": 0, "rest": 4}
    assert report["spot_source"] == "rest" and snap["underlying_price"] == 590.0
    assert set(snap["leg_quotes"]) == {SP, LP, SC, LC} and snap["candidates"] == []
    # REST is asked in the broker's own symbology, never the streamer's.
    assert sorted(broker.rest_calls[0]["options"]) == sorted(OCC[s] for s in (SP, LP, SC, LC))


def test_a_manage_only_snapshot_still_stops_a_side_through_its_trigger(tmp_path):
    db_path = _held(tmp_path)
    broker = RestBroker(options=_rest_stopping())
    snap, _ = live_loop._manage_snapshot("QQQ", db_path, broker, now=NOW, cache_path=tmp_path / "x.db")
    summary = live_loop.run_once(
        _config(symbol="QQQ"), snap, db_path, broker, live=True, log=lambda *_: None,
        entry_block=["manage_only (no snapshot: no price)"],
    )  # fmt: skip
    assert summary["closing"] == 1, "the put side's close went out"
    assert summary["entry"] == {"blocked": ["manage_only (no snapshot: no price)"]}
    row = live_loop._trade_row("OPEN1", db_path)
    assert row["put_stop_fill_status"] == "pending" and row["call_stop_fill_status"] is None


def _tick_env(monkeypatch, tmp_path, db_path, broker):
    """Everything `_tick` reaches outside the ledger, pointed at the test: the entry snapshot
    fails as it did on 2026-11-02, and the live log is captured."""
    logged = []
    monkeypatch.setattr(live_loop, "_build_snapshot", lambda cfg, symbol: (None, "no price"))
    monkeypatch.setattr(live_loop, "make_broker", lambda cfg, designated: broker)
    monkeypatch.setattr(live_loop._paths, "live_db_path", lambda: Path(db_path))
    monkeypatch.setattr(live_loop._paths, "stream_cache_path", lambda: tmp_path / "no_cache.db")
    monkeypatch.setattr(live_loop._stream_request, "register_live", lambda cfg: None)
    monkeypatch.setattr(live_loop, "halt_flag_path", lambda: str(tmp_path / "HALT"))
    monkeypatch.setattr(live_loop._pl, "_now_et", lambda: NOW)
    monkeypatch.setattr(live_loop, "_append_log", logged.append)
    monkeypatch.setattr(live_loop, "_record_attempt", lambda *a, **k: None)
    return logged


def test_a_tick_whose_entry_snapshot_fails_still_manages_a_held_ic(tmp_path, monkeypatch, capsys):
    """Shown to fail on the pre-change tick: it returned 1 at 'no snapshot' and placed nothing."""
    db_path = _held(tmp_path)
    broker = RestBroker(options=_rest_stopping())
    logged = _tick_env(monkeypatch, tmp_path, db_path, broker)
    code = live_loop._tick(_config(symbol="QQQ"), "****1234", [], True)
    assert code == 0, "every held leg was priced, so the tick did its job"
    assert any(p["live"] is True for p in broker.placed), "the stop close was submitted"
    line = json.loads(logged[-1])
    assert line["ok"] is True and line["manage_only"]["error"] == "no snapshot: no price"
    assert line["entry"]["blocked"][-1].startswith("manage_only")


def test_a_manage_only_tick_that_cannot_price_a_held_leg_fails_loudly(tmp_path, monkeypatch, capsys):
    db_path = _held(tmp_path)
    broker = RestBroker(options=None)  # REST is down too
    logged = _tick_env(monkeypatch, tmp_path, db_path, broker)
    code = live_loop._tick(_config(symbol="QQQ"), "****1234", [], True)
    assert code == 1
    line = json.loads(logged[-1])
    assert line["ok"] is False and sorted(line["manage_only"]["unpriced"]) == sorted([SP, LP, SC, LC])
    assert "held legs unpriced" in line["error"]
    assert "CRITICAL" in capsys.readouterr().err
    assert broker.placed == [], "nothing is ever priced off a missing quote"


def test_a_failed_tick_with_nothing_held_writes_its_reason_to_the_live_log(tmp_path, monkeypatch, capsys):
    """2026-11-02: the failure JSON went to stdout only, which the supervisor discards."""
    db_path = _init_db(tmp_path)
    logged = _tick_env(monkeypatch, tmp_path, db_path, RestBroker())
    code = live_loop._tick(_config(symbol="QQQ"), "****1234", [], True)
    assert code == 1
    assert json.loads(logged[-1])["error"] == "no snapshot: no price"
    assert "no snapshot: no price" in capsys.readouterr().err


def test_an_entry_the_ledger_will_not_save_halts_new_entries(tmp_path, monkeypatch):
    """The order is at the broker; without its row nothing manages it and the window could open a
    second. Shown to fail on the pre-change loop, which returned the failed save unread."""
    from test_live_scaffold import _entry_snapshot

    db_path = _init_db(tmp_path)
    flag = tmp_path / "HALT"
    monkeypatch.setattr(live_loop, "halt_flag_path", lambda: str(flag))
    monkeypatch.setattr(live_loop, "_quiet", lambda message: None)
    attempts = []

    def refuse(row, path):
        attempts.append(row["ic_order_id"])
        return {"ok": False, "error": "database is locked"}

    monkeypatch.setattr(paper, "_save_trade", refuse)
    lines = []
    summary = live_loop.run_once(_config(), _entry_snapshot(), db_path, FakeBroker(), live=True, log=lines.append)
    assert summary["entry"]["entry"] == "placed" and summary["entry"]["save_result"]["ok"] is False
    assert len(attempts) == 2, "one retry before halting"
    assert flag.exists() and "LIVE-XSP-ORD1" in flag.read_text(encoding="utf-8")
    assert any(line.startswith("CRITICAL") and "entries halted" in line for line in lines)
