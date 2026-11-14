"""ladder's two notifiable moments: a lot is bought, and it is sold at its target.

The sale card prints the module's stored net (gross less fees, settlement and slippage, each its own
column), never a figure re-derived from gross. Any arm is covered: the card names the arm.
"""

import json
import sqlite3

import pytest

from cherrypick.orchestrator import trade_notifier as tn

pytestmark = pytest.mark.unit

_DDL = (
    "CREATE TABLE ladder_positions (position_id TEXT, arm TEXT, symbol TEXT, quantity INTEGER, "
    "buy_session TEXT, buy_price REAL, target_price REAL, sell_price REAL, entry_value REAL, status TEXT, "
    "exit_reason TEXT, hold_days INTEGER, gross_pnl REAL, fees REAL, settlement REAL, slippage REAL, "
    "net_pnl REAL)"
)


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_DDL)
    return conn


def _lot(conn, pid, status="open", **over):
    row = {
        "position_id": pid, "arm": "chain", "symbol": "TQQQ", "quantity": 10, "buy_session": "2026-11-13",
        "buy_price": 93.0, "target_price": 96.72, "sell_price": None, "entry_value": -930.0,
        "status": status, "exit_reason": None, "hold_days": None, "gross_pnl": None, "fees": None,
        "settlement": None, "slippage": None, "net_pnl": None,
    }  # fmt: skip
    row.update(over)
    conn.execute(
        f"INSERT INTO ladder_positions ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
        tuple(row.values()),
    )


class _Rec:
    def __init__(self):
        self.sent = []

    def notify(self, level, key, title, body, embed=None):
        self.sent.append((key, body))


def test_schema_is_registered_with_seed_and_process():
    assert "ladder_lot" in tn._SCHEMAS


def test_a_buy_and_a_sale_each_notify_once():
    conn = _conn()
    seed_fn, process_fn = tn._SCHEMAS["ladder_lot"]
    state = seed_fn(conn)
    notifier = _Rec()
    _lot(conn, "chain:TQQQ:2026-11-13:1")
    process_fn(conn, state, notifier, "ladder")
    assert len(notifier.sent) == 1
    body = notifier.sent[0][1]
    assert "BUY" in body and "10 TQQQ @ 93.00" in body and "target 96.72" in body and "[chain]" in body
    conn.execute(
        "UPDATE ladder_positions SET status='closed', exit_reason='target_gain', sell_price=96.72, "
        "hold_days=3, gross_pnl=37.2, fees=0.02, settlement=0.0, slippage=0.0, net_pnl=37.18"
    )
    process_fn(conn, state, notifier, "ladder")
    assert len(notifier.sent) == 2
    sale = notifier.sent[1][1]
    assert "SELL" in sale and "10 TQQQ @ 96.72" in sale and "net $+37.18" in sale and "(target_gain)" in sale
    process_fn(conn, state, notifier, "ladder")  # nothing new
    assert len(notifier.sent) == 2


def test_a_lot_already_on_the_book_when_the_notifier_starts_is_not_announced():
    conn = _conn()
    _lot(conn, "a")
    _lot(conn, "b", status="closed", exit_reason="target_gain", sell_price=96.72, net_pnl=37.0)
    seed_fn, process_fn = tn._SCHEMAS["ladder_lot"]
    state = seed_fn(conn)
    notifier = _Rec()
    process_fn(conn, state, notifier, "ladder")
    assert notifier.sent == []


def test_the_sale_card_splits_every_cost_and_reads_a_missing_net_as_not_recorded():
    conn = _conn()
    _lot(
        conn, "a", status="closed", exit_reason="target_breakeven", sell_price=93.1, hold_days=40,
        gross_pnl=1.0, fees=0.5, settlement=0.25, slippage=0.1, net_pnl=0.15,
    )  # fmt: skip
    _lot(conn, "b", status="closed", exit_reason="target_gain", sell_price=96.72, net_pnl=None)
    rows = {r["position_id"]: r for r in conn.execute("SELECT * FROM ladder_positions")}
    text = json.dumps(tn._embed_ladder_exit(rows["a"]))
    assert "net $+0.15" in text and "fees $0.50" in text and "settlement $0.25" in text
    assert "slippage $0.10" in text and "held 40 days" in text
    assert "n/r" in tn._fmt_ladder_exit(rows["b"])
