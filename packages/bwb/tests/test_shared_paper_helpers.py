"""The paper loop's late-settlement query runs against this module's real ledger tables, and the
advised-arm map is the shared one. A table prefix that does not match the schema fails here, not in
a live session the day a leg is first overdue."""

from cherrypick.core import advice

from cherrypick.bwb import db, paper_loop


def _insert(conn, table, **given):
    """One row with the given columns; every other required column gets a placeholder, because only
    the columns the late-settlement query reads matter here."""
    row = dict(given)
    for _, name, kind, notnull, default, pk in conn.execute(f"PRAGMA table_info({table})"):
        if name not in row and notnull and default is None and not pk:
            row[name] = 0 if "INT" in kind.upper() or "REAL" in kind.upper() else "x"
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))


def test_overdue_legs_runs_against_the_real_schema_and_finds_a_late_leg(tmp_path):
    conn = db.connect(str(tmp_path / "ledger.db"))
    assert paper_loop._overdue_legs(conn, "2026-11-16") == []
    _insert(conn, "bwb_positions", position_id=1, status="open")
    _insert(conn, "bwb_legs", position_id=1, leg_role="short", expiration="2026-11-13", status="open")
    late = paper_loop._overdue_legs(conn, "2026-11-16")
    assert [(str(r["position_id"]), r["leg_role"], r["expiration"]) for r in late] == [
        ("1", "short", "2026-11-13")
    ]
    assert paper_loop._overdue_legs(conn, "2026-11-13") == []  # expires that day: not overdue


def test_advised_entries_is_the_shared_helper():
    assert paper_loop.advised_entries is advice.advised_entries
