"""core.excursions: one position's mark path, best and worst, on the basis its module records.

The guards, each broken on purpose while this was written: a tick with an open leg unpriced is
refused (never priced from that leg's last mark); a leg opened later (a roll) counts only from its
opening, and a closed one at its close value thereafter; and the leg-built endpoint reconciles to
the recorded gross, assignments included.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from cherrypick.core import excursions
from cherrypick.core.clock import ET
from cherrypick.core.excursions import __main__ as cli

LEG_COLS = (
    "position_id TEXT, leg_role TEXT, action TEXT, quantity INTEGER, entry_mid REAL, status TEXT, "
    "created_at TEXT, opened_at TEXT, closed_at TEXT, close_value REAL"
)


def _iso(day: int, hour: int, minute: int = 0) -> str:
    return datetime(2026, 10, day, hour, minute, tzinfo=ET).isoformat()


def _epoch(day: int, hour: int, minute: int = 0) -> float:
    return datetime(2026, 10, day, hour, minute, tzinfo=ET).timestamp()


def _pmcc(tmp_path, *, gross: float, assignment: tuple | None = None) -> sqlite3.Connection:
    """A long call held throughout; short_1 sold at entry and bought back on the 14th; short_2 sold
    then and closed with the position on the 16th."""
    db = tmp_path / "pmcc.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE pmcc_positions (position_id TEXT, entry_time TEXT, gross_pnl REAL, status TEXT)"
    )
    conn.execute(f"CREATE TABLE pmcc_legs ({LEG_COLS})")
    conn.execute(
        "CREATE TABLE pmcc_marks (position_id TEXT, leg_role TEXT, marked_at REAL, mid REAL, usable INTEGER)"
    )
    conn.execute(
        "CREATE TABLE pmcc_assignments (position_id TEXT, status TEXT, share_pnl REAL, disposed_at TEXT, "
        "assigned_at TEXT)"
    )
    conn.execute("INSERT INTO pmcc_positions VALUES ('P', ?, ?, 'closed')", (_iso(13, 10), gross))
    legs = [
        ("long", "Buy to Open", 10.0, _iso(13, 10), _iso(16, 15), 11.0),
        ("short_1", "Sell to Open", 2.0, _iso(13, 10), _iso(14, 10), 1.0),
        ("short_2", "Sell to Open", 3.0, _iso(14, 10), _iso(16, 15), 2.5),
    ]
    for role, action, entry, opened, closed, close in legs:
        conn.execute(
            "INSERT INTO pmcc_legs VALUES ('P',?,?,1,?,'closed',?,?,?,?)",
            (role, action, entry, opened, opened, closed, close),
        )
    marks = [
        # 13th 11:00: long 10.5, short_1 1.5 -> (+0.5 + 0.5) x 100 = +100
        (_epoch(13, 11), "long", 10.5, 1),
        (_epoch(13, 11), "short_1", 1.5, 1),
        # 13th 12:00: short_1 unusable -> refused, never priced from its 1.5
        (_epoch(13, 12), "long", 9.0, 1),
        (_epoch(13, 12), "short_1", None, 0),
        # 15th 11:00: long 9.0, short_1 realised +1.0, short_2 4.0 -> (-1.0 + 1.0 - 1.0) x 100 = -100
        (_epoch(15, 11), "long", 9.0, 1),
        (_epoch(15, 11), "short_2", 4.0, 1),
    ]
    conn.executemany("INSERT INTO pmcc_marks VALUES ('P',?,?,?,?)", [(r, t, m, u) for t, r, m, u in marks])
    if assignment is not None:
        conn.execute("INSERT INTO pmcc_assignments VALUES ('P',?,?,?,?)", assignment)
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def test_a_leg_path_prices_each_tick_from_its_open_legs_and_reconciles_at_the_end(tmp_path):
    # End: long +1.0, short_1 +1.0, short_2 +0.5 -> +250.
    x = excursions.excursion(_pmcc(tmp_path, gross=250.0), "pmcc", "P")
    assert x["basis"] == "gross_at_mid"
    assert [v for _, v in x["path"]] == [0.0, 100.0, -100.0, 250.0]
    assert (x["ticks"], x["refused_ticks"]) == (3, 1)
    assert x["best"]["pnl"] == 250.0 and x["worst"] == {"pnl": -100.0, "at": _iso(15, 11)}
    assert x["endpoint"] == 250.0 and x["reconciles"] is True


def test_an_endpoint_that_misses_the_recorded_gross_says_so(tmp_path):
    x = excursions.excursion(_pmcc(tmp_path, gross=240.0), "pmcc", "P")
    assert x["reconciles"] is False


def test_disposed_shares_are_part_of_the_endpoint(tmp_path):
    conn = _pmcc(tmp_path, gross=200.0, assignment=("disposed", -50.0, _iso(17, 9, 45), _iso(16, 16)))
    x = excursions.excursion(conn, "pmcc", "P")
    assert x["endpoint"] == 200.0 and x["reconciles"] is True
    assert x["path"][-1][0] == _iso(17, 9, 45)


def test_a_tick_while_assigned_shares_are_held_is_refused(tmp_path):
    # Assigned on the 14th, disposed at noon on the 15th: the 15th 11:00 tick holds shares no mark
    # prices, so it is refused rather than valued without them.
    conn = _pmcc(tmp_path, gross=200.0, assignment=("disposed", -50.0, _iso(15, 12), _iso(14, 16)))
    x = excursions.excursion(conn, "pmcc", "P")
    assert [v for _, v in x["path"]] == [0.0, 100.0, 200.0]
    assert (x["ticks"], x["refused_ticks"]) == (3, 2)
    assert x["reconciles"] is True


def test_shares_not_yet_disposed_leave_the_endpoint_unknown(tmp_path):
    conn = _pmcc(tmp_path, gross=200.0, assignment=("open", None, None, _iso(16, 16)))
    x = excursions.excursion(conn, "pmcc", "P")
    assert x["endpoint"] is None and x["reconciles"] is None


def _earnings(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "earnings.db")
    conn.execute("CREATE TABLE trades (order_id TEXT, opened_at REAL, closed_at REAL, pnl REAL)")
    conn.execute(
        "CREATE TABLE position_marks (order_id TEXT, marked_at TEXT, unrealized_pnl REAL, usable INTEGER)"
    )
    conn.execute("INSERT INTO trades VALUES ('E', ?, ?, -80.0)", (_epoch(14, 15, 30), _epoch(15, 9, 45)))
    conn.executemany(
        "INSERT INTO position_marks VALUES ('E',?,?,?)",
        [(_iso(14, 15, 45), 30.0, 1), (_iso(15, 9, 31), -50.0, 1), (_iso(15, 9, 40), None, 0)],
    )
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def test_a_recorded_path_runs_from_entry_to_the_trades_own_exit(tmp_path):
    x = excursions.excursion(_earnings(tmp_path), "earnings", "E")
    assert x["basis"] == "gross_as_recorded"
    assert [v for _, v in x["path"]] == [0.0, 30.0, -50.0, -80.0]
    assert x["worst"]["pnl"] == -80.0 and x["endpoint"] == -80.0
    assert (x["ticks"], x["refused_ticks"]) == (3, 1)


def test_modules_without_a_path_say_so_rather_than_guess():
    conn = sqlite3.connect(":memory:")
    for schema in ("meic_ic", "contango_etf"):
        x = excursions.excursion(conn, schema, "x")
        assert x["recorded"] is False and x["reason"]


def test_thinning_keeps_the_ends_and_the_extremes():
    points = [(datetime.fromtimestamp(1_790_000_000 + i, ET).isoformat(), float(i % 7)) for i in range(5000)]
    points[2345] = (points[2345][0], 99.0)
    points[3456] = (points[3456][0], -99.0)
    out = excursions.thin(points, keep=(points[2345], points[3456]))
    values = [v for _, v in out]
    assert len(out) <= excursions.MAX_POINTS + 4
    assert out[0][0] == points[0][0] and out[-1][0] == points[-1][0]
    assert 99.0 in values and -99.0 in values


def test_the_cli_answers_in_json(tmp_path, capsys):
    _pmcc(tmp_path, gross=250.0).close()
    cli.main(["read", "--db", str(tmp_path / "pmcc.db"), "--schema", "pmcc", "--position-id", "P"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] and doc["reconciles"] is True and doc["position_id"] == "P"
