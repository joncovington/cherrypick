"""core.analysis.windows: each arm read over its own era where the breaks name arms, else the suite's."""

import argparse
import sqlite3

from cherrypick.core.analysis import __main__ as cli
from cherrypick.core.analysis import windows


def _b(date, scope, kind="arm_added", reason="r"):
    return {"break_date": date, "scope": scope, "kind": kind, "reason": reason}


def test_an_arm_reads_from_its_own_break():
    breaks = [_b("2026-09-29", "*", "completion_rule"), _b("2026-10-12", "range-high")]
    w = windows.arm_windows(breaks, "scoped", ["control", "range-high"], "2026-10-16", "2026-08-21")
    assert w["range-high"]["scope"] == "arm_era" and w["range-high"]["start"] == "2026-10-12"
    assert w["range-high"]["reason"] == "its own break"
    # No break of its own: the module's latest book-wide break.
    assert w["control"]["start"] == "2026-09-29"
    assert w["control"]["reason"] == "the module's latest book-wide break"
    assert w["control"]["break"]["kind"] == "completion_rule"


def test_the_suite_window_is_a_floor():
    w = windows.arm_windows([_b("2026-08-01", "*", "x")], "scoped", ["control"], "2026-10-16", "2026-08-21")
    assert w["control"] == {
        "scope": "suite",
        "start": "2026-08-21",
        "break": None,
        "reason": "no measurement break since the suite window opened",
    }


def test_a_break_dated_after_the_latest_session_does_not_bind_yet():
    breaks = [_b("2026-12-18", "*", "triple_witching_gate")]
    w = windows.arm_windows(breaks, "scoped", ["control"], "2026-10-16", "2026-08-21")
    assert w["control"]["scope"] == "suite" and w["control"]["start"] == "2026-08-21"


def test_an_outage_caveats_and_never_starts_an_era():
    breaks = [_b("2026-10-08", "*", "partial_session")]
    w = windows.arm_windows(breaks, "scoped", ["control"], "2026-10-16", "2026-08-21")
    assert w["control"]["scope"] == "suite"


def test_a_keyed_journal_reads_the_suite_window_and_says_why():
    w = windows.arm_windows([], "keyed", ["control"], "2026-10-16", "2026-08-21")
    assert w["control"]["scope"] == "suite" and w["control"]["start"] == "2026-08-21"
    assert "no arm named" in w["control"]["reason"]


def test_read_breaks_tells_the_two_schemas_apart():
    scoped = sqlite3.connect(":memory:")
    scoped.execute("CREATE TABLE measurement_breaks (break_date TEXT, scope TEXT, kind TEXT, reason TEXT)")
    scoped.execute("INSERT INTO measurement_breaks VALUES ('2026-10-12','range-high','arm_added','x')")
    rows, schema = windows.read_breaks(scoped)
    assert schema == "scoped" and rows[0]["scope"] == "range-high"
    keyed = sqlite3.connect(":memory:")
    keyed.execute(
        "CREATE TABLE measurement_breaks (break_date TEXT, key TEXT, old_value TEXT, new_value TEXT)"
    )
    assert windows.read_breaks(keyed) == ([], "keyed")
    assert windows.read_breaks(sqlite3.connect(":memory:")) == ([], "none")


# --------------------------------------------------------------------------- through the CLI
def _flies(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE fly_positions (symbol TEXT, arm TEXT, entry_mode TEXT, gross_pnl REAL, fees REAL, "
        "trade_date TEXT, status TEXT)"
    )
    rows = [("control", d) for d in ("2026-10-01", "2026-10-13", "2026-10-14")]
    rows += [("range-high", d) for d in ("2026-10-01", "2026-10-13", "2026-10-14")]
    conn.executemany("INSERT INTO fly_positions VALUES ('SPX', ?, 'legged', 20.0, 5.0, ?, 'settled')", rows)
    conn.execute("CREATE TABLE measurement_breaks (break_date TEXT, scope TEXT, kind TEXT, reason TEXT)")
    conn.execute("INSERT INTO measurement_breaks VALUES ('2026-10-12','range-high','arm_added','enters')")
    conn.commit()
    conn.close()


def _args(tmp_path, **kw):
    base = {"db": str(tmp_path / "f.db"), "schema": "fly_book", "start": "2026-08-21", "end": None,
            "era": None, "module": None, "allocations": None, "window": "arm"}  # fmt: skip
    base.update(kw)
    return argparse.Namespace(**base)


def test_the_cli_reads_each_arm_over_its_own_era(tmp_path):
    _flies(tmp_path / "f.db")
    out = cli.cmd_read(_args(tmp_path))
    rh, control = out["groups"]["range-high"], out["groups"]["control"]
    assert rh["window"]["start"] == "2026-10-12" and rh["reading"]["sample"] == 2
    assert control["window"]["scope"] == "suite" and control["reading"]["sample"] == 3
    assert out["window"] == {"mode": "arm", "as_of": "2026-10-14", "breaks": "scoped", "floor": "2026-08-21"}
    assert out["n_records"] == 5


def test_the_cli_reads_the_suite_window_when_asked(tmp_path):
    _flies(tmp_path / "f.db")
    out = cli.cmd_read(_args(tmp_path, window="suite"))
    assert out["groups"]["range-high"]["reading"]["sample"] == 3
    assert out["groups"]["range-high"]["window"]["scope"] == "suite"
