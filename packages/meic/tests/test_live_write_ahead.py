"""The write-ahead entry intent (2026-11-02): the row an entry needs, written under the order's own
identifier BEFORE the order goes out, so a process that dies between the submit and the save leaves
something the next tick can find the order by. Offline; the broker is a fake."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_live_scaffold import DAY, FakeBroker, _config, _entry_snapshot, _init_db  # noqa: E402

from cherrypick.meic import live_loop  # noqa: E402


def _quiet(*_):
    return None


class Broker(FakeBroker):
    """The fake with today's orders (the recovery read) and a hook on the submit."""

    def __init__(self, today=None, on_place=None, **kw):
        super().__init__(**kw)
        self.today = today
        self.on_place = on_place

    def place(self, spec, live):
        if self.on_place:
            self.on_place(spec)
        return super().place(spec, live)

    def orders_today(self):
        if isinstance(self.today, Exception):
            raise self.today
        return list(self.today or [])


def _intents(db_path):
    path = live_loop._intents_path(db_path)
    return json.loads(Path(path).read_text(encoding="utf-8")) if os.path.exists(path) else {}


def _tick(db_path, broker):
    return live_loop.run_once(_config(), _entry_snapshot(), db_path, broker, live=True, log=_quiet)


class Died(Exception):
    """The process ending between the broker accepting the order and the row being saved."""


def test_the_intent_is_written_before_the_order_goes_out_and_cleared_once_saved(tmp_path):
    db_path = _init_db(tmp_path)
    seen = {}

    def at_submit(spec):
        seen["ext"] = spec["external_identifier"]
        seen["intents"] = _intents(db_path)

    out = _tick(db_path, Broker(on_place=at_submit))
    assert out["entry"]["entry"] == "placed"
    assert seen["ext"].startswith("meic-") and list(seen["intents"]) == [seen["ext"]]
    assert (
        seen["intents"][seen["ext"]]["day"] == DAY
        and seen["intents"][seen["ext"]]["row"]["status"] == "pending"
    )
    assert _intents(db_path) == {}


def test_an_intent_that_cannot_be_written_sends_no_order(tmp_path, monkeypatch):
    db_path = _init_db(tmp_path)
    monkeypatch.setattr(live_loop, "_remember_intent", lambda *a, **k: False)
    broker = Broker()
    out = _tick(db_path, broker)
    assert out["entry"]["reason"] == "entry_intent_unwritten" and broker.placed == []


def test_an_entry_sent_and_never_recorded_is_found_and_recorded_next_tick(tmp_path, monkeypatch):
    """Shown to fail without the intent: a filled entry the ledger never saw had no stop, no
    settlement, and `entries_today` read 0, so the window could open a second one."""
    db_path = _init_db(tmp_path)

    def dies(*a, **k):
        raise Died

    monkeypatch.setattr(live_loop, "_save_placed_entry", dies)
    with pytest.raises(Died):
        _tick(db_path, Broker())
    [ext] = list(_intents(db_path))
    assert live_loop._trade_row("LIVE-XSP-ORD1", db_path) is None
    monkeypatch.undo()

    broker = Broker(today=[{"order_id": "ORD1", "status": "Filled", "external_identifier": ext}])
    out = _tick(db_path, broker)
    assert out["intents_recovered"] == 1 and _intents(db_path) == {}
    row = live_loop._trade_row("LIVE-XSP-ORD1", db_path)
    assert row is not None and row["put_spread_entry_order_id"] == "ORD1"
    assert "blocked" not in out["entry"] or not any("orphan" in b for b in out["entry"]["blocked"])
    assert len(broker.placed) == 0, "no second entry while the first is being recorded"


def test_an_intent_with_no_order_at_the_broker_is_dropped(tmp_path):
    db_path = _init_db(tmp_path)
    live_loop._remember_intent(db_path, "meic-gone", {"symbol": "XSP", "day": DAY, "row": {}})
    out = live_loop._resolve_entry_intents(db_path, Broker(today=[]), DAY, _quiet)
    assert out == {"intents_recovered": 0, "intents_dropped": 1} and _intents(db_path) == {}


def test_an_unreadable_broker_blocks_entries_and_keeps_the_intent(tmp_path):
    db_path = _init_db(tmp_path)
    live_loop._remember_intent(db_path, "meic-x", {"symbol": "XSP", "day": DAY, "row": {}})
    out = live_loop._resolve_entry_intents(db_path, Broker(today=RuntimeError("502")), DAY, _quiet)
    assert "today's orders unreadable" in out["intents_block"] and list(_intents(db_path)) == ["meic-x"]


def test_a_past_sessions_intent_blocks_entries_for_a_person(tmp_path):
    db_path = _init_db(tmp_path)
    live_loop._remember_intent(db_path, "meic-old", {"symbol": "XSP", "day": "2026-07-07", "row": {}})
    out = live_loop._resolve_entry_intents(db_path, Broker(today=[]), DAY, _quiet)
    assert "past session" in out["intents_block"] and list(_intents(db_path)) == ["meic-old"]


def test_a_rejected_order_clears_its_intent_and_an_uncertain_one_keeps_it(tmp_path):
    db_path = _init_db(tmp_path)

    class Refuses(Broker):
        def __init__(self, result):
            super().__init__()
            self.result = result

        def place(self, spec, live):
            self.placed.append(spec)
            return self.result

    _tick(db_path, Refuses({"ok": False, "error": "rejected"}))
    assert _intents(db_path) == {}
    _tick(db_path, Refuses({"ok": False, "uncertain": True, "error": "timeout"}))
    assert len(_intents(db_path)) == 1


def test_an_unreconciled_intent_keeps_an_unarmed_tick_running_and_blocks_arming(tmp_path):
    db_path = _init_db(tmp_path)
    assert not live_loop.holds_today(db_path, DAY)
    live_loop._remember_intent(db_path, "meic-x", {"symbol": "XSP", "day": DAY, "row": {}})
    assert live_loop.holds_today(db_path, DAY)
    verdict = live_loop.arm_verdict({"supervisor_fresh": True, "entry_intents": ["meic-x"]})
    assert verdict["blockers"] == ["an entry sent but not yet reconciled: ['meic-x']"]
