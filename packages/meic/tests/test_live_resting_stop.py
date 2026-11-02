"""The stop-limit resting at the broker on each side of a live IC (2026-11-02).

A quote outage held a live put side unmanaged for 79 minutes; it was stopped at $7.15 against its
$3.60 trigger. These pin the resting stop's lifecycle: placed once the entry fill is confirmed,
booked through the one close-fill path when the broker fills it, re-placed when it dies, and
cancelled -- with the cancel confirmed -- before the loop sends a close or settles the side, so a
side never has two closes working. Offline; the broker is a fake.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_live_scaffold import (  # noqa: E402
    LC,
    LP,
    OCC,
    SC,
    SP,
    FakeBroker,
    _config,
    _entry_snapshot,
    _init_db,
    _open_trade_row,
    _stopping_snapshot,
)

from cherrypick.meic import live_loop, live_orders, paper  # noqa: E402

FILLED_ENTRY = {"ORD1": {"status": "Filled", "price": "-1.37", "filled": True}}


def _on(**over):
    return _config(resting_stop={"enabled": True, "limit_offset_frac": 0.10}, **over)


def _quiet(*_):
    return None


class RefusingBroker(FakeBroker):
    """A cancel the broker refuses (the order filled, or the call failed)."""

    def cancel(self, order_id):
        self.cancelled.append(order_id)
        return {"ok": False, "order_id": order_id, "error": "order is not cancellable"}


# --------------------------------------------------------------------------- the order
def test_resting_stop_spec_is_a_day_stop_limit_buying_the_side_back():
    row = {**_open_trade_row("XSP"), "net_credit": 1.80}
    spec = live_orders.resting_stop_spec(row, "put", 3.60, 0.36)
    assert spec["order_type"] == "Stop Limit" and spec["time_in_force"] == "Day"
    # $3.96 ceilinged to the nickel: the cushion is never rounded back below what was asked.
    assert spec["stop_trigger"] == 3.60 and spec["price"] == 4.00 and spec["price_effect"] == "debit"
    assert [(leg["symbol"], leg["action"]) for leg in spec["legs"]] == [
        (OCC[SP], "buy to close"),
        (OCC[LP], "sell to close"),
    ]


def test_resting_stop_limit_never_asks_more_than_the_spread_can_be_worth():
    row = _open_trade_row("XSP")  # 5 wide
    spec = live_orders.resting_stop_spec(row, "call", 4.80, 1.00)
    assert spec["price"] == 5.0 and spec["stop_trigger"] == 4.80
    assert [leg["symbol"] for leg in spec["legs"]] == [OCC[SC], OCC[LC]]
    with pytest.raises(ValueError):
        live_orders.resting_stop_spec(row, "call", 5.0, 1.00)  # no room for a limit above the stop


# --------------------------------------------------------------------------- the lifecycle
def _placed_and_filled(tmp_path, cfg, broker):
    db_path = _init_db(tmp_path)
    live_loop.run_once(cfg, _entry_snapshot(), db_path, broker, live=True, log=_quiet)
    summary = live_loop.run_once(cfg, _entry_snapshot(), db_path, broker, live=True, log=_quiet)
    return db_path, summary


def test_a_confirmed_entry_gets_a_resting_stop_on_each_side(tmp_path):
    """Shown to fail without `_place_resting_stops` in run_once: nothing rests at the broker."""
    cfg = _on()
    broker = FakeBroker(statuses=dict(FILLED_ENTRY))
    db_path, summary = _placed_and_filled(tmp_path, cfg, broker)
    assert summary["entries_confirmed"] == 1 and summary["resting_placed"] == 2
    row = live_loop._trade_row("LIVE-XSP-ORD1", db_path)
    trigger = live_loop.resting_stop_trigger(row, paper.live_params(cfg))
    assert trigger == pytest.approx(float(row["stop_trigger_current"]) * 1.37)
    stops = [p["spec"] for p in broker.placed[1:]]
    assert [s["order_type"] for s in stops] == ["Stop Limit", "Stop Limit"]
    assert all(s["stop_trigger"] == live_orders.tick_ceil(trigger) for s in stops)
    assert row["put_resting_stop_order_id"] == "ORD2" and row["put_resting_stop_status"] == "working"
    assert row["call_resting_stop_order_id"] == "ORD3" and row["call_resting_stop_status"] == "working"
    # The next tick places nothing more: both stops are still working.
    again = live_loop.run_once(cfg, _entry_snapshot(), db_path, broker, live=True, log=_quiet)
    assert again["resting_placed"] == 0 and again["resting_working"] == 2 and len(broker.placed) == 3


def test_resting_stops_are_off_unless_switched_on(tmp_path):
    broker = FakeBroker(statuses=dict(FILLED_ENTRY))
    db_path, summary = _placed_and_filled(tmp_path, _config(), broker)
    assert summary["entries_confirmed"] == 1 and "resting_placed" in summary
    assert summary["resting_placed"] == 0 and len(broker.placed) == 1


def test_a_resting_stop_the_broker_filled_is_booked_the_same_tick(tmp_path):
    """The side is bought back at the broker; the loop must book it, and never send a close too."""
    cfg = _on()
    broker = FakeBroker(statuses=dict(FILLED_ENTRY))
    db_path, _ = _placed_and_filled(tmp_path, cfg, broker)
    broker._statuses["ORD2"] = {"status": "Filled", "price": "-2.95"}
    summary = live_loop.run_once(cfg, _stopping_snapshot_xsp(), db_path, broker, live=True, log=_quiet)
    assert summary["resting_filled"] == 1 and summary["closes_filled"] == 1
    row = live_loop._trade_row("LIVE-XSP-ORD1", db_path)
    assert row["status"] == "partial" and row["put_stop_cost"] == 2.95
    assert row["put_resting_stop_status"] == "filled" and row["put_stop_fill_status"] == "filled"
    assert not [p for p in broker.placed[3:] if p["spec"]["order_type"] == "Limit"], "no second close"


def _stopping_snapshot_xsp():
    snap = _stopping_snapshot()
    snap.update(symbol="XSP")
    return snap


def test_a_dead_resting_stop_is_placed_again(tmp_path):
    cfg = _on()
    broker = FakeBroker(statuses=dict(FILLED_ENTRY))
    db_path, _ = _placed_and_filled(tmp_path, cfg, broker)
    broker._statuses["ORD3"] = {"status": "Rejected"}
    summary = live_loop.run_once(cfg, _entry_snapshot(), db_path, broker, live=True, log=_quiet)
    assert summary["resting_dead"] == 1 and summary["resting_placed"] == 1
    row = live_loop._trade_row("LIVE-XSP-ORD1", db_path)
    assert row["call_resting_stop_order_id"] == "ORD4" and row["call_resting_stop_status"] == "working"


def test_the_loops_own_stop_cancels_the_resting_stop_before_its_close(tmp_path):
    """Shown to fail without `_clear_resting_stop` on the close path: two closes on one side."""
    cfg = _on(symbol="QQQ")
    db_path = _init_db(tmp_path)
    paper._save_trade(_open_trade_row("QQQ"), db_path)
    paper._update_trade(
        "OPEN1", {"put_resting_stop_order_id": "R1", "put_resting_stop_status": "working"}, db_path
    )
    broker = FakeBroker()
    summary = live_loop.run_once(cfg, _stopping_snapshot(), db_path, broker, live=True, log=_quiet)
    assert broker.cancelled[0] == "R1"
    assert summary["closing"] == 1
    row = live_loop._trade_row("OPEN1", db_path)
    assert row["put_resting_stop_status"] == "cancelled" and row["put_stop_fill_status"] == "pending"


def test_a_resting_stop_that_will_not_cancel_blocks_the_loops_close(tmp_path):
    cfg = _on(symbol="QQQ")
    db_path = _init_db(tmp_path)
    paper._save_trade(_open_trade_row("QQQ"), db_path)
    paper._update_trade(
        "OPEN1", {"put_resting_stop_order_id": "R1", "put_resting_stop_status": "working"}, db_path
    )
    broker = RefusingBroker()  # R1 reads Live: the cancel failed but the stop is still working
    lines = []
    live_loop.run_once(cfg, _stopping_snapshot(), db_path, broker, live=True, log=lines.append)
    assert not [p for p in broker.placed if p["spec"].get("order_type") == "Limit"], "no close on top"
    assert live_loop._trade_row("OPEN1", db_path)["put_stop_fill_status"] is None
    assert any("could not be cancelled" in line for line in lines)


def test_settlement_waits_for_a_resting_stop_that_filled_at_the_close(tmp_path):
    """A side the broker bought back at 15:59 must not also be settled at expiry value. XSP: a
    cash-settled symbol settles at 16:01, where QQQ would be force-closed instead."""
    cfg = _on()
    db_path = _init_db(tmp_path)
    paper._save_trade(_open_trade_row("XSP"), db_path)
    paper._update_trade(
        "OPEN1", {"put_resting_stop_order_id": "R1", "put_resting_stop_status": "working"}, db_path
    )
    broker = RefusingBroker(statuses={"R1": {"status": "Filled", "price": "-2.95"}})
    snap = _entry_snapshot(now_et="16:01", candidates=[])
    counts = live_loop._manage_open_trades(
        "XSP", snap, paper.live_params(cfg), db_path, broker, live=True, log=_quiet,
        settle_price=lambda s: (590.0, "tastytrade_close"),
    )  # fmt: skip
    assert counts["expired"] == 0 and counts["held"] == 1
    row = live_loop._trade_row("OPEN1", db_path)
    assert row["status"] == "open", "not settled yet"
    assert row["put_stop_fill_status"] == "pending" and row["put_resting_stop_status"] == "filled"
    # The next tick's confirmation books the stop at its fill, and only then does the IC settle.
    fills = live_loop._confirm_fills("XSP", snap, db_path, broker, log=_quiet)
    assert fills["closes_filled"] == 1
    assert live_loop._trade_row("OPEN1", db_path)["put_stop_cost"] == 2.95


def test_the_orphan_sweep_knows_a_resting_stop_as_the_loops_own(tmp_path):
    cfg = _on()
    broker = FakeBroker(statuses=dict(FILLED_ENTRY))
    db_path, _ = _placed_and_filled(tmp_path, cfg, broker)
    broker.working = [
        {"order_id": "ORD2", "underlying_symbol": "XSP"},
        {"order_id": "ORD3", "underlying_symbol": "XSP"},
    ]
    assert {"ORD2", "ORD3"} <= set(live_loop._known_order_ids(db_path))
    summary = live_loop.run_once(cfg, _entry_snapshot(), db_path, broker, live=True, log=_quiet)
    assert summary["orphans"] == 0


def test_a_dry_run_preflights_the_resting_stop_and_records_nothing(tmp_path):
    cfg = _on(symbol="QQQ")
    db_path = _init_db(tmp_path)
    paper._save_trade(_open_trade_row("QQQ"), db_path)
    broker = FakeBroker()
    snap = _entry_snapshot(symbol="QQQ", now_et="11:00", candidates=[])
    live_loop.run_once(cfg, snap, db_path, broker, live=False, log=_quiet)
    assert [p["live"] for p in broker.placed if p["spec"]["order_type"] == "Stop Limit"] == [False, False]
    assert live_loop._trade_row("OPEN1", db_path)["put_resting_stop_order_id"] is None
