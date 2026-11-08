"""weeklyput's health, expectation and independent reconcile route, over a ledger shaped as the module
writes it (the entries register, the positions, the loop's iterations)."""

import sqlite3

import pytest

from cherrypick.review import facts, reconcile

SESSION = "2026-11-06"


def _ledger():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE weeklyput_entries (arm TEXT, symbol TEXT, expiration TEXT, entry_session TEXT, "
        "outcome TEXT, refusal TEXT);"
        "CREATE TABLE weeklyput_loop_iterations (session_date TEXT);"
        "CREATE TABLE weeklyput_positions (status TEXT, settle_session TEXT, gross_pnl REAL, "
        "fees REAL, settlement REAL, slippage REAL);"
    )
    return conn


def test_names_entered_against_names_due_and_the_misses_by_name():
    conn = _ledger()
    for sym, outcome, refusal in (
        ("SPX", "entered", None),
        ("SPY", "entered", None),
        ("QQQ", "missed", "delta_not_bracketed"),
        ("IWM", "pending", None),
    ):
        conn.execute(
            "INSERT INTO weeklyput_entries VALUES ('unhedged', ?, '2026-11-13', ?, ?, ?)",
            (sym, SESSION, outcome, refusal),
        )
    conn.execute("INSERT INTO weeklyput_loop_iterations VALUES (?)", (SESSION,))
    health = facts._weeklyput_health(conn, SESSION)
    assert health["due"] == 4 and health["entered"] == 2 and health["pending"] == 1
    assert health["missed"] == {"unhedged:QQQ": "delta_not_bracketed"}
    assert health["loop_ticked"] is True
    exp = facts._weeklyput_expected(conn, SESSION)
    assert (exp["basis"], exp["expected"], exp["observed"]) == ("names_due_vs_entered", 4, 2)


def test_a_session_with_no_cycle_is_not_a_finding():
    conn = _ledger()
    health = facts._weeklyput_health(conn, "2026-11-09")
    assert health["due"] is None and health["entered"] is None and health["missed"] is None
    exp = facts._weeklyput_expected(conn, "2026-11-09")
    assert exp["expected"] is None and exp["observed"] is None


def test_the_independent_route_counts_closed_puts_on_the_expiry_session():
    conn = _ledger()
    conn.execute("INSERT INTO weeklyput_positions VALUES ('closed', '2026-11-13', 85.0, 1.0, 0.0, 2.0)")
    conn.execute("INSERT INTO weeklyput_positions VALUES ('closed', '2026-11-13', -500.0, 1.0, 6.0, 2.0)")
    conn.execute("INSERT INTO weeklyput_positions VALUES ('open', NULL, NULL, NULL, NULL, NULL)")
    row = conn.execute(reconcile._INDEPENDENT["weeklyput"], ("2026-11-13",)).fetchone()
    assert (row["n"], row["gross"], row["cost"]) == (2, pytest.approx(-415.0), pytest.approx(12.0))
