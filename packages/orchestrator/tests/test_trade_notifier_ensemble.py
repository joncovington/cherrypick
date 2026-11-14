"""ensemble's two notifiable moments: a structure is entered, and it ends (a touch, a profit target, a time
exit, or the expiry's posted close).

The exit card prints the module's stored net (gross less fees, settlement and slippage, each its own
column), never a figure re-derived from gross. Any arm is covered: the card names the arm and the legs.
"""

import json
import sqlite3

import pytest

from cherrypick.orchestrator import trade_notifier as tn

pytestmark = pytest.mark.unit

_DDL = (
    "CREATE TABLE ensemble_positions (position_id TEXT, arm TEXT, symbol TEXT, expiration TEXT, "
    "quantity INTEGER, entry_session TEXT, entry_mid REAL, entry_value REAL, capital REAL, entry_json TEXT, "
    "status TEXT, exit_reason TEXT, gross_pnl REAL, fees REAL, settlement REAL, slippage REAL, net_pnl REAL)"
)
_LEGS = [
    {"name": "short_put", "right": "P", "side": -1, "strike": 6000.0},
    {"name": "long_put", "right": "P", "side": 1, "strike": 5990.0},
]


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_DDL)
    return conn


def _position(conn, pid, status="open", **over):
    row = {
        "position_id": pid, "arm": "put-spread-1300", "symbol": "SPX", "expiration": "2026-11-16",
        "quantity": 2, "entry_session": "2026-11-16", "entry_mid": 1.25, "entry_value": 250.0,
        "capital": 1750.0, "entry_json": json.dumps({"legs": _LEGS}), "status": status,
        "exit_reason": None, "gross_pnl": None, "fees": None, "settlement": None, "slippage": None,
        "net_pnl": None,
    }  # fmt: skip
    row.update(over)
    conn.execute(
        f"INSERT INTO ensemble_positions ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
        tuple(row.values()),
    )


class _Rec:
    def __init__(self):
        self.sent = []

    def notify(self, level, key, title, body, embed=None):
        self.sent.append((key, body))


def test_schema_is_registered_with_seed_and_process():
    assert "ensemble_trade" in tn._SCHEMAS


def test_an_entry_and_a_close_each_notify_once():
    conn = _conn()
    seed_fn, process_fn = tn._SCHEMAS["ensemble_trade"]
    state = seed_fn(conn)
    notifier = _Rec()
    _position(conn, "put-spread-1300:2026-11-16")
    process_fn(conn, state, notifier, "ensemble")
    assert len(notifier.sent) == 1
    body = notifier.sent[0][1]
    assert "ENTRY" in body and "2 x SPX 2026-11-16 -6000P +5990P" in body and "1.25 cr" in body
    assert "[put-spread-1300]" in body
    conn.execute(
        "UPDATE ensemble_positions SET status='closed', exit_reason='touch', gross_pnl=-300.0, fees=2.5, "
        "settlement=0.0, slippage=4.0, net_pnl=-306.5"
    )
    process_fn(conn, state, notifier, "ensemble")
    assert len(notifier.sent) == 2
    assert (
        "CLOSED" in notifier.sent[1][1]
        and "net $-306.50" in notifier.sent[1][1]
        and "(touch)" in notifier.sent[1][1]
    )
    process_fn(conn, state, notifier, "ensemble")  # nothing new
    assert len(notifier.sent) == 2


def test_the_exit_card_splits_every_cost_and_reads_a_missing_net_as_not_recorded():
    conn = _conn()
    _position(
        conn, "a", status="closed", exit_reason="settlement", gross_pnl=120.0, fees=2.0, settlement=1.0,
        slippage=3.0, net_pnl=114.0,
    )  # fmt: skip
    _position(conn, "b", status="closed", exit_reason="time", gross_pnl=50.0, net_pnl=None)
    rows = {r["position_id"]: r for r in conn.execute("SELECT * FROM ensemble_positions")}
    card = tn._embed_ensemble_exit(rows["a"])
    text = json.dumps(card)
    assert "net $+114.00" in text and "fees $2.00" in text and "settlement $1.00" in text
    assert "slippage $3.00" in text
    assert "n/r" in tn._fmt_ensemble_exit(rows["b"])
