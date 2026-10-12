"""A live position books the broker's fill price, not the order's limit (2026-10-12), and the
backfill that brings earlier rows into line."""

import pytest
from test_live_scaffold import FakeBroker, _loop_cfg, _open_entry_row, _snapshot

from cherrypick.flies import db as dbmod
from cherrypick.flies import fill_facts, live_loop
from cherrypick.flies import fill_model as fm


@pytest.fixture
def live_conn(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    return dbmod.connect(dbmod.live_db_path())


def _quiet(*_):
    return None


def legs(sell, buy):
    return [
        {"action": "Sell to Open", "quantity": 1, "fill_price": str(sell)},
        {"action": "Buy to Open", "quantity": 1, "fill_price": str(buy)},
    ]


# --------------------------------------------------------------------------- the rule
def test_an_improvement_is_booked_and_a_missing_or_worse_price_keeps_the_limit():
    assert fm.booked_price(legs(2.60, 0.10), 1, fm.ENTRY, 2.45) == (2.5, "broker")
    assert fm.booked_price(None, 1, fm.ENTRY, 2.45) == (2.45, "limit")
    # A credit order cannot fill under its limit: a leg sum saying so is a bad read, not a fill.
    assert fm.booked_price(legs(2.40, 0.10), 1, fm.ENTRY, 2.45) == (2.45, "limit")
    # A completion is a debit: better is lower.
    completion = [
        {"action": "Buy to Open", "quantity": 1, "fill_price": "2.50"},
        {"action": "Sell to Open", "quantity": 1, "fill_price": "0.40"},
    ]
    assert fm.booked_price(completion, 1, fm.COMPLETION, 2.15) == (2.1, "broker")
    assert fm.booked_price(completion, 1, fm.COMPLETION, 2.05) == (2.05, "limit")


def test_the_entry_books_the_broker_price_and_measures_slippage_from_it(live_conn):
    """Shown to fail without the change: the row booked the status's 1.15 limit."""
    dbmod.save_position(live_conn, {**_open_entry_row(), "entry_mid_at_submit": 1.20})
    status = {"status": "Filled", "price": "1.15", "filled": True, "fills": legs(1.40, 0.20)}
    broker = FakeBroker(order_statuses={"ORD-E1": status})
    live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=_quiet)
    row = live_conn.execute("SELECT * FROM fly_positions WHERE position_id = 'E1'").fetchone()
    assert row["credit"] == pytest.approx(1.20) and row["net"] == pytest.approx(1.20)
    assert row["slippage_dollars"] == pytest.approx(0.0)
    order = dbmod.live_order(live_conn, "ORD-E1")
    assert order["limit_price"] == pytest.approx(1.05) and order["broker_fill_price"] == pytest.approx(1.20)


def test_without_leg_fills_the_entry_books_the_limit_as_before(live_conn):
    dbmod.save_position(live_conn, _open_entry_row())
    broker = FakeBroker(order_statuses={"ORD-E1": {"status": "Filled", "price": "1.15", "filled": True}})
    live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=_quiet)
    row = live_conn.execute("SELECT * FROM fly_positions WHERE position_id = 'E1'").fetchone()
    assert row["credit"] == pytest.approx(1.15)


# --------------------------------------------------------------------------- the backfill
def _settled(position_id, *, credit, net, pnl, reconciled, kind="short_vertical", debit=None, floor=None):
    return {
        "position_id": position_id,
        "book_id": "2026-10-12:control:SPX",
        "trade_date": "2026-10-12",
        "arm": "control",
        "entry_mode": "legged",
        "symbol": "SPX",
        "kind": kind,
        "side": "put",
        "center": 7800.0,
        "wing_width": 5,
        "quantity": 1,
        "credit": credit,
        "debit": debit,
        "net": net,
        "fees": 3.44,
        "floor_dollars": floor,
        "expiry_payoff": 0.0,
        "gross_pnl": round((net + 0.0) * 100, 2),
        "pnl": pnl,
        "status": "settled",
        "entry_mid_at_submit": credit + 0.05,
        "broker_reconciliation_status": "reconciled" if reconciled else None,
        "entry_order_id": f"O-{position_id}",
        "entry_fill_status": "filled",
    }


def _order(position_id, limit, broker):
    return {
        "order_id": f"O-{position_id}",
        "trade_date": "2026-10-12",
        "position_id": position_id,
        "leg": "entry",
        "side": "put",
        "center": 7800.0,
        "wing_width": 5,
        "quantity": 1,
        "limit_price": limit,
        "outcome": "filled",
        "broker_fill_price": broker,
        "source": "live",
    }


def test_the_backfill_moves_only_the_price_columns_on_a_reconciled_row(live_conn):
    dbmod.save_position(live_conn, _settled("R", credit=2.40, net=2.50, pnl=246.56, reconciled=True))
    dbmod.save_live_order(live_conn, _order("R", 2.40, 2.50))
    plan = fill_facts.book_prices_plan(live_conn, fills={"O-R": {"cash": 250.0}})
    (c,) = plan["changes"]
    assert c["after"] == {"credit": 2.5, "slippage_dollars": -5.0}
    assert c["net_agrees_with_broker_cash"] is True and not plan["refused"]


def test_the_backfill_carries_an_unreconciled_row_through_to_pnl(live_conn):
    dbmod.save_position(live_conn, _settled("U", credit=1.45, net=1.45, pnl=141.56, reconciled=False))
    dbmod.save_live_order(live_conn, _order("U", 1.45, 1.50))
    plan = fill_facts.book_prices_plan(live_conn, fills={"O-U": {"cash": 150.0}})
    (c,) = plan["changes"]
    assert c["after"]["credit"] == 1.5 and c["after"]["net"] == 1.5
    assert c["after"]["gross_pnl"] == 150.0 and c["after"]["pnl"] == 146.56


def test_a_change_the_transactions_do_not_confirm_is_refused(live_conn):
    """Shown to fail without the check: a broker_fill_price the transactions disagree with (or have
    no record of) would be written."""
    dbmod.save_position(live_conn, _settled("X", credit=1.45, net=1.45, pnl=141.56, reconciled=False))
    dbmod.save_live_order(live_conn, _order("X", 1.45, 1.50))
    for fills in ({"O-X": {"cash": 145.0}}, {}):
        plan = fill_facts.book_prices_plan(live_conn, fills=fills)
        assert not plan["changes"] and plan["refused"][0]["order_id"] == "O-X"


def test_a_completed_floor_shifts_by_the_credit_and_never_double_reserves(live_conn):
    """A settled row's fees already hold its settlement charge; recomputing the floor from them
    reserved it twice (caught on the 10-12 dry run: +$3.11 -> -$1.89 for a $5 better credit)."""
    row = _settled(
        "F", credit=2.25, net=0.25, pnl=232.11, reconciled=False, kind="fly", debit=2.0, floor=3.11
    )
    dbmod.save_position(live_conn, {**row, "fees": 16.89, "expiry_payoff": 2.24})
    dbmod.save_live_order(live_conn, _order("F", 2.25, 2.30))
    (c,) = fill_facts.book_prices_plan(live_conn, fills=None)["changes"]
    assert c["after"]["net"] == pytest.approx(0.30)
    assert c["after"]["floor_dollars"] == pytest.approx(8.11) and c["after"]["risk_free"] == 1
    assert c["after"]["pnl"] == pytest.approx(237.11)
