"""An assigned VXX leg pays its $5 settlement event once.

VXX is always physically settled, so every ITM leg is an assignment: settlement delivers shares and
disposal sells them, and the $5 event is inside `engine.assignment_fee` at disposal. Settlement also
charged `settlement_fee(itm)` for the same legs, so each paid twice -- the guard calendars and pmcc
carry (`itm - assigned`) was missing here.
"""

from __future__ import annotations

import sqlite3

import pytest
from cherrypick.core import fees as core_fees

from cherrypick.curve import book, db, engine


def _plan():
    """A 50/55 VXX call credit spread, the shape `book.enter_position` takes."""

    def leg(role, strike, action, mid):
        return {
            "leg_role": role,
            "occ_symbol": f"VXX   {role}{strike:g}",
            "streamer_symbol": f".{role}",
            "expiration": "2026-10-16",
            "strike": strike,
            "option_type": "call",
            "action": action,
            "bid": mid - 0.05,
            "ask": mid + 0.05,
            "mid": mid,
            "delta": 0.30,
        }

    return {
        "symbol": "VXX",
        "expiration": "2026-10-16",
        "short_strike": 50.0,
        "long_strike": 55.0,
        "spot": 45.0,
        "short_mid": 2.0,
        "long_mid": 1.0,
        "credit": 1.0,
        "width": 5.0,
        "max_loss": 400.0,
        "credit_pct_of_width": 0.2,
        "dte": 30,
        "legs": [leg("short_call", 50.0, "Sell to Open", 2.0), leg("long_call", 55.0, "Buy to Open", 1.0)],
    }


@pytest.fixture
def conn(tmp_path):
    return db.connect(str(tmp_path / "paper.db"))


def _assignments(conn, pid):
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute("SELECT * FROM curve_assignments WHERE position_id = ?", (pid,))]


def test_both_legs_assigned_pay_the_settlement_event_once_each(conn):
    opened = book.enter_position(
        conn, _plan(), {}, "control", entry_session="2026-09-17", advice_params=None, regime=None
    )
    pid = opened["position_id"]
    entry_fees = conn.execute("SELECT fees FROM curve_positions WHERE position_id = ?", (pid,)).fetchone()[0]

    # 60 is through both strikes (50 short, 55 long): two ITM legs, both physically assigned.
    book.settle_expiring_legs(conn, "2026-10-16", 60.0, {}, symbol="VXX")
    after_settle = conn.execute("SELECT fees FROM curve_positions WHERE position_id = ?", (pid,)).fetchone()[
        0
    ]
    assert after_settle == pytest.approx(entry_fees)  # nothing cash-settled, so nothing charged here

    disposal = 0.0
    for a in _assignments(conn, pid):
        book.dispose_assignment(conn, a, 60.5, session_date="2026-10-19")
        disposal += engine.assignment_fee(a, 60.5)
    final = conn.execute("SELECT fees FROM curve_positions WHERE position_id = ?", (pid,)).fetchone()[0]
    assert final == pytest.approx(entry_fees + disposal, abs=0.01)
    # ...and that disposal total holds exactly one $5 event per assigned leg.
    share_side = disposal - 2 * core_fees.ASSIGNMENT_FEE_PER_SETTLEMENT
    assert 0 <= share_side < 1.0
