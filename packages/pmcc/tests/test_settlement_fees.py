"""The settlement part of a position's fees, recorded beside the total (2026-09-25).

`fees` is the total of every cost the position has incurred: entry, each traded exit, and the
settlement or assignment charges. `settlement_fees` is the part of it that is settlement -- a
component the suite's trade tables show in its own column, never an extra cost.
"""

from __future__ import annotations

import pytest

from cherrypick.pmcc import book, db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "paper.db"))
    db.save_position(
        c,
        {
            "position_id": "TQQQ:control:2026-09-15",
            "symbol": "TQQQ",
            "arm": "control",
            "entry_session": "2026-09-15",
            "quantity": 1,
            "long_expiration": "2026-12-18",
            "long_strike": 60.0,
            "short_expiration": "2026-09-19",
            "short_strike": 90.0,
            "fees": 3.0,
            "status": "open",
        },
    )
    return c


def _row(conn):
    return conn.execute(
        "SELECT fees, exit_cost, exit_slippage, settlement_fees FROM pmcc_positions"
    ).fetchone()


def test_a_traded_exit_adds_to_fees_but_not_to_settlement(conn):
    book._accumulate_exit_costs(conn, "TQQQ:control:2026-09-15", fee=1.3, slippage=0.4)
    row = _row(conn)
    assert row["fees"] == pytest.approx(4.7)
    assert row["settlement_fees"] is None


def test_a_settlement_charge_is_recorded_as_the_part_of_fees_it_is(conn):
    book._accumulate_exit_costs(conn, "TQQQ:control:2026-09-15", fee=1.3, slippage=0.4)
    book._accumulate_exit_costs(conn, "TQQQ:control:2026-09-15", fee=5.0, slippage=0.0, settlement=True)
    book._accumulate_exit_costs(conn, "TQQQ:control:2026-09-15", fee=5.12, slippage=0.0, settlement=True)
    row = _row(conn)
    assert row["fees"] == pytest.approx(3.0 + 1.3 + 0.4 + 5.0 + 5.12)
    assert row["settlement_fees"] == pytest.approx(10.12)
    assert row["exit_cost"] == pytest.approx(1.3 + 5.0 + 5.12)
