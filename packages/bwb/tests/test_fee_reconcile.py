"""Reconciliation of the live ledger against real broker transactions: pure over a transcribed
chain, exact matching, the modeled snapshot taken once, and an unmatched row left untouched."""

from __future__ import annotations

import pytest
from conftest import occ

from cherrypick.bwb import book as bookmod
from cherrypick.bwb import db, fee_reconcile

EXP = "2026-09-18"


def _leg(role, strike, action):
    return {
        "leg_role": role,
        "occ_symbol": occ("SPXW", EXP, strike),
        "streamer_symbol": f".SPXW{strike:g}",
        "expiration": EXP,
        "strike": strike,
        "option_type": "put",
        "action": action,
        "bid": 1.0,
        "ask": 1.2,
        "mid": 1.1,
    }


def _plan(credit=0.9, body=7600.0):
    return {
        "symbol": "SPX",
        "spot": 7700.0,
        "expiration": EXP,
        "dte": 2,
        "atm_strike": 7700.0,
        "expected_move": 100.0,
        "body_strike": body,
        "near_strike": body + 5,
        "far_strike": body - 10,
        "body_mid": 1.5,
        "near_mid": 1.9,
        "far_mid": 0.2,
        "credit": credit,
        "narrow_width": 5.0,
        "wide_width": 10.0,
        "max_loss_up": 0.0,
        "max_loss_down": 5.0 - credit,
        "max_loss": 5.0 - credit,
        "legs": [
            _leg("near_long", body + 5, "Buy to Open"),
            _leg("body_short_1", body, "Sell to Open"),
            _leg("body_short_2", body, "Sell to Open"),
            _leg("far_long", body - 10, "Buy to Open"),
        ],
    }


def _txn(kind, *, order_id=None, symbol=None, value, fees=0.0, date=EXP):
    return {
        "transaction_type": kind,
        "order_id": order_id,
        "symbol": symbol,
        "value": str(value),
        "commission": str(-fees) if fees else None,
        "clearing_fees": None,
        "regulatory_fees": None,
        "proprietary_index_option_fees": None,
        "transaction_date": date,
        "quantity": 1,
    }


@pytest.fixture
def settled(managed_home, config):
    conn = db.connect(db.live_db_path())
    pid = "SPX:control:2026-09-16:1"
    bookmod.enter_position(
        conn,
        _plan(),
        config,
        "control",
        entry_session="2026-09-16",
        advice_params=None,
        position_id_override=pid,
        extra={
            "entry_order_id": "ORD1",
            "entry_fill_status": "filled",
            "fees": 6.89,
            "fees_source": "broker_estimate",
        },
    )
    bookmod.settle_expiring_legs(
        conn, EXP, 7595.0, config, symbol="SPX"
    )  # through the body, above the far wing
    row = dict(conn.execute("SELECT * FROM bwb_positions WHERE position_id = ?", (pid,)).fetchone())
    assert row["status"] == "closed" and row["itm_settlements"] == 2
    return conn, pid, row


def _row(conn, pid):
    return dict(conn.execute("SELECT * FROM bwb_positions WHERE position_id = ?", (pid,)).fetchone())


def test_pending_reconciliation_lists_settled_expirations_strictly_before_today(settled):
    conn, pid, _ = settled
    assert fee_reconcile.pending_reconciliation(conn, "SPX", today=EXP) == []  # fees post next day
    assert fee_reconcile.pending_reconciliation(conn, "SPX", today="2026-09-21") == [EXP]
    assert fee_reconcile.pending_reconciliation(conn, "SPX", today="2026-10-21") == []  # outside the lookback


def test_reconcile_replaces_modeled_costs_with_real_ones_and_snapshots_the_model_once(settled):
    conn, pid, before = settled
    body = occ("SPXW", EXP, 7600.0)
    near = occ("SPXW", EXP, 7605.0)
    txns = [
        _txn("Trade", order_id="ORD1", symbol=near, value=-190.0, fees=1.6),
        _txn("Trade", order_id="ORD1", symbol=body, value=300.0, fees=3.29),
        _txn("Trade", order_id="ORD1", symbol=occ("SPXW", EXP, 7590.0), value=-20.0, fees=2.0),
        _txn("Receive Deliver", symbol=near, value=1000.0, fees=5.0),
        _txn("Receive Deliver", symbol=body, value=-1000.0, fees=5.0),
    ]
    out = fee_reconcile.reconcile_date(conn, EXP, "SPX", txns, log=lambda *_: None)
    assert out["reconciled"] == [pid] and out["unmatched"] == []
    row = _row(conn, pid)
    assert row["fees_source"] == "reconciled" and row["reconciled_at"]
    assert row["modeled_fees"] == pytest.approx(before["fees"])
    assert row["modeled_gross_pnl"] == pytest.approx(before["gross_pnl"])
    assert row["fees"] == pytest.approx(1.6 + 3.29 + 2.0 + 5.0 + 5.0)
    assert row["gross_pnl"] == pytest.approx(-190 + 300 - 20 + 1000 - 1000)
    assert out["fee_variance"] == []  # $5 per settlement event, as modeled
    # a second run finds nothing to do and the snapshot is not overwritten
    again = fee_reconcile.reconcile_date(conn, EXP, "SPX", txns, log=lambda *_: None)
    assert again["reconciled"] == [] and again["unmatched"] == []
    assert _row(conn, pid)["modeled_fees"] == pytest.approx(before["fees"])


def test_a_position_without_matching_broker_data_is_marked_unmatched_and_left_alone(settled):
    conn, pid, before = settled
    txns = [_txn("Trade", order_id="OTHER", value=1.0)]
    out = fee_reconcile.reconcile_date(conn, EXP, "SPX", txns, log=lambda *_: None)
    assert out["unmatched"] == [pid]
    row = _row(conn, pid)
    assert row["fees_source"] == "unmatched" and row["fees"] == pytest.approx(before["fees"])
    assert row["gross_pnl"] == pytest.approx(before["gross_pnl"]) and row["reconciled_at"] is None


def test_a_settlement_fee_that_differs_from_the_model_is_reported_per_symbol(settled):
    conn, pid, _ = settled
    body = occ("SPXW", EXP, 7600.0)
    txns = [
        _txn("Trade", order_id="ORD1", symbol=body, value=90.0, fees=6.89),
        _txn("Receive Deliver", symbol=body, value=-1000.0, fees=10.0),  # per contract, not per event
    ]
    out = fee_reconcile.reconcile_date(conn, EXP, "SPX", txns, log=lambda *_: None)
    assert out["reconciled"] == [pid]
    assert out["fee_variance"][0]["real_fee"] == 10.0 and out["fee_variance"][0]["modeled_fee"] == 5.0


def test_settlement_charges_the_event_fee_per_distinct_symbol_not_per_leg_row(settled):
    """The body is two leg rows of ONE contract. At 7595 the near wing and the body finish ITM:
    two symbols, three rows -- two $5 events, not three. Counted rows until 2026-09-18."""
    conn, pid, row = settled
    assert row["itm_settlements"] == 2
    assert row["fees"] == pytest.approx(6.89 + 2 * 5.0)
