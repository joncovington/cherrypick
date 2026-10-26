"""B1 for earnings: a live opening order is held to the written kill criteria; a close never is."""

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest
from cherrypick.core import killcriteria as kc

from cherrypick.earnings import db as live_db
from cherrypick.earnings import tt

OPEN = {
    "legs": [
        {"symbol": "X", "action": "Sell to Open", "quantity": 1},
        {"symbol": "Y", "action": "Buy to Open", "quantity": 1},
    ]
}
CLOSE = {"legs": [{"symbol": "X", "action": "Buy to Close", "quantity": 1}]}
HAND = {"live": {"kill": {"written": "2026-10-25", "hand_limit_dollars": 750}}}
BANDS = {
    "live": {
        "kill": {
            "written": "2026-10-25",
            "drawdown_reduce": 500.0,
            "drawdown_block": 800.0,
            "streak_reduce": 5,
            "streak_block": 8,
        }
    }
}
CLOSED_AT = 1793131200  # a session after the criteria were written


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
                    (f"o{i}", "X", "default", "iron_fly", n, 0, 0, 300, "2026-10-26T15:35:00", CLOSED_AT))  # fmt: skip
    con.commit()
    con.close()
    return db


def _sized(spec, quantity):
    return {"legs": [{**leg, "quantity": quantity} for leg in spec["legs"]]}


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


def test_a_live_close_recorded_without_its_costs_refuses(tmp_path, monkeypatch):
    """The live path as a person runs it: `save_trade`, then `save_close` with the pnl and no
    `exit_cost`. The row has no net, which the verdict skips -- so a loss past the hand limit read as
    no drawdown at all and the next opening order went out. Now the row refuses until it is recorded."""
    path = tmp_path / "earnings_trades.db"
    monkeypatch.setattr(live_db, "DB_PATH", path)
    trade = {"order_id": "L1", "symbol": "X", "expiration": "2026-10-30", "opened_at": CLOSED_AT - 3600}
    assert live_db.cmd_save_trade(SimpleNamespace(data=json.dumps(trade)))["ok"]
    close = {"order_id": "L1", "exit_debit": 9.0, "pnl": -900.0, "closed_at": CLOSED_AT}
    assert live_db.cmd_save_close(SimpleNamespace(data=json.dumps(close)))["ok"]

    refusal = tt.kill_refusal(OPEN, HAND, path)
    assert refusal is not None and "have no net" in refusal and "L1" in refusal
    assert kc.latched("earnings") is None  # recording the costs is the fix, so it does not latch
    assert tt.kill_refusal(CLOSE, HAND, path) is None

    # Recorded, the loss is a loss: past the hand limit, a block.
    con = sqlite3.connect(path)
    con.execute("UPDATE trades SET entry_cost = 3.5, exit_cost = 3.5 WHERE order_id = 'L1'")
    con.commit()
    con.close()
    assert "750" in tt.kill_refusal(OPEN, HAND, path)


def test_a_reduced_verdict_refuses_a_full_size_order(tmp_path):
    db = _ledger(tmp_path, [-600.0])  # past drawdown_reduce, short of drawdown_block
    assert "reduced" in tt.kill_refusal(_sized(OPEN, 2), BANDS, db)
    assert tt.kill_refusal(OPEN, BANDS, db) is None  # one contract a leg still goes
    assert tt.kill_refusal(_sized(CLOSE, 4), BANDS, db) is None
    # A normal verdict leaves the size alone.
    assert tt.kill_refusal(_sized(OPEN, 4), BANDS, _ledger(tmp_path, [-100.0], name="calm.db")) is None


def test_a_missing_live_ledger_refuses_until_one_is_started(tmp_path, monkeypatch):
    path = tmp_path / "earnings_trades.db"
    refusal = tt.kill_refusal(OPEN, HAND, path)
    assert refusal is not None and "missing" in refusal and "init_db" in refusal
    assert tt.kill_refusal(CLOSE, HAND, path) is None
    monkeypatch.setattr(live_db, "DB_PATH", path)
    assert live_db.cmd_init_db(SimpleNamespace())["ok"]
    assert tt.kill_refusal(OPEN, HAND, path) is None  # a genuine first trade, from an empty book


def test_an_unreadable_live_ledger_refuses(tmp_path):
    bad = tmp_path / "earnings_trades.db"
    bad.write_bytes(b"not a database")
    assert "unreadable" in tt.kill_refusal(OPEN, HAND, bad)


# --------------------------------------------------------------------------- the order path
@pytest.fixture
def broker(monkeypatch):
    """The live order path up to the broker, with the broker replaced by a recorder."""
    reached: list[str] = []

    async def _trading_account(account_number=None):
        reached.append("account")
        return SimpleNamespace()

    async def _place_order(*a, **k):
        reached.append("place_order")
        return {"ok": True}

    monkeypatch.setattr(tt, "_live_trading_enabled", lambda: True)
    monkeypatch.setattr(tt, "_trading_account", _trading_account)
    monkeypatch.setattr(tt, "_build_order", lambda spec: spec)
    monkeypatch.setattr(tt._broker, "place_order", _place_order)
    monkeypatch.setattr(tt, "get_session", lambda: None)
    return reached


def _execute(order, live=True):
    args = SimpleNamespace(live=live, order=json.dumps(order), account_number=None, wait=0)
    return asyncio.run(tt.cmd_execute_trade(args))


def test_execute_trade_stops_a_refused_live_order_before_the_broker(broker, tmp_path, monkeypatch):
    monkeypatch.setattr(tt, "_load_config", lambda: HAND)
    monkeypatch.setattr(tt._paths, "live_db_path", lambda: _ledger(tmp_path, [-800.0]))
    result = _execute(OPEN)
    assert result["ok"] is False and "kill criteria" in result["error"]
    assert broker == [], "a refused order must never reach the account or the broker"


def test_execute_trade_lets_an_allowed_live_order_through(broker, tmp_path, monkeypatch):
    """The control for the test above: the same path with nothing to refuse reaches the broker, so
    the refusal is what stopped it there."""
    monkeypatch.setattr(tt, "_load_config", lambda: HAND)
    monkeypatch.setattr(tt._paths, "live_db_path", lambda: _ledger(tmp_path, [-100.0]))
    assert _execute(OPEN)["ok"] is True
    assert broker == ["account", "place_order"]
