"""Per-leg ticket facts: each leg's share of its ticket sums to the ticket, to the cent.

A held-long position closes ~40 shorts over its life, and its tracker shows each short's own fee and
slippage. Those come from allocating each ticket's position-level cost across its legs, so the one
identity that matters is that the legs of every ticket sum to that ticket exactly -- and that
control's position-level totals are what they were before the legs carried anything.
"""

from datetime import datetime

import pytest

from cherrypick.pmcc import db, engine, paper_loop


def _legs(conn, pid):
    return {leg["leg_role"]: leg for leg in db.legs_for(conn, pid)}


def test_allocation_sums_to_the_ticket_and_puts_the_remainder_last():
    assert engine.allocate(1.25, [0.625, 0.625]) == [0.62, 0.63]
    assert sum(engine.allocate(2.24, [1.12, 1.12])) == pytest.approx(2.24)
    assert engine.allocate(0.0, [0.0, 0.0]) == [0.0, 0.0]
    assert engine.allocate(5.0, [5.0]) == [5.0]
    assert engine.allocate(3.0, []) == []


def _enter(cache, config, tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    cache.spot("TQQQ", 70.60)
    cache.option("TQQQ", "2026-09-04", 71.0, bid=0.90, ask=1.00)
    cache.option("TQQQ", "2026-09-11", 58.0, bid=14.40, ask=14.60, delta=0.88)
    paper_loop.run_once(config, conn, cache_path=cache.path, when=datetime(2026, 8, 24, 11, 0))
    position = db.open_positions(conn)[0]
    return conn, position


def test_entry_legs_carry_their_share_of_the_entry_ticket(cache, config, tmp_path):
    conn, position = _enter(cache, config, tmp_path)
    legs = _legs(conn, position["position_id"])
    # Control's position-level figures are exactly the ticket's, as before the legs carried anything.
    ticket = engine.entry_cost("TQQQ", [{"bid": 14.40, "ask": 14.60}, {"bid": 0.90, "ask": 1.00}], 1, config)
    assert position["entry_cost"] == ticket["fee"]
    assert position["entry_slippage"] == ticket["slippage"]
    assert position["fees"] == ticket["total"]
    assert sum(leg["entry_cost"] for leg in legs.values()) == pytest.approx(position["entry_cost"], abs=1e-9)
    assert sum(leg["entry_slippage"] for leg in legs.values()) == pytest.approx(
        position["entry_slippage"], abs=1e-9
    )
    # The sell carries the TAF the buy does not -- a fraction of a cent, so visible unrounded only.
    assert engine.leg_fee("TQQQ", 1, opening=True, selling=True) > engine.leg_fee(
        "TQQQ", 1, opening=True, selling=False
    )
    for leg in legs.values():
        assert leg["entry_spot"] == 70.60
        assert leg["opened_session"] == "2026-08-24"
        assert leg["opened_at"] is not None


def test_closed_legs_carry_their_share_of_the_close_ticket_and_why(cache, config, tmp_path):
    conn, position = _enter(cache, config, tmp_path)
    cache.spot("TQQQ", 70.90)
    cache.option("TQQQ", "2026-09-04", 71.0, bid=0.20, ask=0.25)
    cache.option("TQQQ", "2026-09-11", 58.0, bid=14.65, ask=14.85)
    paper_loop.run_once(config, conn, cache_path=cache.path, when=datetime(2026, 9, 4, 11, 0))
    closed = conn.execute(
        "SELECT * FROM pmcc_positions WHERE position_id = ?", (position["position_id"],)
    ).fetchone()
    assert closed["status"] == "closed"
    legs = _legs(conn, position["position_id"])
    assert sum(leg["close_cost"] for leg in legs.values()) == pytest.approx(closed["exit_cost"], abs=1e-9)
    assert sum(leg["close_slippage"] for leg in legs.values()) == pytest.approx(
        closed["exit_slippage"], abs=1e-9
    )
    # Every cost the position paid is on some leg: entry + exit, fee + slippage = fees.
    on_legs = sum(
        leg["entry_cost"] + leg["entry_slippage"] + leg["close_cost"] + leg["close_slippage"]
        for leg in legs.values()
    )
    assert on_legs == pytest.approx(closed["fees"], abs=1e-9)
    for leg in legs.values():
        assert leg["close_reason"] == "short_expiration"
        assert leg["close_spot"] == 70.90


def test_a_settled_leg_records_its_print_and_no_trading_cost(cache, config, tmp_path):
    conn, position = _enter(cache, config, tmp_path)
    paper_loop.run_settle(
        config, conn, cache_path=cache.path, when=datetime(2026, 9, 4, 16, 30), price=72.10, day="2026-09-04"
    )
    short = _legs(conn, position["position_id"])["short_call_1"]
    assert short["close_kind"] == "assigned"
    assert short["close_spot"] == 72.10
    assert short["close_cost"] == 0.0 and short["close_slippage"] == 0.0
    assert short["close_reason"] == "settlement"
