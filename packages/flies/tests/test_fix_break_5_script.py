"""scripts/flies_fix_break_5.py: dry run by default, backs up before writing, refuses on contrary evidence."""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from cherrypick.flies import db as dbmod

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "flies_fix_break_5.py"
REASON = (
    "live concurrency: the one-incomplete-at-a-time rule removed (live.max_incomplete_spreads null); "
    "max_open_margin_dollars 1000 becomes the sole sizing gate."
)


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("flies_fix_break_5", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _refusal(conn, day, n=3):
    for i in range(n):
        dbmod.record_decision(
            conn,
            trade_date=day,
            arm="control",
            symbol="SPX",
            mode="entry",
            reason="incomplete_spread_blocks",
            when=f"{day}T1{i}:31:00-04:00",
        )


def _decision(conn, day, reason="entered"):
    dbmod.record_decision(
        conn,
        trade_date=day,
        arm="control",
        symbol="SPX",
        mode="entry",
        reason=reason,
        accepted=True,
        when=f"{day}T10:30:00-04:00",
    )


@pytest.fixture
def ledger(tmp_path):
    path = tmp_path / "live_trades.db"
    conn = dbmod.connect(str(path))
    for i in range(1, 5):
        dbmod.record_measurement_break(
            conn, break_date=f"2026-09-0{i}", scope="live", kind="entry_rules", reason=f"filler {i}"
        )
    dbmod.record_measurement_break(
        conn, break_date="2026-09-25", scope="live", kind="entry_rules", reason=REASON, detail="note"
    )
    _refusal(conn, "2026-09-24")
    _refusal(conn, "2026-09-25")
    _decision(conn, "2026-09-25")
    _decision(conn, "2026-09-28")
    conn.close()
    return path


def _row(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT * FROM measurement_breaks WHERE id = 5").fetchone())
    finally:
        conn.close()


def test_the_default_is_a_dry_run_that_writes_nothing(script, ledger):
    out = script.run(ledger, apply=False, today="2026-10-12")
    assert out["status"] == "ready" and out["applied"] is False
    assert out["before"]["break_date"] == "2026-09-25"
    assert out["after"]["break_date"] == "2026-09-28"
    assert out["evidence"]["derived_date"] == "2026-09-28"
    assert _row(ledger)["break_date"] == "2026-09-25"
    assert not list(ledger.parent.glob("*.bak-pre-break5-fix-*"))


def test_apply_backs_up_first_then_corrects_the_one_row(script, ledger):
    out = script.run(ledger, apply=True, today="2026-10-12")
    assert out["applied"] is True
    backup = Path(out["backup"])
    assert backup.exists()
    old = sqlite3.connect(backup)
    assert old.execute("SELECT break_date FROM measurement_breaks WHERE id = 5").fetchone()[0] == "2026-09-25"
    old.close()
    row = _row(ledger)
    assert row["break_date"] == "2026-09-28"
    assert "corrected to 09-28 on 2026-10-12" in row["reason"] and "3 refusals" in row["reason"]
    assert row["detail"] == "note" and row["scope"] == "live"
    # A second run is a no-op.
    assert script.run(ledger, apply=True)["status"] == "already_correct"


def test_refuses_when_the_rule_still_bound_after_the_recorded_date(script, ledger):
    """The guard: if the ledger does not show the rule's last refusal on 09-25 and cap-only from 09-28,
    the correction is not what the data says, and nothing is written."""
    conn = dbmod.connect(str(ledger))
    _refusal(conn, "2026-09-28")
    conn.close()
    out = script.run(ledger, apply=True, today="2026-10-12")
    assert out["status"] == "refused" and out["applied"] is False
    assert "last incomplete_spread_blocks session" in out["why"]
    assert _row(ledger)["break_date"] == "2026-09-25"
    assert not list(ledger.parent.glob("*.bak-pre-break5-fix-*"))


def test_refuses_a_row_that_is_not_the_concurrency_break(script, ledger):
    conn = sqlite3.connect(ledger)
    conn.execute("UPDATE measurement_breaks SET reason = 'something else' WHERE id = 5")
    conn.commit()
    conn.close()
    assert script.run(ledger, apply=True)["status"] == "refused"
