"""The helpers the paper loops share: late-settlement legs, minutes since a stamp, the decision
window, the advised-arm map and the cadence-change journal.

Each was a copy in several modules; these pin what every copy did so the shared one cannot drift.
"""

import json
import sqlite3
from datetime import date, datetime

import pytest

from cherrypick.core import advice, cadence, clock
from cherrypick.core import db as coredb

# ----------------------------------------------------------------------------- overdue_legs


def _ledger(prefix: str) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        f"""
        CREATE TABLE {prefix}_positions (position_id INTEGER PRIMARY KEY, status TEXT);
        CREATE TABLE {prefix}_legs (position_id INTEGER, leg_role TEXT, expiration TEXT, status TEXT);
        """
    )
    return conn


def test_overdue_legs_names_open_legs_of_open_positions_expired_before_the_day():
    conn = _ledger("dc")
    conn.executemany("INSERT INTO dc_positions VALUES (?, ?)", [(1, "open"), (2, "closed"), (3, "open")])
    conn.executemany(
        "INSERT INTO dc_legs VALUES (?, ?, ?, ?)",
        [
            (1, "short", "2026-11-13", "open"),  # overdue: the one row that should come back
            (1, "long", "2026-11-20", "open"),  # not yet expired
            (1, "wing", "2026-11-12", "closed"),  # already settled leg
            (2, "short", "2026-11-10", "open"),  # its position is closed
            (3, "short", "2026-11-16", "open"),  # expires on the day itself: not overdue yet
        ],
    )
    assert coredb.overdue_legs(conn, "dc", "2026-11-16") == [
        {"position_id": 1, "leg_role": "short", "expiration": "2026-11-13"}
    ]


def test_overdue_legs_refuses_a_prefix_that_is_not_a_table_prefix():
    conn = _ledger("dc")
    for bad in ("dc; DROP TABLE dc_legs", "DC", "", "dc legs", "1dc"):
        with pytest.raises(ValueError):
            coredb.overdue_legs(conn, bad, "2026-11-16")


def test_overdue_legs_on_a_missing_prefix_fails_loudly():
    with pytest.raises(sqlite3.OperationalError):
        coredb.overdue_legs(_ledger("dc"), "curve", "2026-11-16")


# ----------------------------------------------------------------------------- minutes_since


def test_minutes_since_reads_a_naive_stamp_as_eastern():
    when = datetime(2026, 11, 16, 10, 30, tzinfo=clock.ET)
    assert clock.minutes_since("2026-11-16T10:00:00", when) == 30.0


def test_minutes_since_honors_an_explicit_offset():
    when = datetime(2026, 11, 16, 10, 30, tzinfo=clock.ET)
    assert clock.minutes_since("2026-11-16T15:00:00+00:00", when) == 30.0  # 10:00 ET


def test_minutes_since_is_none_for_a_missing_or_unreadable_stamp():
    when = datetime(2026, 11, 16, 10, 30, tzinfo=clock.ET)
    assert clock.minutes_since(None, when) is None
    assert clock.minutes_since("", when) is None
    assert clock.minutes_since("not a time", when) is None


# ----------------------------------------------------------------------------- decision window

CONTANGO = {"decision_minutes_before_close": 10, "decision_window_minutes": 8}
WEEKLYPUT = {"decision_minutes_before_close": 15, "decision_window_minutes": 10}


def test_close_min_follows_the_early_close():
    assert clock.close_min(date(2026, 11, 16)) == 16 * 60
    assert clock.close_min(date(2026, 11, 27)) == 13 * 60  # the day after Thanksgiving


def test_decision_window_opens_before_the_close_with_each_modules_own_defaults():
    assert clock.decision_window(date(2026, 11, 16), None, CONTANGO) == (15 * 60 + 50, 15 * 60 + 58)
    assert clock.decision_window(date(2026, 11, 16), None, WEEKLYPUT) == (15 * 60 + 45, 15 * 60 + 55)
    assert clock.decision_window(date(2026, 11, 27), None, WEEKLYPUT) == (12 * 60 + 45, 12 * 60 + 55)


def test_decision_window_reads_only_its_own_keys_from_params():
    params = {"decision_minutes_before_close": 20, "decision_window_minutes": 5, "close_min": 1}
    assert clock.decision_window(date(2026, 11, 16), params, CONTANGO) == (15 * 60 + 40, 15 * 60 + 45)


# ----------------------------------------------------------------------------- advised_entries


def test_advised_entries_keys_each_experiment_by_tag_in_order_and_drops_untagged():
    decision = {
        "experiments": [
            {"tag": "advised:b", "params": {"x": 1}},
            {"params": {"x": 2}},  # no tag: not an arm
            {"tag": "advised:a", "params": {"x": 3}},
        ]
    }
    assert list(advice.advised_entries(decision)) == ["advised:b", "advised:a"]
    assert advice.advised_entries(None) == {}
    assert advice.advised_entries({}) == {}


# ----------------------------------------------------------------------------- cadence


def _note(tmp_path, seconds, rows, logs, note="resolution changed"):
    def record(conn, **row):
        rows.append(row)

    cadence.note_change(
        None, seconds, data_dir=str(tmp_path), record_break=record, note=note, log=logs.append
    )


def test_first_cadence_writes_the_state_and_no_row(tmp_path):
    rows, logs = [], []
    _note(tmp_path, 60, rows, logs)
    assert rows == [] and logs == []
    state = json.loads((tmp_path / "tick_cadence.json").read_text(encoding="utf-8"))
    assert state["seconds"] == 60 and state["since"] == clock.today_iso()


def test_an_unchanged_cadence_writes_nothing(tmp_path):
    rows, logs = [], []
    _note(tmp_path, 60, rows, logs)
    _note(tmp_path, 60, rows, logs)
    assert rows == [] and logs == []


def test_a_changed_cadence_journals_one_break_with_the_exact_row_shape(tmp_path):
    rows, logs = [], []
    _note(tmp_path, 60, rows, logs)
    _note(tmp_path, 30, rows, logs, note="resolution changed")
    _note(tmp_path, 30, rows, logs)  # once only
    assert rows == [
        {
            "break_date": clock.today_iso(),
            "key": "tick_cadence",
            "old_value": "60",
            "new_value": "30",
            "note": "resolution changed",
        }
    ]
    assert logs == ["tick cadence changed 60s -> 30s — journaled as a measurement break"]
    assert json.loads((tmp_path / "tick_cadence.json").read_text(encoding="utf-8"))["seconds"] == 30


def test_a_failure_in_the_journal_is_logged_and_swallowed(tmp_path):
    logs = []

    def broken(conn, **row):
        raise RuntimeError("ledger locked")

    (tmp_path / "tick_cadence.json").write_text(json.dumps({"seconds": 60}), encoding="utf-8")
    cadence.note_change(None, 30, data_dir=str(tmp_path), record_break=broken, note="n", log=logs.append)
    assert len(logs) == 1 and logs[0].startswith("cadence-change journaling failed (non-fatal): RuntimeError")
    # the state stays at the old cadence, so the next tick tries the row again
    assert json.loads((tmp_path / "tick_cadence.json").read_text(encoding="utf-8"))["seconds"] == 60
