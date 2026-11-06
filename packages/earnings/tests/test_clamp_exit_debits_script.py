"""scripts/earnings_clamp_exit_debits.py: an exit recorded outside its structure's payoff range is
moved to the bound and its gross P&L recomputed; a row inside its bounds and a structure with no bound
(a calendar) are left alone. Synthetic rows."""

import importlib.util
import json
import sqlite3
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "earnings_clamp_exit_debits.py"


def _script():
    spec = importlib.util.spec_from_file_location("earnings_clamp_exit_debits", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _leg(symbol, action, quantity=1):
    return {"symbol": symbol, "action": action, "quantity": quantity}


# A 95/100/105 call butterfly, debit 1.00: worth 0 to 5, so its exit debit lies in [-5, 0].
FLY = [
    _leg("AAA   261120C00095000", "Buy to Open"),
    _leg("AAA   261120C00100000", "Sell to Open", 2),
    _leg("AAA   261120C00105000", "Buy to Open"),
]
# A calendar across two expiries: no bound.
CAL = [_leg("BBB   261120C00050000", "Sell to Open"), _leg("BBB   261218C00050000", "Buy to Open")]


def ledger(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE trades (order_id TEXT PRIMARY KEY, strategy TEXT, entry_credit REAL, exit_debit REAL,"
        " quantity INTEGER, pnl REAL, legs_json TEXT, closed_at REAL)"
    )
    rows = [
        ("past", "expected_move_butterfly", -1.0, 3.35, 1, -435.0, json.dumps(FLY), 1.0),
        ("inside", "expected_move_butterfly", -1.0, -2.0, 1, 100.0, json.dumps(FLY), 1.0),
        ("calendar", "atm_calendar", -1.0, 4.0, 1, -500.0, json.dumps(CAL), 1.0),
        ("open", "expected_move_butterfly", -1.0, None, 1, None, json.dumps(FLY), None),
    ]
    conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return path


def test_an_exit_past_the_payoff_is_moved_to_the_bound(tmp_path):
    mod = _script()
    db = ledger(tmp_path / "paper_trades.db")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    p = mod.plan(conn)
    conn.close()
    assert p["rows"] == 3 and p["unbounded"] == 1
    (change,) = p["changes"]
    assert change["order_id"] == "past"
    assert change["exit_debit"] == [3.35, 0.0] and str(change["exit_debit"][1]) == "0.0"
    assert change["pnl"] == [-435.0, -100.0]  # the debit paid, and no more

    mod.apply(db, p["changes"])
    conn = sqlite3.connect(db)
    got = dict(conn.execute("SELECT order_id, pnl FROM trades").fetchall())
    conn.close()
    assert got == {"past": -100.0, "inside": 100.0, "calendar": -500.0, "open": None}
