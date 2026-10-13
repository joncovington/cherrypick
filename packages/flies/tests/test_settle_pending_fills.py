"""A live book never settles on a fill status it has not asked the broker about (2026-10-13).

An internet outage from ~14:53 ET past the close kept the loop from polling. Live position
live-control-7805's resting completion FILLED at the broker at 15:08:02 ET, and the book was later
settled as an uncompleted short vertical with `completion_fill_status = 'pending'` still on the row:
+$225 gross booked instead of +$480. Settlement now confirms every pending entry and completion
first, and refuses the book while any answer is unknown. A confirmed completion is stamped at the
broker's own fill time, not the moment it was noticed."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from test_live_scaffold import DAY, FakeBroker, _open_entry_row, _settle_cfg

from cherrypick.flies import db as dbmod
from cherrypick.flies import fly, live_loop

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
PRINT = 7497.0  # a completed 7490/7495/7500 put fly pays 3.00 here; the bare vertical pays 0


@pytest.fixture
def live_conn(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    return dbmod.connect(dbmod.live_db_path())


def _fills(filled_at_utc: list[str | None]) -> list[dict]:
    """The completion's leg fills: buy the 7500 put at 2.40, sell two 7495 puts at 0.80 -> 0.80 db."""
    legs = [
        {"symbol": "SPXW  P07500000", "action": "Buy to Open", "quantity": "1", "fill_price": "2.40"},
        {"symbol": "SPXW  P07495000", "action": "Sell to Open", "quantity": "2", "fill_price": "0.80"},
    ]
    return [{**leg, "filled_at": at} for leg, at in zip(legs, filled_at_utc, strict=True)]


def _pending_completion(conn, *, pid="E1", day=DAY, entry_time=None, book_id=None):
    dbmod.save_position(
        conn,
        {
            **_open_entry_row(entry_fill_status="filled"),
            "position_id": pid,
            "trade_date": day,
            "book_id": book_id or f"{day}:gex:SPX",
            "completion_order_id": f"ORD-C-{pid}",
            "completion_fill_status": "pending",
            **({"entry_time": entry_time} if entry_time else {}),
        },
    )


def _row(conn, pid="E1"):
    return conn.execute("SELECT * FROM fly_positions WHERE position_id = ?", (pid,)).fetchone()


def _expected_fly_pnl() -> float:
    """What the 1.05-credit vertical completed at a 0.80 debit settles to at PRINT, net."""
    return fly.position_pnl(
        {
            "kind": "fly",
            "side": "put",
            "center": 7495.0,
            "wing_width": 5,
            "net": 0.25,
            "quantity": 1,
            "fees": 3.44 + fly.vertical_open_fee("SPX", 1),
            "entry_mode": "legged",
            "status": "open",
        },
        PRINT,
    )


def test_a_pending_completion_the_broker_filled_settles_as_a_completed_fly(live_conn):
    _pending_completion(live_conn)
    broker = FakeBroker(
        order_statuses={
            "ORD-C-E1": {
                "status": "Filled",
                "price": "0.80",
                "filled": True,
                "fills": _fills(["2026-10-13T19:08:01+00:00", "2026-10-13T19:08:02+00:00"]),
            }
        }
    )
    out = live_loop.run_settle_live(_settle_cfg(), live_conn, cache_path="unused", price=PRINT, broker=broker)
    assert out["ok"], out
    assert "ORD-C-E1" in broker.status_calls
    row = _row(live_conn)
    assert row["status"] == "settled" and row["kind"] == "fly"
    assert row["completion_fill_status"] == "filled" and row["debit"] == pytest.approx(0.80)
    assert row["pnl"] == pytest.approx(_expected_fly_pnl(), abs=0.01)
    vertical = (1.05 * 100) - 3.44  # what the old path booked: the credit kept, as if never completed
    assert row["pnl"] > vertical + 100


def test_a_broker_that_cannot_answer_blocks_settlement(live_conn):
    _pending_completion(live_conn)
    broker = FakeBroker(
        order_statuses={"ORD-C-E1": {"order_id": "ORD-C-E1", "status": None, "error": "ConnectError: down"}}
    )
    out = live_loop.run_settle_live(_settle_cfg(), live_conn, cache_path="unused", price=PRINT, broker=broker)
    assert not out["ok"] and out["reason"] == "pending_fill_unconfirmed"
    assert out["positions"] == ["E1"]
    row = _row(live_conn)
    assert row["status"] == "open" and row["completion_fill_status"] == "pending"
    book = live_conn.execute("SELECT status FROM fly_books").fetchone()
    assert book is None or book["status"] != "settled"
    assert not live_loop.session_officially_settled(live_conn, DAY)


def test_a_still_working_status_blocks_settlement_too(live_conn):
    _pending_completion(live_conn)
    broker = FakeBroker()  # every order reads "Live"
    out = live_loop.run_settle_live(_settle_cfg(), live_conn, cache_path="unused", price=PRINT, broker=broker)
    assert not out["ok"] and out["reason"] == "pending_fill_unconfirmed"
    assert _row(live_conn)["status"] == "open"


