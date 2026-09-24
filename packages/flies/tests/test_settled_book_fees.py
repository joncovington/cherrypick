"""A settled book read at a hypothetical expiry price carries THAT price's assignment fee.

Settlement folds the fee its real price charged into `fees`, and `fly.position_pnl` used to trust
that figure at every price. So a settled book's worst case mixed a hypothetical price with the real
settlement's fees: 2026-09-24's live control book (settled 7704.13, $30 of assignment fees)
recorded -$224.11 at 7695 and 7710+ -- the $1 of payoff a cent under 7695 plus the $30 of fees
7704.13 charged, a combination no single price produces. And the read side
that built positions without `status` added a fresh fee on top of the folded one -- $55 of fees on
the same book. Built from that book's real legs.
"""

import pytest

from cherrypick.flies import engine, fly

SETTLE = 7704.13


def _open_book() -> list[dict]:
    fly_leg = {"kind": "fly", "wing_width": 5.0, "net": 0.25, "quantity": 1, "fees": 6.8866}
    return [
        {**fly_leg, "side": "put", "center": 7690.0},
        {**fly_leg, "side": "put", "center": 7705.0},
        {**fly_leg, "side": "call", "center": 7700.0},
        {
            "kind": "short_vertical",
            "side": "call",
            "center": 7685.0,
            "wing_width": 5.0,
            "net": 2.55,
            "quantity": 1,
            "fees": 3.4433,
        },
    ]


def test_every_recorded_pnl_is_unchanged_at_its_own_settlement_price():
    for p in engine.settle(_open_book(), SETTLE):
        assert fly.position_pnl(p, SETTLE) == pytest.approx(p["pnl"], abs=0.01)


def test_a_settled_book_reads_each_price_with_that_prices_fee():
    settled = engine.settle(_open_book(), SETTLE)
    assert sum(p["assignment_fee"] for p in settled) == 30.0  # what 7704.13 charged
    floor = fly.book_floor(settled)
    # A cent under 7695 six strikes are in the money ($30) and the 7690 put fly is worth a cent:
    # -$223.11. At 7695 itself, and from 7710 up, five strikes ($25): -$219.11.
    assert floor["worst"] == pytest.approx(-223.11, abs=0.01)
    assert floor["worst_at"] == pytest.approx(7694.99)
    assert fly.book_pnl(settled, 7695.0) == pytest.approx(-219.11, abs=0.01)
    assert fly.book_pnl(settled, 7720.0) == pytest.approx(-219.11, abs=0.01)
    # ...and it is the same book as the unsettled one, read at the same prices.
    # (settlement rounds fees to the cent; the open book does not)
    assert fly.book_floor(_open_book())["worst"] == pytest.approx(floor["worst"], abs=0.02)


def test_without_a_settlement_price_a_settled_row_keeps_its_recorded_fees():
    p = {**engine.settle(_open_book(), SETTLE)[0]}
    p.pop("settlement_price")
    assert fly.position_pnl(p, 7650.0) == pytest.approx(0.25 * 100 - p["fees"], abs=0.01)
