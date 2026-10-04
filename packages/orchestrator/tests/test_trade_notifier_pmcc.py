"""pmcc's notifications for a held-long position: notable short trades ping, routine rolls do not,
each settled short pings once per leg, and a deploy never replays what is already on file."""

import json
import sqlite3

import pytest

from cherrypick.orchestrator import trade_notifier as tn

pytestmark = pytest.mark.unit

PID = "SLV:shield:2026-10-05"


class _Recorder:
    def __init__(self):
        self.sent = []

    def notify(self, level, key, title, body, embed=None):
        self.sent.append((key, body))


def _ledger():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE pmcc_positions (id INTEGER PRIMARY KEY, position_id TEXT, symbol TEXT, arm TEXT, "
        "status TEXT, entry_session TEXT, long_strike REAL, long_expiration TEXT, short_strike REAL, "
        "short_expiration TEXT, net_debit REAL, entry_net_tv REAL, entry_downside_protection_pct REAL, "
        "entry_spot REAL, roll_count INTEGER, settlement_spot REAL, gross_pnl REAL, fees REAL, "
        "exit_reason TEXT);"
        "CREATE TABLE pmcc_management_events (id INTEGER PRIMARY KEY, position_id TEXT, action TEXT, "
        "reason TEXT, executed INTEGER, detail_json TEXT, session_date TEXT);"
        "CREATE TABLE pmcc_legs (position_id TEXT, leg_role TEXT, strike REAL, expiration TEXT, status TEXT, "
        "close_kind TEXT, close_spot REAL);"
    )
    conn.execute(
        "INSERT INTO pmcc_positions (position_id, symbol, arm, status, entry_session, long_strike, "
        "long_expiration, short_strike, short_expiration, net_debit, entry_spot, roll_count) "
        "VALUES (?, 'SLV', 'shield', 'open', '2026-10-05', 32, '2027-09-17', 53, '2026-10-16', "
        "22.9, 54.7, 0)",
        (PID,),
    )
    return conn


def _event(conn, action, reason, detail):
    conn.execute(
        "INSERT INTO pmcc_management_events (position_id, action, reason, executed, detail_json) "
        "VALUES (?, ?, ?, 1, ?)",
        (PID, action, reason, json.dumps(detail)),
    )


def _settle(conn, role, strike, kind="assigned"):
    conn.execute(
        "INSERT INTO pmcc_legs VALUES (?, ?, ?, '2026-10-16', 'settled', ?, 55.1)", (PID, role, strike, kind)
    )


def _existing_state(conn):
    """A state from before the held-long keys existed: entry already pinged, nothing else."""
    return {
        "notified_entry_ids": [PID],
        "notified_exit_ids": [],
        "notified_roll_ids": [],
        "notified_settlement_ids": [],
    }


def test_a_deploy_replays_nothing_already_on_file():
    conn = _ledger()
    roll = {"old_strike": 53, "new_strike": 54, "new_expiration": "2026-10-23"}
    _event(conn, "roll_short", "decayed", roll)
    _settle(conn, "short_call_1", 53)
    rec = _Recorder()
    st = _existing_state(conn)
    tn._pmcc_process(conn, st, rec, "pmcc")
    assert rec.sent == []
    assert st["notified_settled_leg_ids"] == [f"{PID}:short_call_1"]


def test_a_notable_roll_pings_once_and_the_routine_friday_roll_never_does():
    conn = _ledger()
    rec = _Recorder()
    st = _existing_state(conn)
    tn._pmcc_process(conn, st, rec, "pmcc")  # seeds the new keys from an empty ledger
    _event(conn, "roll_short", "expiry", {"old_strike": 53, "new_strike": 54, "new_expiration": "2026-10-23"})
    _event(
        conn,
        "roll_short",
        "breach",
        {"old_strike": 54, "new_strike": 52, "new_expiration": "2026-10-23", "net_roll_credit": -0.4},
    )
    _event(conn, "close_short", "roll_deadline", {"strike": 52, "expiration": "2026-10-23"})
    tn._pmcc_process(conn, st, rec, "pmcc")
    tn._pmcc_process(conn, st, rec, "pmcc")
    bodies = [b for _k, b in rec.sent]
    assert len(bodies) == 2
    assert "ROLLED (breach)" in bodies[0] and "54 → 52" in bodies[0]
    assert "BOUGHT BACK" in bodies[1] and "roll_deadline" in bodies[1]


def test_each_settled_short_of_a_held_long_position_pings_once_and_says_the_long_stays():
    conn = _ledger()
    rec = _Recorder()
    st = _existing_state(conn)
    tn._pmcc_process(conn, st, rec, "pmcc")
    _settle(conn, "short_call_1", 53)
    _settle(conn, "short_call_2", 54, kind="expired")
    tn._pmcc_process(conn, st, rec, "pmcc")
    tn._pmcc_process(conn, st, rec, "pmcc")
    keys = [k for k, _b in rec.sent]
    assert keys == [f"trade.pmcc.settlement.{PID}:short_call_1", f"trade.pmcc.settlement.{PID}:short_call_2"]
    assert all("the long stays open" in b for _k, b in rec.sent)
    assert "ITM (shares delivered)" in rec.sent[0][1] and "OTM" in rec.sent[1][1]
