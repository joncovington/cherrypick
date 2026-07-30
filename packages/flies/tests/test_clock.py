"""Every timestamp this module persists is ET and carries its offset.

Regression guard: the DB writers used a bare `datetime.now()` until 2026-07-27, so stored timestamps
were naive machine-local while the engine's session logic ran on ET. A row could read entry_time
07:45 next to an entry_window of 09:45-14:30, and any analysis comparing a stored time against a
market hour was silently two hours out on this machine — and differently wrong on another.
"""

from datetime import datetime

import book as bookmod
import clock
import db as dbmod


def test_clock_now_is_eastern_and_offset_aware():
    now = clock.now_et()
    assert now.tzinfo is not None
    assert now.utcoffset() is not None
    # ET is UTC-5 (EST) or UTC-4 (EDT) — never the machine's zone by accident.
    assert now.utcoffset().total_seconds() in (-5 * 3600, -4 * 3600)


def test_persisted_timestamps_round_trip_as_aware():
    stamped = clock.now_iso()
    parsed = datetime.fromisoformat(stamped)
    assert parsed.tzinfo is not None, f"{stamped!r} must carry its offset"
    assert parsed.isoformat(timespec="seconds") == stamped


def test_both_db_writers_emit_the_same_aware_clock():
    """book and db each had their own `_now()`; both must be ET or a single row can mix clocks."""
    for produced in (bookmod._now(), dbmod._now()):
        parsed = datetime.fromisoformat(produced)
        assert parsed.tzinfo is not None, f"{produced!r} is naive"
        assert parsed.utcoffset().total_seconds() in (-5 * 3600, -4 * 3600)


def test_today_is_the_et_session_date():
    """Not the local date: west of Eastern the local day lags, so an evening read would ask for the
    wrong session."""
    assert clock.today_iso() == clock.now_et().date().isoformat()


def test_stored_timestamps_are_subtractable():
    """`book._minutes_since` subtracts two stored timestamps. Mixing an aware value with a naive one
    raises TypeError, which is why the migration had to convert every column together rather than
    per-table."""
    a = clock.now_iso()
    b = clock.now_iso()
    assert bookmod._minutes_since(a, b) is not None
