"""A cancelled entry is not a position, and nothing that values a book may count it.

The live session of 2026-09-23 is the case these tests are built from, because it is the one that
found the defect. Seven rows were written that day: five entries filled, two were cancelled before
filling. The profit forest drew all seven, and `settle_book` valued all seven, so both credited the
book with the 2.70 and 2.65 of credit on two structures that never existed. The session was
recorded at +$722.11 against a real +$194.00, and `credit_collected` at $900 against a real $365.

Worse than the number: the chart showed ONE continuous profitable band where the real book had
four narrow fragments with gaps between them, because unearned credit lifts the whole curve until
the gaps close. The forest's own docstring calls it "the one view that shows at a glance whether a
book is genuinely safe or merely safe-looking"; this made it do the opposite.

The rows below are that session's, values included, so the regression is anchored to something
that actually happened rather than to a shape invented to fit the fix.
"""

from __future__ import annotations

import pytest

from cherrypick.flies import fly

# 2026-09-23, arm `control`, SPX -- copied from `fly_positions`, fees included. They differ per
# structure on purpose: a fly carries four legs to a vertical's two, and the two rows that settled
# in the money carry assignment fees on top. A flat fee here would miss the real arithmetic.
SESSION_ROWS = [
    # center, side, kind,             net,  fees,    status,      entry_fill_status
    (7720.0, "call", "short_vertical", 2.70, 3.4433, "cancelled", "cancelled"),
    (7720.0, "put", "fly", 0.25, 21.89, "settled", "filled"),
    (7725.0, "call", "short_vertical", 2.65, 3.4433, "cancelled", "cancelled"),
    (7730.0, "call", "fly", 0.25, 6.89, "settled", "filled"),
    (7715.0, "call", "fly", 0.25, 6.89, "settled", "filled"),
    (7705.0, "call", "fly", 0.25, 16.89, "settled", "filled"),
    (7695.0, "call", "short_vertical", 2.65, 13.44, "settled", "filled"),
]

SETTLEMENT = 7706.05


def _rows() -> list[dict]:
    return [
        {
            "center": c,
            "side": side,
            "kind": kind,
            "wing_width": 5.0,
            "far_width": None,
            "net": net,
            "quantity": 1,
            "fees": fees,
            "status": status,
            "entry_fill_status": efs,
        }
        for c, side, kind, net, fees, status, efs in SESSION_ROWS
    ]


def test_a_cancelled_entry_is_not_a_position():
    assert fly.entry_never_filled({"status": "cancelled", "entry_fill_status": "cancelled"}) is True
    assert fly.entry_never_filled({"status": "settled", "entry_fill_status": "filled"}) is False
    assert fly.entry_never_filled({"status": "open", "entry_fill_status": "filled"}) is False


def test_an_unknown_fill_status_is_not_treated_as_cancelled():
    """The paper loop has never written `entry_fill_status` -- all 1,218 paper rows carry NULL. A
    predicate written as `entry_fill_status == "filled"` would drop every one of them while looking
    entirely reasonable, erasing the whole paper history from the forest and the book P&L.

    Unknown is not cancelled, the same way `None` is not zero anywhere else in this suite."""
    paper_row = {"status": "settled", "entry_fill_status": None}

    assert fly.entry_never_filled(paper_row) is False
    assert fly.held([paper_row]) == [paper_row]


def test_held_drops_only_the_cancelled_rows():
    kept = fly.held(_rows())

    assert len(kept) == 5, "expected the five filled entries"
    assert sorted(p["center"] for p in kept) == [7695.0, 7705.0, 7715.0, 7720.0, 7730.0]
    assert all(p["status"] != "cancelled" for p in kept)


def _as_settle_book_did(rows: list[dict]) -> list[dict]:
    """`settle_book` stamped every row `settled` before valuing the book, cancelled ones included.
    That matters to the arithmetic: `position_pnl` trusts `fees` to already carry the assignment
    fee on a settled row and recomputes one otherwise, so the status has to be replicated here or
    the numbers drift by exactly that fee."""
    return [{**r, "status": "settled"} for r in rows]


def test_book_pnl_excludes_credit_that_was_never_collected():
    """The headline number, and it is the one that was recorded against real money.

    $722.11 is what `fly_books` holds for 2026-09-23 and what the shipped `settle_book` reproduces
    from these rows. The two cancelled entries contributed 2.70 and 2.65 of credit that was never
    collected; the day actually made $194.00."""
    rows = _as_settle_book_did(_rows())

    counted_all = fly.book_pnl(rows, SETTLEMENT)
    real = fly.book_pnl(fly.held(rows), SETTLEMENT)

    assert counted_all == pytest.approx(722.11, abs=0.01), "should reproduce the recorded figure"
    assert real == pytest.approx(194.00, abs=0.01)
    assert counted_all - real == pytest.approx(528.11, abs=0.01)


def test_the_cash_summary_does_not_bank_credit_from_a_cancelled_order():
    """`book_cash` feeds the book row's `credit_collected`, which is the field an operator reads to
    answer "what did this book take in". A cancelled entry took in nothing."""
    rows = _as_settle_book_did(_rows())

    assert fly.book_cash(rows)["credit_collected"] == pytest.approx(900.00, abs=0.01)
    assert fly.book_cash(fly.held(rows))["credit_collected"] == pytest.approx(365.00, abs=0.01)


def test_the_profitable_band_is_not_papered_over_by_phantom_credit():
    """Worse than the number. Unearned credit lifts the whole curve, which welds the real book's
    separate profitable windows into one continuous band -- so a book that is only profitable in
    four narrow strips reads as safe across a wide one. That is precisely the misreading the
    forest exists to prevent."""
    rows = _as_settle_book_did(_rows())

    inflated = fly.book_floor(rows, step=1.0)
    real = fly.book_floor(fly.held(rows), step=1.0)

    assert len(inflated["bands"]) == 1, "the inflated view shows one continuous band"
    assert len(real["bands"]) > 1, "the real book is profitable only in fragments"
    assert real["worst"] > inflated["worst"], "and the inflated worst case is not even conservative"


def test_settlement_lands_inside_a_real_band_not_only_the_inflated_one():
    """2026-09-23 settled at 7706.05, inside one of the real fragments (7703-7707). The day was
    genuinely profitable -- this is a measurement defect, not a losing session dressed up."""
    real = fly.book_floor(fly.held(_as_settle_book_did(_rows())), step=1.0)

    assert any(lo <= SETTLEMENT <= hi for lo, hi in real["bands"])
