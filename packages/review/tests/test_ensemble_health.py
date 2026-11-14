"""ensemble's health, expectation and independent reconcile route, over a ledger shaped as the module
writes it (the session register, the positions, the loop's iterations)."""

import sqlite3

from cherrypick.review import facts, reconcile

SESSION = "2026-11-16"


def _ledger():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE ensemble_entries (arm TEXT, symbol TEXT, entry_session TEXT, outcome TEXT, "
        "refusal TEXT);"
        "CREATE TABLE ensemble_loop_iterations (session_date TEXT);"
        "CREATE TABLE ensemble_positions (status TEXT, exit_session TEXT, gross_pnl REAL, fees REAL, "
        "settlement REAL, slippage REAL);"
    )
    return conn


def _day(conn, rows):
    for arm, outcome, refusal in rows:
        conn.execute(
            "INSERT INTO ensemble_entries VALUES (?, 'SPX', ?, ?, ?)", (arm, SESSION, outcome, refusal)
        )
    conn.execute("INSERT INTO ensemble_loop_iterations VALUES (?)", (SESSION,))


def test_arms_that_declined_on_their_rules_are_decisions_and_input_misses_are_named():
    conn = _ledger()
    _day(
        conn,
        [
            ("a", "entered", None),
            ("b", "missed", "gate:sd_change"),  # the arm did its job
            ("c", "missed", "trail_incomplete"),  # the loop did not
            ("d", "missed", "no_tick_in_window"),
            ("e", "pending", None),
        ],
    )
    health = facts._ensemble_health(conn, SESSION)
    assert health["due"] == 5 and health["entered"] == 1 and health["declined"] == 1
    assert health["missed"] == {"c": "trail_incomplete", "d": "no_tick_in_window"}
    assert health["pending"] == 1 and health["loop_ticked"] is True
    exp = facts._ensemble_expected(conn, SESSION)
    assert (exp["basis"], exp["expected"], exp["observed"]) == ("arm_days_due_vs_decided", 5, 2)


def test_a_session_on_which_no_arm_trades_is_not_a_finding():
    conn = _ledger()
    health = facts._ensemble_health(conn, "2026-11-14")
    assert health["due"] is None and health["entered"] is None and health["missed"] is None
    exp = facts._ensemble_expected(conn, "2026-11-14")
    assert exp["expected"] is None and exp["observed"] is None


def test_the_independent_route_counts_closed_trades_on_the_day_they_ended():
    conn = _ledger()
    conn.execute("INSERT INTO ensemble_positions VALUES ('closed', ?, 85.0, 1.0, 0.0, 2.0)", (SESSION,))
    conn.execute("INSERT INTO ensemble_positions VALUES ('closed', ?, -500.0, 1.0, 6.0, 2.0)", (SESSION,))
    conn.execute("INSERT INTO ensemble_positions VALUES ('open', NULL, NULL, NULL, NULL, NULL)")
    row = conn.execute(reconcile._INDEPENDENT["ensemble"], (SESSION,)).fetchone()
    assert (row["n"], row["gross"], row["cost"]) == (2, -415.0, 12.0)
    assert facts._ensemble_health(conn, SESSION)["settled"] == 2


def test_the_module_is_registered_for_the_review():
    assert facts.MODULES["ensemble"]["schema"] == "ensemble_trade"
    assert facts.HEALTH_READERS["ensemble"] is facts._ensemble_health
    assert facts.EXPECTED_READERS["ensemble"] is facts._ensemble_expected
