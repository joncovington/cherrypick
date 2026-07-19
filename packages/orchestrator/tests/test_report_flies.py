"""Tests for the `fly_book` schema reader (cherrypick-flies) in the unified P&L report.

Same read-only lane as test_report.py: build a tiny temp paper DB in the flies schema and assert the
report normalizes it correctly. The attribution tag is the ARM, because comparing the arms is the
entire reason that module exists.
"""

import sqlite3

import pytest

from cherrypick.orchestrator import reconcile, report

pytestmark = pytest.mark.unit


def _flies_db(path, rows):
    """rows: (symbol, arm, entry_mode, kind, gross_pnl, fees, trade_date, status)."""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE fly_positions (id INTEGER PRIMARY KEY, position_id TEXT, symbol TEXT, arm TEXT, "
        "entry_mode TEXT, kind TEXT, gross_pnl REAL, fees REAL, trade_date TEXT, status TEXT)"
    )
    conn.executemany(
        "INSERT INTO fly_positions (position_id, symbol, arm, entry_mode, kind, gross_pnl, fees, "
        "trade_date, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(f"P{i}", *r) for i, r in enumerate(rows)],
    )
    conn.commit()
    conn.close()


def _cfg(tmp_path):
    (tmp_path / "flies").mkdir(exist_ok=True)
    return {
        "modules": {
            "flies": {
                "enabled": True,
                "path": str(tmp_path / "flies"),
                "paper": {"paper_db": "paper.db", "trade_schema": "fly_book"},
            }
        }
    }


def test_reader_nets_gross_pnl_against_fees(tmp_path):
    db = tmp_path / "flies" / "paper.db"
    db.parent.mkdir(exist_ok=True)
    _flies_db(db, [
        ("SPX", "gex", "legged", "fly", 105.0, 6.89, "2026-07-20", "settled"),
        ("SPX", "control", "outright", "fly", -20.0, 6.89, "2026-07-20", "settled"),
    ])
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    records = report._flies_closed(conn)

    assert {r["profile"] for r in records} == {"gex", "control"}
    winner = next(r for r in records if r["profile"] == "gex")
    assert winner["gross_pnl"] == 105.0
    assert winner["cost"] == 6.89
    assert winner["net_pnl"] == pytest.approx(98.11)
    assert winner["strategy"] == "legged"
    assert winner["session"] == "2026-07-20"


def test_reader_ignores_positions_that_have_not_settled(tmp_path):
    """A credit spread still waiting on its completing leg is not a result yet, and counting it as
    one would flatter the arm it belongs to."""
    db = tmp_path / "flies" / "paper.db"
    db.parent.mkdir(exist_ok=True)
    _flies_db(db, [
        ("SPX", "gex", "legged", "short_vertical", None, 3.44, "2026-07-20", "open"),
        ("SPX", "gex", "legged", "fly", 35.0, 6.89, "2026-07-20", "settled"),
    ])
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    assert len(report._flies_closed(conn)) == 1


def test_report_picks_up_the_fly_book_schema(tmp_path):
    """End to end through the registry — the point of the integration, not just the reader."""
    cfg = _cfg(tmp_path)
    _flies_db(tmp_path / "flies" / "paper.db", [
        ("SPX", "gex", "legged", "fly", 105.0, 6.89, "2026-07-20", "settled"),
        ("SPX", "time_window", "legged", "fly", 35.0, 6.89, "2026-07-20", "settled"),
    ])
    out = report.run(cfg)
    assert out["modules"]["flies"]["ok"] is True
    assert out["modules"]["flies"]["trades"] == 2


def test_open_positions_reader_tags_by_arm(tmp_path):
    """reconcile's view: open paper positions, attributed to the arm holding them."""
    db = tmp_path / "flies" / "paper.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    _flies_db(db, [
        ("SPX", "gex", "legged", "short_vertical", None, 3.44, "2026-07-20", "open"),
        ("SPX", "control", "legged", "fly", 35.0, 6.89, "2026-07-20", "settled"),
    ])
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    assert reconcile._flies_open(conn) == [{"symbol": "SPX", "profile": "gex"}]


def test_schema_is_registered_in_every_registry():
    """All four dispatch points must know the schema, or the module silently vanishes from one
    surface — the kind of gap that only shows up weeks later when a report comes back empty."""
    from cherrypick.orchestrator import trade_notifier

    assert "fly_book" in report._READERS
    assert "fly_book" in reconcile._OPEN_READERS
    assert "fly_book" in trade_notifier._SCHEMAS
    # calibrate dispatches through report._READERS rather than keeping its own table.
    from cherrypick.orchestrator import calibrate

    assert calibrate.report._READERS is report._READERS