def test_no_broker_to_ask_blocks_a_book_with_a_pending_fill(live_conn):
    _pending_completion(live_conn)
    out = live_loop.run_settle_live(_settle_cfg(), live_conn, cache_path="unused", price=PRINT)
    assert not out["ok"] and out["reason"] == "pending_fill_unconfirmed"
    assert _row(live_conn)["status"] == "open"


def test_a_terminal_unfilled_completion_settles_as_the_vertical(live_conn):
    _pending_completion(live_conn)
    broker = FakeBroker(order_statuses={"ORD-C-E1": {"status": "Cancelled", "price": None, "filled": False}})
    out = live_loop.run_settle_live(_settle_cfg(), live_conn, cache_path="unused", price=PRINT, broker=broker)
    assert out["ok"], out
    row = _row(live_conn)
    assert row["status"] == "settled" and row["kind"] == "short_vertical"
    assert row["completion_fill_status"] == "cancelled"


def test_a_pending_entry_is_confirmed_before_settlement(live_conn):
    dbmod.save_position(
        live_conn, {**_open_entry_row(entry_fill_status="filled"), "kind": "fly", "net": 0.25}
    )
    dbmod.save_position(live_conn, {**_open_entry_row(order_id="ORD-E2"), "position_id": "E2"})
    broker = FakeBroker(order_statuses={"ORD-E2": {"status": "Expired", "price": None, "filled": False}})
    out = live_loop.run_settle_live(_settle_cfg(), live_conn, cache_path="unused", price=PRINT, broker=broker)
    assert out["ok"], out
    assert _row(live_conn, "E2")["status"] == "cancelled"
    assert _row(live_conn, "E1")["status"] == "settled"


def test_settle_overdue_asks_the_broker_about_a_pending_completion(live_conn):
    past = "2026-10-06"
    _pending_completion(live_conn, day=past)
    broker = FakeBroker(order_statuses={"ORD-C-E1": {"status": "Filled", "price": "0.80", "filled": True}})
    out = live_loop.settle_overdue(
        _settle_cfg(), live_conn, cache_path="unused", today=DAY, close_fn=lambda s, d: PRINT, broker=broker
    )
    assert [s["session"] for s in out["settled"]] == [past], out
    row = _row(live_conn)
    assert row["kind"] == "fly" and row["pnl"] == pytest.approx(_expected_fly_pnl(), abs=0.01)


def test_settle_overdue_leaves_a_session_the_broker_cannot_confirm(live_conn):
    past = "2026-10-06"
    _pending_completion(live_conn, day=past)
    broker = FakeBroker(order_statuses={"ORD-C-E1": {"status": None, "error": "timeout"}})
    out = live_loop.settle_overdue(
        _settle_cfg(), live_conn, cache_path="unused", today=DAY, close_fn=lambda s, d: PRINT, broker=broker
    )
    assert out["settled"] == []
    assert [(x["session"], x["reason"]) for x in out["left"]] == [(past, "pending_fill_unconfirmed")]
    assert _row(live_conn)["status"] == "open"


def test_the_brokers_fill_time_stamps_completed_at_and_latency(live_conn):
    entered = datetime.fromisoformat(f"{DAY}T14:00:00").replace(tzinfo=ET)
    filled = entered + timedelta(minutes=68, seconds=2)
    _pending_completion(live_conn, entry_time=entered.isoformat(timespec="seconds"))
    utc = filled.astimezone(UTC)
    broker = FakeBroker(
        order_statuses={
            "ORD-C-E1": {
                "status": "Filled",
                "price": "0.80",
                "filled": True,
                "fills": _fills(
                    [(utc - timedelta(seconds=1)).isoformat(), utc.isoformat().replace("+00:00", "Z")]
                ),
            }
        }
    )
    pos = dict(_row(live_conn))
    live_loop._confirm_completion_fill(live_conn, pos, broker, lambda *_: None, spot=7497.0)
    row = _row(live_conn)
    assert row["completed_at"] == filled.isoformat(timespec="seconds")
    assert row["completion_latency_min"] == pytest.approx(68.0)
    decision = live_conn.execute(
        "SELECT first_seen FROM fly_decisions WHERE mode = 'completion' AND reason = 'completed'"
    ).fetchone()
    assert decision["first_seen"] == filled.isoformat(timespec="seconds")


def test_no_broker_fill_time_falls_back_to_now(live_conn):
    from cherrypick.flies import clock

    _pending_completion(live_conn)
    broker = FakeBroker(order_statuses={"ORD-C-E1": {"status": "Filled", "price": "0.80", "filled": True}})
    before = datetime.fromisoformat(clock.now_iso())
    live_loop._confirm_completion_fill(live_conn, dict(_row(live_conn)), broker, lambda *_: None)
    stamped = datetime.fromisoformat(_row(live_conn)["completed_at"])
    assert stamped >= before
