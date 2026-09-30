"""The shared close / dispose / finalize writer the spread books (calendars, pmcc, curve) run on.

Expected rows are worked by hand here rather than re-derived through the code under test: the
per-leg P&L, the gross and exit value, and the running cost totals. The one input taken from
`cherrypick.core.fees` is the fee schedule itself, which `test_fees.py` pins.
"""

from __future__ import annotations

import sqlite3

import pytest

from cherrypick.core import fees, ledgerstore, spreadbook

SCHEMA = """
CREATE TABLE IF NOT EXISTS t_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id TEXT NOT NULL UNIQUE,
    symbol TEXT, quantity INTEGER, status TEXT NOT NULL DEFAULT 'open',
    exit_reason TEXT, closed_at TEXT, closed_session TEXT, exit_value REAL, gross_pnl REAL,
    exit_cost REAL, exit_slippage REAL, fees REAL, settlement_fees REAL,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS t_legs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id TEXT NOT NULL, leg_role TEXT NOT NULL, streamer_symbol TEXT, action TEXT,
    entry_mid REAL, status TEXT, close_kind TEXT, closed_at TEXT,
    close_bid REAL, close_ask REAL, close_value REAL,
    created_at TEXT, updated_at TEXT,
    UNIQUE(position_id, leg_role)
);
CREATE TABLE IF NOT EXISTS t_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id TEXT NOT NULL, leg_role TEXT NOT NULL, direction TEXT, shares INTEGER, basis REAL,
    status TEXT, disposed_session TEXT, disposed_at TEXT, disposal_price REAL, share_pnl REAL,
    fees REAL, created_at TEXT, updated_at TEXT,
    UNIQUE(position_id, leg_role)
);
CREATE TABLE IF NOT EXISTS t_marks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date TEXT, usable INTEGER, refusal TEXT
);
"""
NO_SLIPPAGE = {"tastytrade_costs": {"slippage_frac_of_spread": 0.0}}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    yield c
    c.close()


@pytest.fixture
def book():
    return spreadbook.SpreadBook(ledgerstore.LedgerStore("t_", SCHEMA, {}))


def _calendar(book, conn, *, quantity=2, fees_so_far=3.0):
    """A put calendar: short front entered at 3.20, long back at 6.45."""
    book.store.save_position(
        conn,
        {"position_id": "p", "symbol": "SPX", "quantity": quantity, "status": "open", "fees": fees_so_far},
    )
    for role, sym, action, mid in (
        ("back_put", ".B", "Buy to Open", 6.45),
        ("front_put", ".F", "Sell to Open", 3.20),
    ):
        book.store.save_leg(
            conn,
            {
                "position_id": "p",
                "leg_role": role,
                "streamer_symbol": sym,
                "action": action,
                "entry_mid": mid,
                "status": "open",
            },
        )


def _pos(conn):
    return dict(conn.execute("SELECT * FROM t_positions WHERE position_id = 'p'").fetchone())


def _legs(conn):
    return {r["leg_role"]: dict(r) for r in conn.execute("SELECT * FROM t_legs")}


MARKS = {
    "quotes": {
        ".F": {"bid": 1.05, "ask": 1.15, "mid": 1.10},
        ".B": {"bid": 3.90, "ask": 4.10, "mid": 4.00},
    }
}


# --------------------------------------------------------------------------- the traded close


