"""The shared calibration-reading CLI (python -m cherrypick.core.metrics): a JSON wrapper over
ledgers.READERS + profiles.group_by_tag + calibration_reading, for a read-only TypeScript
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


def test_stamped_advised_rows_group_per_experiment_and_unstamped_stay_on_the_tag(tmp_path):
    """Three experiments ran on `advised:control` in turn; the tag pools them, the stamp splits
    them. Rows from before the stamp existed stay on the bare tag -- never inferred by date."""
    path = tmp_path / "meic_paper.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE ic_trades (symbol TEXT, risk_profile TEXT, pnl REAL, fees REAL, exit_time TEXT, "
        "experiment_id TEXT)"
    )
    conn.executemany(
        "INSERT INTO ic_trades VALUES (?,?,?,?,?,?)",
        [
            ("SPX", "control", 10.0, 1.0, "2026-09-01T15:45:00", None),
            ("SPX", "advised:control", 20.0, 1.0, "2026-09-01T15:45:00", None),  # pre-stamp
            ("SPX", "advised:control", 30.0, 1.0, "2026-09-10T15:45:00", "exp-2026-09-09-meic-1"),
            ("SPX", "advised:control", -5.0, 1.0, "2026-09-11T15:45:00", "exp-2026-09-09-meic-1"),
            ("SPX", "advised:control", 7.0, 1.0, "2026-09-15T15:45:00", "exp-2026-09-14-meic-1"),
        ],
    )
    conn.commit()
    conn.close()
    out = cli.cmd_read(_args(db=str(path), schema="meic_ic"))
    assert out["ok"]
    assert set(out["groups"]) == {
        "control",
        "advised:control",
        "advised:control@exp-2026-09-09-meic-1",
        "advised:control@exp-2026-09-14-meic-1",
    }
    assert out["groups"]["advised:control@exp-2026-09-09-meic-1"]["trade_nets"] == [29.0, -6.0]
    assert out["groups"]["advised:control"]["trade_nets"] == [19.0]


def test_a_ledger_without_the_column_reads_with_no_experiment(meic_db):
    out = cli.cmd_read(_args(db=meic_db, schema="meic_ic"))
    assert out["ok"] and "@" not in "".join(out["groups"])


def _pmcc_db(tmp_path, with_era=True):
    path = tmp_path / "pmcc_paper.db"
    conn = sqlite3.connect(path)
    era_col = ", era TEXT" if with_era else ""
    conn.execute(
        "CREATE TABLE pmcc_positions (symbol TEXT, arm TEXT, status TEXT, gross_pnl REAL, fees REAL, "
        "entry_slippage REAL, exit_slippage REAL, net_debit REAL, quantity INTEGER, "
        f"closed_session TEXT{era_col})"
    )
    rows = [
        ("TQQQ", "keltner", "closed", 50.0, 5.0, 1.0, 1.0, 20.0, 1, "2026-08-22", None),  # pre-stamp
        ("TQQQ", "control", "closed", 80.0, 5.0, 1.0, 1.0, 20.0, 1, "2026-09-10", "redesign"),
        ("XSP", "control", "closed", -40.0, 5.0, 1.0, 1.0, 20.0, 1, "2026-10-09", "redesign"),
        ("XSP", "shield", "closed", 30.0, 5.0, 1.0, 1.0, 20.0, 1, "2026-10-09", "shield"),
        ("XSP", "shield", "open", 0.0, 0.0, 1.0, None, 20.0, 1, None, "shield"),  # open, excluded
    ]
    cols = 11 if with_era else 10
    conn.executemany(f"INSERT INTO pmcc_positions VALUES ({','.join('?' * cols)})", [r[:cols] for r in rows])
    conn.commit()
    conn.close()
    return str(path)


def test_era_scopes_pmcc_by_the_stamp_not_the_close_date(tmp_path):
    """pmcc's eras are roster changes stamped at entry: a redesign position closing after the shield
    boundary is still redesign, and an unstamped row never matches a stamped era."""
    db = _pmcc_db(tmp_path)
    shield = cli.cmd_read(_args(db=db, schema="pmcc", era="shield"))
    assert shield["ok"] and shield["n_records"] == 1 and set(shield["groups"]) == {"shield"}
    redesign = cli.cmd_read(_args(db=db, schema="pmcc", era="redesign"))
    assert redesign["n_records"] == 2 and redesign["groups"]["control"]["trade_nets"] == [75.0, -45.0]
    every_era = cli.cmd_read(_args(db=db, schema="pmcc", era="ALL"))
    for everything in (every_era, cli.cmd_read(_args(db=db, schema="pmcc"))):
        assert everything["n_records"] == 4


def test_era_on_a_pmcc_ledger_from_before_the_column_matches_nothing(tmp_path):
    db = _pmcc_db(tmp_path, with_era=False)
    out = cli.cmd_read(_args(db=db, schema="pmcc", era="shield"))
    assert out["ok"] and out["n_records"] == 0


def test_era_on_a_schema_without_one_is_refused_not_ignored(meic_db):
    out = cli.cmd_read(_args(db=meic_db, schema="meic_ic", era="current"))
    assert out["ok"] is False and "no era" in out["error"]
