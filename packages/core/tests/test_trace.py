"""core.trace: how a position got in, and what was decided while it was held.

The guards, each broken on purpose while this was written: the attempts shown are only those since
the arm's previous fill (never the whole session); a linked attempt wins over a time match; an
executed management event is never folded into the run of times it was only considered.
"""

from __future__ import annotations

import json
import sqlite3

from cherrypick.core import trace
from cherrypick.core.trace import __main__ as cli


def _bwb(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "bwb.db")
    conn.execute(
        "CREATE TABLE bwb_positions (position_id TEXT, arm TEXT, symbol TEXT, entry_time TEXT, "
        "entry_session TEXT)"
    )
    conn.execute(
        "CREATE TABLE bwb_entry_attempts (id INTEGER PRIMARY KEY, ts TEXT, trade_date TEXT, symbol TEXT, "
        "arm TEXT, "
        "outcome TEXT, block_detail TEXT, body_strike REAL, credit REAL)"
    )
    conn.execute(
        "CREATE TABLE bwb_management_events (id INTEGER PRIMARY KEY, position_id TEXT, occurred_at REAL, "
        "session_date TEXT, action TEXT, reason TEXT, executed INTEGER, gate TEXT, detail_json TEXT)"
    )
    conn.execute(
        "INSERT INTO bwb_positions VALUES ('P2', 'control', 'SPX', '2026-10-15T11:00:05-04:00', '2026-10-15')"
    )
    attempts = [
        ("2026-10-15T10:00:00-04:00", "no_fresh_quotes", None, None),
        ("2026-10-15T10:01:00-04:00", "filled", None, 7600.0),  # an earlier position's fill
        ("2026-10-15T10:30:00-04:00", "spread_too_wide", "0.40", None),
        ("2026-10-15T10:31:00-04:00", "spread_too_wide", "0.35", None),
        ("2026-10-15T10:32:00-04:00", "no_fresh_quotes", None, None),
        ("2026-10-15T11:00:00-04:00", "filled", None, 7650.0),  # this position
        ("2026-10-15T11:30:00-04:00", "spread_too_wide", "0.50", None),  # after: not part of its entry
    ]
    conn.executemany(
        "INSERT INTO bwb_entry_attempts (ts, trade_date, symbol, arm, outcome, block_detail, body_strike) "
        "VALUES (?, '2026-10-15', 'SPX', 'control', ?, ?, ?)",
        attempts,
    )
    events = [
        (100.0, "fire_addon", "addon_credit_met", 0, "spread_too_wide", '{"credit": 0.025}'),
        (160.0, "fire_addon", "addon_credit_met", 0, "spread_too_wide", '{"credit": 0.05}'),
        (220.0, "fire_addon", "addon_credit_met", 1, None, '{"credit": 0.10}'),
        (280.0, "fire_addon", "addon_credit_met", 1, None, '{"credit": 0.10}'),
    ]
    conn.executemany(
        "INSERT INTO bwb_management_events (position_id, occurred_at, session_date, action, reason, "
        "executed, gate, "
        "detail_json) VALUES ('P2', ?, '2026-10-15', ?, ?, ?, ?, ?)",
        events,
    )
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def test_entry_shows_only_the_attempts_since_the_arms_previous_fill(tmp_path):
    e = trace.trace(_bwb(tmp_path), "bwb_132", "P2")["entry"]
    assert e["basis"] == "matched" and e["attempts_before"] == 3
    assert [(r["outcome"], r["count"]) for r in e["runs"]] == [("spread_too_wide", 2), ("no_fresh_quotes", 1)]
    # A run keeps its first and last time, and the last row's block detail.
    assert e["runs"][0]["first"].endswith("10:30:00-04:00") and e["runs"][0]["last"].endswith(
        "10:31:00-04:00"
    )
    assert e["runs"][0]["block_detail"] == "0.35"
    assert e["filled"]["detail"]["body_strike"] == 7650.0


def test_an_executed_event_is_never_folded_into_the_considered_ones(tmp_path):
    g = trace.trace(_bwb(tmp_path), "bwb_132", "P2")["management"]
    assert g["events"] == 4
    assert [(r["executed"], r["count"]) for r in g["runs"]] == [(False, 2), (True, 1), (True, 1)]
    assert g["runs"][0]["gate"] == "spread_too_wide" and g["runs"][0]["detail"] == {"credit": 0.05}
    assert [x["detail"] for x in g["executed"]] == [{"credit": 0.10}, {"credit": 0.10}]