def test_a_traded_close_books_every_leg_and_the_position(book, conn):
    _calendar(book, conn)
    result = book.close_open_legs(
        conn, _pos(conn), MARKS, NO_SLIPPAGE, reason="friday_exit", session_date="2026-10-02"
    )

    # Two legs closed, one of them (the long back) sold to close.
    expected_fee = fees.spread_close_cost("SPX", [{}, {}], 2, NO_SLIPPAGE, sell_legs=1)["fee"]
    assert result == {
        "ok": True,
        "position_id": "p",
        "legs_closed": 2,
        "cost": {"fee": expected_fee, "slippage": 0.0, "total": expected_fee},
    }
    legs = _legs(conn)
    assert (legs["front_put"]["status"], legs["front_put"]["close_kind"]) == ("closed", "traded")
    assert (legs["front_put"]["close_bid"], legs["front_put"]["close_ask"]) == (1.05, 1.15)
    assert legs["front_put"]["close_value"] == 1.10
    assert legs["back_put"]["close_value"] == 4.00

    pos = _pos(conn)
    # short: 3.20 - 1.10 = +2.10; long: 4.00 - 6.45 = -2.45; -0.35/share x 100 x 2 = -70.00
    assert pos["gross_pnl"] == -70.0
    # the long is received (+4.00), the short paid away (-1.10)
    assert pos["exit_value"] == 2.9
    assert (pos["status"], pos["exit_reason"], pos["closed_session"]) == (
        "closed",
        "friday_exit",
        "2026-10-02",
    )
    assert pos["exit_cost"] == expected_fee
    assert pos["exit_slippage"] == 0.0
    assert pos["fees"] == round(3.0 + expected_fee, 2)
    assert pos["settlement_fees"] is None, "a traded close is not settlement"


def test_a_missing_quote_refuses_and_writes_nothing(book, conn):
    _calendar(book, conn)
    before = (_pos(conn), _legs(conn))
    marks = {"quotes": {".F": MARKS["quotes"][".F"]}}
    result = book.close_open_legs(conn, _pos(conn), marks, {}, reason="x", session_date="2026-10-02")
    assert result == {"ok": False, "reason": "missing_leg_quotes", "position_id": "p"}
    assert (_pos(conn), _legs(conn)) == before


def test_an_unpriced_close_never_finalizes(book, conn):
    _calendar(book, conn)
    marks = {"quotes": {**MARKS["quotes"], ".B": {"bid": 3.9, "ask": 4.1, "mid": None}}}
    book.close_open_legs(conn, _pos(conn), marks, NO_SLIPPAGE, reason="x", session_date="2026-10-02")
    pos = _pos(conn)
    assert pos["status"] == "open"
    assert pos["gross_pnl"] is None
    assert pos["exit_cost"] is not None, "the close's costs are still charged"


# --------------------------------------------------------------------------- shares and finalize


def _assigned_calendar(book, conn):
    """Front put settled ITM at intrinsic 2.00 and assigned (LONG 200 shares at 648.00 basis); back
    put closed at 5.00."""
    _calendar(book, conn)
    book.store.save_leg(
        conn, {"position_id": "p", "leg_role": "front_put", "status": "settled", "close_value": 2.0}
    )
    book.store.save_leg(
        conn, {"position_id": "p", "leg_role": "back_put", "status": "closed", "close_value": 5.0}
    )
    book.store.save_assignment(
        conn,
        {
            "position_id": "p",
            "leg_role": "front_put",
            "direction": "long",
            "shares": 200,
            "basis": 648.0,
            "status": "open",
        },
    )


def test_open_shares_hold_the_close_then_disposal_finalizes(book, conn):
    _assigned_calendar(book, conn)
    assert book.finalize_if_done(conn, "p", reason="x", session_date="2026-10-05") is False
    assert _pos(conn)["status"] == "open"

    assignment = dict(conn.execute("SELECT * FROM t_assignments").fetchone())
    result = book.dispose_assignment(conn, assignment, 650.5, session_date="2026-10-05")

    fee = fees.assignment_fee(assignment, 650.5)
    # long 200 shares from 648.00 to 650.50 = +500.00
    assert result == {"position_id": "p", "share_pnl": 500.0, "fee": fee, "price": 650.5}
    row = dict(conn.execute("SELECT * FROM t_assignments").fetchone())
    assert (row["status"], row["disposed_session"], row["disposal_price"]) == (
        "disposed",
        "2026-10-05",
        650.5,
    )
    assert (row["share_pnl"], row["fees"]) == (500.0, fee)

    pos = _pos(conn)
    # short put 3.20 -> 2.00 = +1.20; long put 6.45 -> 5.00 = -1.45; -0.25 x 100 x 2 = -50 + 500 shares
    assert pos["gross_pnl"] == 450.0
    assert pos["exit_value"] == 3.0  # +5.00 received, -2.00 settled against
    assert (pos["status"], pos["exit_reason"]) == ("closed", "shares_disposed")
    assert pos["settlement_fees"] == fee, "the assignment charge is the settlement part of fees"
    assert pos["fees"] == round(3.0 + fee, 2)
    assert pos["exit_slippage"] == 0.0


