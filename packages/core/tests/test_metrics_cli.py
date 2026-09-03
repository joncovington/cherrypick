"""The shared calibration-reading CLI (python -m cherrypick.core.metrics): a JSON wrapper over
ledgers.READERS + profiles.compare_profiles + calibration_reading, for a read-only TypeScript
bridge (the console) that cannot import Python directly.
"""

import argparse
import sqlite3

import pytest

from cherrypick.core.metrics import __main__ as cli
from cherrypick.core.metrics import session_nets_dated


def _args(**kw):
    defaults = {"start": None, "end": None}
    defaults.update(kw)
    return argparse.Namespace(**defaults)


@pytest.fixture
def meic_db(tmp_path):
    path = tmp_path / "meic_paper.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE ic_trades (symbol TEXT, risk_profile TEXT, pnl REAL, fees REAL, "
        "exit_time TEXT, slippage_dollars REAL, wing_width REAL, net_credit REAL, "
        "quantity INTEGER, dollar_multiplier REAL)"
    )
    rows = [
        ("SPX", "control", 120.0, 5.0, "2026-08-20T15:45:00", 2.0, 10.0, 3.0, 1, 100.0),
        ("SPX", "control", -60.0, 5.0, "2026-08-21T15:45:00", 2.0, 10.0, 3.0, 1, 100.0),
        ("SPX", "aggressive", 300.0, 8.0, "2026-08-20T15:45:00", 3.0, 20.0, 6.0, 1, 100.0),
        ("SPX", None, 10.0, 1.0, "2026-08-19T15:45:00", None, None, None, None, None),  # untagged
        ("SPX", "control", 0.0, 0.0, None, None, None, None, None, None),  # still open, excluded
    ]
    conn.executemany(
        "INSERT INTO ic_trades (symbol, risk_profile, pnl, fees, exit_time, slippage_dollars, "
        "wing_width, net_credit, quantity, dollar_multiplier) VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return str(path)


def test_read_groups_by_profile_and_excludes_open_trades(meic_db):
    out = cli.cmd_read(_args(db=meic_db, schema="meic_ic"))
    assert out["ok"] is True
    assert out["schema"] == "meic_ic"
    assert out["n_records"] == 4  # the still-open row (exit_time NULL) is excluded
    assert set(out["groups"].keys()) == {"control", "aggressive", "unassigned"}


def test_control_group_reading_matches_calibration_reading_shape(meic_db):
    out = cli.cmd_read(_args(db=meic_db, schema="meic_ic"))
    control = out["groups"]["control"]
    # net_pnl: (120-5) + (-60-5) = 50
    assert control["reading"]["sample"] == 2
    assert control["reading"]["net_pnl"] == 50.0
    assert control["trade_nets"] == [115.0, -65.0]


def test_session_nets_are_date_paired_and_match_session_nets_dated(meic_db):
    out = cli.cmd_read(_args(db=meic_db, schema="meic_ic"))
    control = out["groups"]["control"]
    assert control["session_nets"] == [["2026-08-20", 115.0], ["2026-08-21", -65.0]]
    # Cross-checked against the pure function directly, not re-derived here.
    records = [
        {"session": "2026-08-20", "net_pnl": 115.0},
        {"session": "2026-08-21", "net_pnl": -65.0},
    ]
    assert [list(p) for p in session_nets_dated(records)] == control["session_nets"]


def test_start_end_bounds_are_pushed_into_the_reader(meic_db):
    out = cli.cmd_read(_args(db=meic_db, schema="meic_ic", start="2026-08-21", end="2026-08-21"))
    assert out["n_records"] == 1
    assert out["groups"]["control"]["reading"]["sample"] == 1


def test_unknown_schema_fails_cleanly():
    out = cli.cmd_read(_args(db="ignored.db", schema="not_a_real_schema"))
    assert out["ok"] is False
    assert "unknown schema" in out["error"]


def test_unreadable_db_fails_cleanly_not_a_traceback(tmp_path):
    missing = tmp_path / "does_not_exist.db"
    out = cli.cmd_read(_args(db=str(missing), schema="meic_ic"))
    assert out["ok"] is False
    assert "cannot read" in out["error"]
