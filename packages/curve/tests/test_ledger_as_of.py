"""The as-of open reader for schema 'curve_vx', against a ledger built through this module\'s own DDL.
Core cannot import this package, so this is where a column the reader names and the schema drops fails.

Session S is 2026-10-21. The rows: one entered on S and closed after it (held at the close), one entered on
S and never closed, one entered the day before and closed after S, one entered and closed within S (gone by
its close), one entered after S (not yet a position, closed or not), and one cancelled entry. Read from
the book as it stands today, only the never-closed row is on it, which is the answer the old reader gave
for every past session."""

import pytest
from cherrypick.core import ledgers

from cherrypick.curve import db

S = "2026-10-21"
BEFORE, AFTER, LATER = "2026-10-20", "2026-10-22", "2026-10-23"
SCHEMA = "curve_vx"


def _connect(tmp_path):
    return db.connect(str(tmp_path / "paper_trades.db"))


def _insert(conn, i, symbol, entry, exit_, status):
    conn.execute(
        "INSERT INTO curve_positions (position_id, symbol, arm, entry_session, expiration, short_strike, "
        "long_strike, entry_max_loss, status, closed_session) "
        "VALUES (?, ?, 'control', ?, '2026-11-20', 18, 17, 0.6, ?, ?)",
        (f"p{i}", symbol, entry, status, exit_),
    )


# symbol, entry session, exit session, status
ROWS = [
    ("HELD", S, AFTER, "closed"),
    ("NEVER", S, None, "open"),
    ("EARLY", BEFORE, AFTER, "closed"),
    ("SAME", S, S, "closed"),
    ("LATER", AFTER, LATER, "closed"),
    ("FUTURE", AFTER, None, "open"),
    ("CANCELLED", S, None, "cancelled"),
]


@pytest.fixture
def conn(tmp_path):
    c = _connect(tmp_path)
    for i, (symbol, entry, exit_, status) in enumerate(ROWS):
        _insert(c, i, symbol, entry, exit_, status)
    c.commit()
    return c


def _symbols(records):
    return sorted(r["symbol"] for r in records)


def test_the_held_book_at_a_past_close_is_read_from_the_ledgers_own_dates(conn):
    as_of = ledgers.OPEN_AS_OF_READERS[SCHEMA]
    # Held at S's close: entered by S, not closed on or before S. SAME was gone by the close; LATER and
    # FUTURE were not yet positions; CANCELLED never was one.
    assert _symbols(as_of(conn, S)) == ["EARLY", "HELD", "NEVER"]
    # The day after: HELD's exit was that day, so it is gone by the close; FUTURE and LATER entered.
    assert _symbols(as_of(conn, AFTER)) == ["FUTURE", "LATER", "NEVER"]
    assert _symbols(as_of(conn, BEFORE)) == ["EARLY"]
    assert _symbols(as_of(conn, LATER)) == ["FUTURE", "NEVER"]


def test_the_book_as_it_stands_today_cannot_answer_a_past_close(conn):
    """What the reader this replaces gave every past session: only what is still open now."""
    today = ledgers.OPEN_READERS[SCHEMA](conn)
    assert _symbols(today) == ["FUTURE", "NEVER"]
    assert _symbols(today) != _symbols(ledgers.OPEN_AS_OF_READERS[SCHEMA](conn, S))


def test_the_records_are_the_open_readers_records(conn):
    held = {r["symbol"]: r for r in ledgers.OPEN_AS_OF_READERS[SCHEMA](conn, S)}
    today = {r["symbol"]: r for r in ledgers.OPEN_READERS[SCHEMA](conn)}
    assert held["NEVER"] == today["NEVER"]
    assert held["HELD"]["session"] == S and held["EARLY"]["session"] == BEFORE
    assert held["HELD"]["capital"] is not None