def test_a_short_delivery_earns_the_fall(book, conn):
    _assigned_calendar(book, conn)
    conn.execute("UPDATE t_assignments SET direction = 'short'")
    assignment = dict(conn.execute("SELECT * FROM t_assignments").fetchone())
    result = book.dispose_assignment(conn, assignment, 650.5, session_date="2026-10-05")
    assert result["share_pnl"] == -500.0
    assert _pos(conn)["gross_pnl"] == -550.0


def test_a_reason_already_on_the_row_wins_and_a_closed_row_is_left_alone(book, conn):
    _assigned_calendar(book, conn)
    conn.execute("UPDATE t_assignments SET status = 'disposed', share_pnl = 0")
    book.store.save_position(conn, {"position_id": "p", "exit_reason": "stop"})
    assert book.finalize_if_done(conn, "p", reason="later", session_date="2026-10-05") is True
    closed = _pos(conn)
    assert closed["exit_reason"] == "stop"
    assert book.finalize_if_done(conn, "p", reason="again", session_date="2026-10-06") is False
    assert _pos(conn) == closed
    assert book.finalize_if_done(conn, "missing", reason="x", session_date="2026-10-06") is False


# --------------------------------------------------------------------------- the small ones


def test_exit_costs_accumulate_from_nulls_and_settlement_is_a_component(book, conn):
    book.store.save_position(conn, {"position_id": "p", "quantity": None})
    book.accumulate_exit_costs(conn, "p", fee=1.3, slippage=0.4)
    book.accumulate_exit_costs(conn, "p", fee=5.0, slippage=0.0, settlement=True)
    pos = _pos(conn)
    assert (pos["exit_cost"], pos["exit_slippage"], pos["fees"], pos["settlement_fees"]) == (
        6.3,
        0.4,
        6.7,
        5.0,
    )
    assert book.position_quantity(conn, "p") == 1
    assert book.position_quantity(conn, "absent") == 1


@pytest.mark.parametrize(
    ("snapshot", "blocks"),
    [
        ({}, False),
        ({"max_spread_pct": 0.30}, True),  # no per-leg detail: percentage alone
        ({"max_spread_pct": 0.25}, False),
        ({"leg_spreads": [{"pct": 2.0, "abs": 0.01}]}, False),  # the penny-wide 200% short
        ({"leg_spreads": [{"pct": 0.1, "abs": 0.02}, {"pct": 0.3, "abs": 0.06}]}, True),
        ({"leg_spreads": [{"pct": 0.3, "abs": 0.05}]}, False),  # both limits are strict
    ],
)
def test_exit_spread_blocks_needs_percent_and_money(snapshot, blocks):
    assert spreadbook.exit_spread_blocks(snapshot, {}) is blocks


def test_mark_coverage_counts_refusals_by_reason(conn):
    store = ledgerstore.LedgerStore("t_", SCHEMA, {})
    rows = [(1, None), (0, "stale"), (0, "stale"), (0, "wide"), (1, None)]
    conn.executemany("INSERT INTO t_marks (session_date, usable, refusal) VALUES ('2026-10-02', ?, ?)", rows)
    assert store.mark_coverage(conn, "2026-10-02") == {
        "session": "2026-10-02",
        "marks": 5,
        "refused": 3,
        "refusal_share": 0.6,
        "refusals": {"stale": 2, "wide": 1},
    }
    assert store.mark_coverage(conn, "2026-10-03")["refusal_share"] is None