def _flies(tmp_path, *, with_orders: bool) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "flies.db")
    conn.execute(
        "CREATE TABLE fly_positions (position_id TEXT, arm TEXT, symbol TEXT, trade_date TEXT, "
        "entry_time TEXT)"
    )
    conn.execute(
        "CREATE TABLE fly_entry_attempts (id INTEGER PRIMARY KEY, ts TEXT, trade_date TEXT, arm TEXT, "
        "symbol TEXT, "
        "outcome TEXT, block_detail TEXT, center REAL, position_id TEXT)"
    )
    conn.execute(
        "CREATE TABLE fly_live_orders (order_id TEXT, position_id TEXT, leg TEXT, side TEXT, "
        "limit_price REAL, "
        "mid_at_submit REAL, outcome TEXT, placed_at TEXT, broker_fill_price REAL)"
    )
    conn.execute(
        "INSERT INTO fly_positions VALUES ('F', 'control', 'SPX', '2026-10-15', '2026-10-15T11:00:00-04:00')"
    )
    # Two fills at the same minute: only the link says which is F's.
    conn.executemany(
        "INSERT INTO fly_entry_attempts (ts, trade_date, arm, symbol, outcome, center, position_id) "
        "VALUES (?, '2026-10-15', 'control', 'SPX', ?, ?, ?)",
        [
            ("2026-10-15T10:59:00-04:00", "cadence_blocked", None, None),
            ("2026-10-15T11:00:00-04:00", "filled", 7650.0, "F"),
            ("2026-10-15T11:00:00-04:00", "filled", 7700.0, "OTHER"),
        ],
    )
    if with_orders:
        conn.execute(
            "INSERT INTO fly_live_orders VALUES ('o1', 'F', 'short_vertical', 'credit', 2.10, 2.15, "
            "'filled', "
            "'2026-10-15T11:00:01-04:00', 2.10)"
        )
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def test_a_linked_attempt_wins_over_a_time_match(tmp_path):
    x = trace.trace(_flies(tmp_path, with_orders=False), "fly_book", "F")
    assert x["entry"]["basis"] == "linked" and x["entry"]["filled"]["detail"]["center"] == 7650.0
    assert "position_id" not in x["entry"]["filled"]["detail"]


def test_flies_live_orders_are_the_live_entry_trace(tmp_path):
    x = trace.trace(_flies(tmp_path, with_orders=True), "fly_book", "F")
    assert x["orders"]["recorded"] is True
    [o] = x["orders"]["orders"]
    assert (o["leg"], o["limit_price"], o["broker_fill_price"]) == ("short_vertical", 2.10, 2.10)
    (tmp_path / "paper").mkdir()
    paper = trace.trace(_flies(tmp_path / "paper", with_orders=False), "fly_book", "F")
    assert paper["orders"]["recorded"] is False and paper["management"]["recorded"] is False


def test_meic_attempt_times_are_composed_from_the_session_and_hh_mm(tmp_path):
    conn = sqlite3.connect(tmp_path / "meic.db")
    conn.execute(
        "CREATE TABLE ic_trades (ic_order_id TEXT, risk_profile TEXT, symbol TEXT, entry_time TEXT, "
        "exit_time TEXT)"
    )
    conn.execute(
        "CREATE TABLE entry_attempts (id INTEGER PRIMARY KEY, ts TEXT, trade_date TEXT, arm TEXT, "
        "symbol TEXT, "
        "outcome TEXT, block_detail TEXT, ic_order_id TEXT)"
    )
    conn.execute("INSERT INTO ic_trades VALUES ('M', 'control', 'SPX', '2026-10-16 12:58:30-04:00', NULL)")
    conn.executemany(
        "INSERT INTO entry_attempts (ts, trade_date, arm, symbol, outcome, ic_order_id) "
        "VALUES (?, '2026-10-16', 'control', 'SPX', ?, ?)",
        [("12:57", "cadence_blocked", None), ("12:58", "filled", "M")],
    )
    conn.commit()
    conn.row_factory = sqlite3.Row
    e = trace.trace(conn, "meic_ic", "M")["entry"]
    assert e["filled"]["at"] == "2026-10-16T12:58:00-04:00"
    assert e["runs"][0]["first"] == "2026-10-16T12:57:00-04:00"


def test_a_long_trail_keeps_its_ends_and_says_how_much_it_left_out():
    rows = [{"at": str(i), "outcome": "a" if i % 2 else "b"} for i in range(200)]
    capped, omitted = trace._capped(trace.runs(rows, key=lambda r: r["outcome"]))
    assert len(capped) == trace.MAX_RUNS and omitted == 200 - trace.MAX_RUNS
    assert capped[0]["first"] == "0" and capped[-1]["first"] == "199"


def test_an_unknown_position_is_not_found(tmp_path):
    x = trace.trace(_bwb(tmp_path), "bwb_132", "NOPE")
    assert x["found"] is False


def test_the_cli_answers_in_json(tmp_path, capsys):
    _bwb(tmp_path).close()
    cli.main(["read", "--db", str(tmp_path / "bwb.db"), "--schema", "bwb_132", "--position-id", "P2"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] and doc["found"] and doc["entry"]["attempts_before"] == 3
