"""The paired-debit record wired through the real Monday entry path (2026-10-12).

`_try_entry` rebound `arms` before testing `arms is None`, so the record never ran: dc_paired_debits
held no row from 2026-08-24 to 10-12 although the Friday regime traded four weeks. The tests in
test_friday_entry.py call `_record_paired_debits` directly and could not see it; these go through
`_try_entry`, the way a Monday tick does.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from test_friday_entry import _snapshot

from cherrypick.calendars import clock, db, paper_loop, provider

ET = clock.ET


@pytest.fixture()
def conn(tmp_path):
    return db.connect(str(tmp_path / "cal.db"))


def _friday_row(conn):
    db.save_position(
        conn,
        {
            "position_id": "2026-08-31:friday:control:put",
            "week_of": "2026-08-31",
            "entry_session": "2026-08-28",
            "arm": "friday:control",
            "side": "put",
            "symbol": "SPX",
            "structure": "dc_7_11",
            "front_expiration": "2026-09-04",
            "back_expiration": "2026-09-08",
            "strike": 750.0,
            "quantity": 1,
            "entry_debit": 1.20,
            "entry_spot": 758.0,
            "entry_time": "2026-08-28T15:55:00-04:00",
            "status": "open",
        },
    )


def _monday(monkeypatch, conn, tmp_path, *, arms=None):
    snap = {**_snapshot({750.0: (1.0, 2.5)}), "ok": True, "quote_stats": {"fresh": 4, "rejected": 0}}
    monkeypatch.setattr(provider, "build_entry_snapshot", lambda *a, **k: snap)
    week = {
        "week_of": "2026-08-31",
        "entry_session": "2026-08-31",
        "front_expiration": "2026-09-04",
        "back_expiration": "2026-09-08",
        "structure": "dc_4_7",
    }
    cfg = {"symbols": ["SPX"], "arms": {"control": {"enabled": True}}}
    paper_loop._try_entry(
        cfg,
        conn,
        cache_path=str(tmp_path / "none.db"),
        when=datetime(2026, 8, 31, 10, 0, tzinfo=ET),
        week=week,
        arms=arms,
    )


def test_the_monday_pass_records_the_pair(conn, tmp_path, monkeypatch):
    _friday_row(conn)
    _monday(monkeypatch, conn, tmp_path)
    rows = db.paired_debits(conn, "2026-08-31")
    assert len(rows) == 1 and rows[0]["usable"] == 1
    assert rows[0]["friday_debit"] == 1.20 and rows[0]["monday_debit"] == 1.5


def test_a_non_default_regimes_pass_records_nothing(conn, tmp_path, monkeypatch):
    """The Friday regime's own entry passes its arms; only the default regime's pass is the Monday
    moment."""
    _friday_row(conn)
    _monday(monkeypatch, conn, tmp_path, arms=["friday:control"])
    assert db.paired_debits(conn, "2026-08-31") == []


def test_a_failed_write_is_reported_not_swallowed(conn, capsys):
    conn.execute("DROP TABLE dc_paired_debits")
    db.record_paired_debit(conn, week_of="2026-08-31", side="put")
    assert "paired-debit row not recorded" in capsys.readouterr().err


def test_record_break_writes(tmp_path):
    """The one writing CLI verb opened the ledger read-only from 2026-09-24 and raised."""
    import argparse

    from cherrypick.calendars import cli

    path = str(tmp_path / "cal.db")
    db.connect(path).close()
    args = argparse.Namespace(db=path, date="2026-10-19", key="advice_bounds", old="a", new="b", note="n")
    assert cli.cmd_record_break(args) == 0
    row = db.connect(path).execute("SELECT break_date, key FROM measurement_breaks").fetchone()
    assert tuple(row) == ("2026-10-19", "advice_bounds")
