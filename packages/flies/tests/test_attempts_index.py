"""`fly_entry_attempts` carries a covering (arm, trade_date, outcome) index, and an older ledger gets it.

The console's attempts rail counts each arm's sessions seen and filled over the whole table; without
this index that was a full scan of ~508k rows on every page load. The plan check is what fails if the
index goes missing or stops covering the read.
"""

import sqlite3

from cherrypick.flies import db as dbmod

RAIL = (
    "SELECT arm, COUNT(DISTINCT trade_date), "
    "COUNT(DISTINCT CASE WHEN outcome = 'filled' THEN trade_date END) "
    "FROM fly_entry_attempts WHERE arm = ? GROUP BY 1"
)


def _plan(conn) -> str:
    return " | ".join(str(r[3]) for r in conn.execute("EXPLAIN QUERY PLAN " + RAIL, ("control",)))


def test_the_per_arm_rail_read_is_served_from_a_covering_index(tmp_path):
    conn = dbmod.connect(str(tmp_path / "paper_trades.db"))
    plan = _plan(conn)
    assert "COVERING INDEX idx_fly_attempts_arm_date_outcome" in plan, plan


def test_a_ledger_opened_before_the_index_existed_gains_it_on_connect(tmp_path):
    path = str(tmp_path / "paper_trades.db")
    dbmod.connect(path).close()
    old = sqlite3.connect(path)
    old.execute("DROP INDEX idx_fly_attempts_arm_date_outcome")
    old.commit()
    old.close()

    conn = dbmod.connect(path)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert "idx_fly_attempts_arm_date_outcome" in names
