"""weeklyput's two notifiable moments: a put is sold, and it settles at the expiry's posted close.

The exit card prints the module's stored net (gross less fees, settlement and slippage, each its own
column), never a figure re-derived from gross.
"""

import sqlite3

import pytest

from cherrypick.orchestrator import trade_notifier as tn

pytestmark = pytest.mark.unit

_DDL = (
    "CREATE TABLE weeklyput_positions (position_id TEXT, arm TEXT, symbol TEXT, expiration TEXT, "
    "strike REAL, quantity INTEGER, entry_session TEXT, entry_mid REAL, entry_delta REAL, "
    "entry_value REAL, status TEXT, settle_price REAL, exit_reason TEXT, gross_pnl REAL, fees REAL, "
    "settlement REAL, slippage REAL, net_pnl REAL)"
)


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_DDL)
    return conn


def _put(conn, pid, status="open", **over):
    row = {
        "position_id": pid, "arm": "unhedged", "symbol": "SPY", "expiration": "2026-11-13",
        "strike": 640.0, "quantity": 1, "entry_session": "2026-11-06", "entry_mid": 0.85,
        "entry_delta": -0.10, "entry_value": 85.0, "status": status, "settle_price": None,
        "exit_reason": None, "gross_pnl": None, "fees": None, "settlement": None, "slippage": None,
        "net_pnl": None,
    }  # fmt: skip
    row.update(over)
    conn.execute(
        f"INSERT INTO weeklyput_positions ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
        tuple(row.values()),
    )


class _Rec:
    def __init__(self):
        self.sent = []

    def notify(self, level, key, title, body, embed=None):
        self.sent.append((key, body))


def test_schema_is_registered_with_seed_and_process():
    assert "weeklyput_put" in tn._SCHEMAS


def test_a_sale_and_a_settlement_each_notify_once():
    conn = _conn()
    seed_fn, process_fn = tn._SCHEMAS["weeklyput_put"]
    state = seed_fn(conn)  # nothing yet: the first pass seeds an empty ledger
    notifier = _Rec()
    _put(conn, "unhedged:SPY:2026-11-13")
    process_fn(conn, state, notifier, "weeklyput")
    assert len(notifier.sent) == 1
    assert "SOLD" in notifier.sent[0][1] and "SPY 2026-11-13 640P" in notifier.sent[0][1]
    conn.execute(
        "UPDATE weeklyput_positions SET status='closed', settle_price=650.0, exit_reason='expired', "
        "gross_pnl=85.0, fees=1.2, settlement=0.0, slippage=2.0, net_pnl=81.8"
    )
    process_fn(conn, state, notifier, "weeklyput")
    assert len(notifier.sent) == 2
    assert "SETTLED" in notifier.sent[1][1] and "net $+81.80" in notifier.sent[1][1]
    process_fn(conn, state, notifier, "weeklyput")  # nothing new
    assert len(notifier.sent) == 2


def test_the_exit_card_splits_every_cost_and_reads_a_missing_net_as_not_recorded():
    conn = _conn()
    _put(
        conn, "a", status="closed", settle_price=630.0, exit_reason="assigned", gross_pnl=-915.0,
        fees=1.2, settlement=6.3, slippage=2.0, net_pnl=-924.5,
    )  # fmt: skip
    _put(conn, "b", status="closed", settle_price=650.0, exit_reason="expired", net_pnl=None)
    a, b = conn.execute("SELECT * FROM weeklyput_positions").fetchall()
    details = tn._embed_weeklyput_exit(a)["fields"][0]["value"]
    assert "fees $1.20, settlement $6.30, slippage $2.00" in details
    assert "net n/r" in tn._fmt_weeklyput_exit(b)
