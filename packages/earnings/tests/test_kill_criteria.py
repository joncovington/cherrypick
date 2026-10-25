"""B1 for earnings: a live opening order is held to the written kill criteria; a close never is."""

import sqlite3

import pytest
from cherrypick.core import killcriteria as kc

from cherrypick.earnings import tt

OPEN = {
    "legs": [
        {"symbol": "X", "action": "Sell to Open", "quantity": 1},
        {"symbol": "Y", "action": "Buy to Open", "quantity": 1},
    ]
}
CLOSE = {"legs": [{"symbol": "X", "action": "Buy to Close", "quantity": 1}]}
HAND = {"live": {"kill": {"written": "2026-10-25", "hand_limit_dollars": 750}}}


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))


def _ledger(tmp_path, nets, name="live.db"):
    db = tmp_path / name
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE trades (order_id TEXT, symbol TEXT, profile TEXT, strategy TEXT, pnl REAL, entry_cost REAL,"
        " exit_cost REAL, capital_at_risk REAL, opened_at TEXT, closed_at TEXT)"
    )
    for i, n in enumerate(nets):
        con.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (f"o{i}", "X", "default", "iron_fly", n, 0, 0, 300, "2026-10-26T15:35:00", 1793131200))  # fmt: skip
    con.commit()
    con.close()
    return db


def test_no_criteria_refuses_an_opening_order_but_never_a_close(tmp_path):
    assert "kill_criteria_unwritten" in tt.kill_refusal(OPEN, {}, tmp_path / "none.db")
    assert tt.kill_refusal(CLOSE, {}, tmp_path / "none.db") is None


def test_the_hand_limit_holds_one_contract_and_blocks_at_the_limit(tmp_path):
    assert tt.kill_refusal(OPEN, HAND, _ledger(tmp_path, [-200.0])) is None
    two = {"legs": [{**OPEN["legs"][0], "quantity": 2}]}
    assert "one contract" in tt.kill_refusal(two, HAND, _ledger(tmp_path, [], name="empty.db"))


def test_a_block_latches(tmp_path):
    db = _ledger(tmp_path, [-800.0])
    assert "750" in tt.kill_refusal(OPEN, HAND, db)
    assert kc.latched("earnings") is not None
    assert tt.kill_refusal(CLOSE, HAND, db) is None  # closing stays possible while latched
