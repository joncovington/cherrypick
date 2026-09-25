"""The cost breakdown the suite's trade tables show: fees, settlement and slippage apart.

Both new columns are COMPONENTS, never extra costs. `settlement_fees` is the part of `fees` that is
the exercise/assignment charge at settlement -- `fees` stays the total every reader already sums.
`slippage_dollars` is what the modelled fills conceded against mid, already inside the fill prices
and so inside gross; it measures the cost, and is never subtracted a second time.
"""

import pytest
from test_engine import BASE_CONFIG, q, snapshot

from cherrypick.flies import book as bookmod
from cherrypick.flies import db as dbmod
from cherrypick.flies import fly


@pytest.fixture()
def conn(tmp_path):
    return dbmod.connect(str(tmp_path / "paper_trades.db"))


def one_arm_config(**defaults):
    return {"defaults": {**BASE_CONFIG["defaults"], **defaults}, "arms": {"control": {}}}


def _only_row(conn):
    (row,) = dbmod.book_positions(conn, bookmod.book_id_for("2026-07-20", "control", "SPX"))
    return row


def test_conceded_is_the_haircut_the_pricing_function_applied():
    short, long_ = q(2.0, 2.4), q(1.0, 1.2)  # spreads 0.40 and 0.20
    assert fly.conceded(fly.vertical_credit, short, long_, slippage_frac=0.0) == 0.0
    assert fly.conceded(fly.vertical_credit, short, long_, slippage_frac=0.25) == pytest.approx(0.25 * 0.60)
    # Same answer whichever side of the trade: a debit's haircut is added, a credit's subtracted.
    assert fly.conceded(fly.vertical_debit, long_, short, slippage_frac=0.25) == pytest.approx(0.25 * 0.60)


def test_a_legged_entry_records_its_slippage_and_the_completion_adds_its_own(conn):
    config = one_arm_config(entry_modes=["legged"])
    slip = BASE_CONFIG["defaults"].get("slippage_frac", fly.DEFAULT_SLIPPAGE_FRAC)
    first = snapshot(underlying_price=5998.0)
    bookmod.process_snapshot(first, config, conn, "control")
    entered = _only_row(conn)
    assert entered["slippage_dollars"] is not None and entered["slippage_dollars"] > 0

    later = snapshot(underlying_price=6004.0, puts={6000: q(1.0, 1.2), 6005: q(2.4, 2.6)})
    bookmod.process_snapshot(later, config, conn, "control")
    completed = _only_row(conn)
    # The completing debit vertical bought 6005 and sold 6000: its own haircut, added on.
    completion_pts = fly.conceded(fly.vertical_debit, q(2.4, 2.6), q(1.0, 1.2), slippage_frac=slip)
    assert completed["kind"] == "fly"
    assert completed["slippage_dollars"] == pytest.approx(entered["slippage_dollars"] + completion_pts * 100)


def test_a_position_opened_before_slippage_was_recorded_keeps_it_unrecorded(conn):
    # Adding only the completion's slippage to a NULL would read as the whole position's.
    config = one_arm_config(entry_modes=["legged"])
    bookmod.process_snapshot(snapshot(underlying_price=5998.0), config, conn, "control")
    conn.execute("UPDATE fly_positions SET slippage_dollars = NULL")
    conn.commit()
    later = snapshot(underlying_price=6004.0, puts={6000: q(1.0, 1.2), 6005: q(2.4, 2.6)})
    bookmod.process_snapshot(later, config, conn, "control")
    assert _only_row(conn)["kind"] == "fly"
    assert _only_row(conn)["slippage_dollars"] is None


def test_settlement_records_its_fee_as_part_of_fees_not_on_top_of_them(conn):
    config = one_arm_config(entry_modes=["legged"])
    bookmod.process_snapshot(snapshot(underlying_price=5998.0), config, conn, "control")
    fees_before = _only_row(conn)["fees"]
    # Settling below the short put strike leaves the short leg ITM: one settlement event.
    bookmod.settle_book(conn, "2026-07-20", "control", "SPX", 5990.0, config)
    settled = _only_row(conn)
    itm = fly.itm_legs_at_settlement(bookmod._to_position(settled), 5990.0)
    assert itm >= 1
    assert settled["settlement_fees"] == pytest.approx(fly.expire_fee(itm))
    # A component: the total already includes it, exactly once.
    assert settled["fees"] == pytest.approx(fees_before + settled["settlement_fees"], abs=0.01)  # cents
    assert settled["pnl"] == pytest.approx(settled["gross_pnl"] - settled["fees"])


def test_an_otm_settlement_records_a_zero_fee_not_a_missing_one(conn):
    config = one_arm_config(entry_modes=["legged"])
    bookmod.process_snapshot(snapshot(underlying_price=5998.0), config, conn, "control")
    bookmod.settle_book(conn, "2026-07-20", "control", "SPX", 6050.0, config)  # far above: all OTM
    assert _only_row(conn)["settlement_fees"] == 0.0
