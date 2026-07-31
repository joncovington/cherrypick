"""Tests for fee_reconcile.py, built from the real 2026-07-30 XSP 744-center fly's transcribed
broker transactions (the same session used to discover the modeled-vs-real P&L gap this module
exists to close) — same spirit as tests/fixtures/books.json's real order chains.
"""

from __future__ import annotations

import pytest

import db as dbmod
import fee_reconcile

TRADE_DATE = "2026-07-30"
SYMBOL = "XSP"


def _trade_txn(order_id, value, fees=(-0.02, -0.1, -1.0, 0.0)):
    reg, clr, comm, prop = fees
    return {
        "transaction_type": "Trade",
        "order_id": order_id,
        "value": str(value),
        "regulatory_fees": str(reg),
        "clearing_fees": str(clr),
        "commission": str(comm),
        "proprietary_index_option_fees": str(prop),
    }


def _settlement_txn(symbol, value, clearing_fee=None):
    return {
        "transaction_type": "Receive Deliver",
        "transaction_date": TRADE_DATE,
        "symbol": symbol,
        "value": str(value),
        "regulatory_fees": None,
        "clearing_fees": str(clearing_fee) if clearing_fee is not None else None,
        "commission": None,
        "proprietary_index_option_fees": None,
    }


# The 744-center fly's real order chain and settlement (order3=entry, order4=completion).
_744_TRANSACTIONS = [
    _trade_txn("489436397", 190.0),  # Sell to Open 744P @1.90
    _trade_txn("489436397", -118.0),  # Buy to Open 743P @1.18
    _trade_txn("489436686", -97.0),  # Buy to Open 745P @0.97
    _trade_txn("489436686", 42.0),  # Sell to Open 744P @0.42
    _settlement_txn("XSP   260730P00743000", 0.0),  # expired worthless
    _settlement_txn("XSP   260730P00744000", 0.0),  # assignment removal (no fee row)
    _settlement_txn("XSP   260730P00744000", -48.0, clearing_fee=-5.0),  # cash-settled assignment
    _settlement_txn("XSP   260730P00745000", 0.0),  # exercise removal (no fee row)
    _settlement_txn("XSP   260730P00745000", 124.0, clearing_fee=-5.0),  # cash-settled exercise
]


@pytest.fixture
def live_conn(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    return dbmod.connect(dbmod.live_db_path())


def _save_744_position(conn, **overrides):
    row = {
        "position_id": "744-fly",
        "trade_date": TRADE_DATE,
        "arm": "gex",
        "symbol": SYMBOL,
        "kind": "fly",
        "side": "put",
        "center": 744,
        "wing_width": 1,
        "quantity": 1,
        "net": 0.10,  # modeled: 0.65 credit - 0.55 debit
        "fees": 19.49,  # modeled: vertical_open_fee x2 + flat $5/contract assignment estimate
        "gross_pnl": 86.0,
        "pnl": 66.51,
        "expiry_payoff": 0.85,
        "status": "settled",
        "entry_order_id": "489436397",
        "completion_order_id": "489436686",
    }
    row.update(overrides)
    row.setdefault("book_id", f"{row['trade_date']}:{row['arm']}:{row['symbol']}")
    dbmod.save_position(conn, row)
    dbmod.save_book(
        conn,
        {
            "book_id": row["book_id"],
            "trade_date": row["trade_date"],
            "arm": row["arm"],
            "symbol": row["symbol"],
            "pnl": row["pnl"],
            "fees": row["fees"],
            "status": "settled",
        },
    )
    return row


def test_reconcile_date_recomputes_pnl_from_real_broker_cash_flow(live_conn):
    _save_744_position(live_conn)
    result = fee_reconcile.reconcile_date(live_conn, TRADE_DATE, SYMBOL, _744_TRANSACTIONS)

    assert result["reconciled"] == ["744-fly"]
    assert result["unmatched"] == []

    row = live_conn.execute("SELECT * FROM fly_positions WHERE position_id = '744-fly'").fetchone()
    # Real cash: trades net +$17.00, settlement net +$76.00, fees $14.48 -> gross $93.00, pnl $78.52.
    assert row["net"] == pytest.approx(0.17, abs=1e-4)
    assert row["expiry_payoff"] == pytest.approx(0.76, abs=1e-4)
    assert row["fees"] == pytest.approx(14.48, abs=1e-2)
    assert row["gross_pnl"] == pytest.approx(93.0, abs=1e-2)
    assert row["pnl"] == pytest.approx(78.52, abs=1e-2)
    assert row["broker_reconciliation_status"] == "reconciled"
    assert row["broker_reconciled_at"] is not None
    # The original modeled figures are snapshotted, not lost.
    assert row["modeled_pnl"] == pytest.approx(66.51)
    assert row["modeled_fees"] == pytest.approx(19.49)
    assert row["modeled_gross_pnl"] == pytest.approx(86.0)

    variance = result["variance"][0]
    assert variance["position_id"] == "744-fly"
    assert variance["delta"] == pytest.approx(12.01, abs=1e-2)

    book = live_conn.execute("SELECT * FROM fly_books WHERE book_id = ?", (row["book_id"],)).fetchone()
    assert book["pnl"] == pytest.approx(78.52, abs=1e-2)
    assert book["broker_reconciliation_status"] == "reconciled"


def test_reconcile_date_is_idempotent(live_conn):
    _save_744_position(live_conn)
    fee_reconcile.reconcile_date(live_conn, TRADE_DATE, SYMBOL, _744_TRANSACTIONS)
    first = dict(live_conn.execute("SELECT * FROM fly_positions WHERE position_id = '744-fly'").fetchone())

    # Second call: the position no longer matches the "unreconciled" query (broker_reconciled_at
    # is set), so it's a no-op -- not a second overwrite of modeled_* or a double fee count.
    second_result = fee_reconcile.reconcile_date(live_conn, TRADE_DATE, SYMBOL, _744_TRANSACTIONS)
    second = dict(live_conn.execute("SELECT * FROM fly_positions WHERE position_id = '744-fly'").fetchone())

    assert second_result["reconciled"] == []
    assert second_result["unmatched"] == []
    assert second == first


def test_reconcile_date_leaves_unmatched_position_untouched(live_conn):
    row = _save_744_position(live_conn, position_id="orphan-fly", entry_order_id="999999", completion_order_id="888888")
    result = fee_reconcile.reconcile_date(live_conn, TRADE_DATE, SYMBOL, _744_TRANSACTIONS)

    assert result["reconciled"] == []
    assert result["unmatched"] == ["orphan-fly"]

    stored = live_conn.execute("SELECT * FROM fly_positions WHERE position_id = 'orphan-fly'").fetchone()
    assert stored["broker_reconciliation_status"] == "unmatched"
    assert stored["broker_reconciled_at"] is None
    # Canonical columns untouched -- no guessing.
    assert stored["pnl"] == pytest.approx(row["pnl"])
    assert stored["fees"] == pytest.approx(row["fees"])
    assert stored["modeled_pnl"] is None


def test_pending_reconciliation_excludes_today_and_out_of_window(live_conn):
    _save_744_position(live_conn, trade_date="2026-07-30")
    _save_744_position(live_conn, position_id="too-old", trade_date="2026-07-01")
    _save_744_position(live_conn, position_id="today", trade_date="2026-07-31")

    pending = fee_reconcile.pending_reconciliation(live_conn, SYMBOL, lookback_days=5, today="2026-07-31")
    assert pending == ["2026-07-30"]
