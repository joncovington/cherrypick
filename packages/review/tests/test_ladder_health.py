"""ladder's health, expectation and independent reconcile route, over a ledger shaped as the module
writes it (the lots, the feed's snapshots, the loop's iterations)."""

import sqlite3

from cherrypick.review import facts, reconcile

SESSION = "2026-11-16"


def _ledger():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE ladder_positions (status TEXT, buy_session TEXT, sell_session TEXT, gross_pnl REAL, "
        "fees REAL, settlement REAL, slippage REAL);"
        "CREATE TABLE ladder_snapshots (trade_date TEXT, status TEXT);"
        "CREATE TABLE ladder_loop_iterations (session_date TEXT);"
    )
    return conn


def test_a_quiet_session_is_the_rule_working_and_a_missing_quote_is_named():
    conn = _ledger()
    for status in ("ok", "ok", "no_fresh_quote"):
        conn.execute("INSERT INTO ladder_snapshots VALUES (?, ?)", (SESSION, status))
    for _ in range(3):
        conn.execute("INSERT INTO ladder_loop_iterations VALUES (?)", (SESSION,))
    conn.execute("INSERT INTO ladder_positions VALUES ('open', '2026-11-10', NULL, NULL, NULL, NULL, NULL)")
    health = facts._ladder_health(conn, SESSION)
    assert health["loop_ticked"] is True and health["iterations"] == 3
    assert health["no_quote_ticks"] == 1
    assert (health["bought"], health["settled"], health["open_lots"]) == (0, 0, 1)
    exp = facts._ladder_expected(conn, SESSION)
    assert (exp["basis"], exp["expected"], exp["observed"]) == ("levels_reached_only", None, None)


def test_a_session_the_loop_never_ticked_says_so():
    health = facts._ladder_health(_ledger(), "2026-11-14")
    assert health["loop_ticked"] is False and health["no_quote_ticks"] is None


def test_the_independent_route_counts_lots_sold_on_the_day_they_were_sold():
    conn = _ledger()
    conn.execute(
        "INSERT INTO ladder_positions VALUES ('closed', '2026-11-09', ?, 40.0, 0.5, 0.0, 0.0)", (SESSION,)
    )
    conn.execute(
        "INSERT INTO ladder_positions VALUES ('closed', ?, ?, 20.0, 0.25, 0.0, 0.0)", (SESSION, SESSION)
    )
    conn.execute("INSERT INTO ladder_positions VALUES ('open', ?, NULL, NULL, NULL, NULL, NULL)", (SESSION,))
    row = conn.execute(reconcile._INDEPENDENT["ladder"], (SESSION,)).fetchone()
    assert (row["n"], row["gross"], row["cost"]) == (2, 60.0, 0.75)
    health = facts._ladder_health(conn, SESSION)
    assert health["settled"] == 2 and health["bought"] == 2


def test_the_module_is_registered_for_the_review():
    assert facts.MODULES["ladder"]["schema"] == "ladder_lot"
    assert facts.HEALTH_READERS["ladder"] is facts._ladder_health
    assert facts.EXPECTED_READERS["ladder"] is facts._ladder_expected
