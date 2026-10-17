"""A book's session peak risk: replayed by `analytics.session_peak_risk`, recorded at settlement,
backfilled by `run.py backfill-peak-risk`, and checked against the live loop's own record.

The rows are the live ledger's, values included, from the two sessions that taught the backfill's
check what a recorded peak can hold that a position never did (2026-10-16):

- 2026-09-25: one legged entry sent at 2.15 and filled at 2.20. The loop recorded $298.44 at
  10:30:47, its row still at the limit; the position carried $293.44.
- 2026-10-08: at 10:21:30 the loop also counted an entry order working at 2.45 that never filled.
"""

from __future__ import annotations

import pytest
from test_engine import BASE_CONFIG, snapshot

from cherrypick.flies import analytics, cli, fly
from cherrypick.flies import book as bookmod
from cherrypick.flies import db as dbmod

DAY = "2026-09-25"


def _legged(pid, entry, done, credit, debit, *, status="settled", exit_time="2026-09-25T16:20:31-04:00"):
    completed = done is not None
    return {
        "position_id": pid,
        "book_id": f"{DAY}|control|SPX",
        "trade_date": DAY,
        "arm": "control",
        "symbol": "SPX",
        "kind": "fly" if completed else "short_vertical",
        "entry_mode": "legged",
        "side": "put",
        "center": 6700.0,
        "wing_width": 5.0,
        "far_width": None,
        "quantity": 1,
        "credit": credit,
        "debit": debit,
        "net": round(credit - (debit or 0.0), 2) if completed else credit,
        "fees": 6.88 if completed else 3.4433,
        "floor_dollars": 3.1134 if completed else None,
        "status": status,
        "entry_time": entry,
        "completed_at": done,
        "exit_time": exit_time,
        "settlement_price": None,
    }


def _session_0925():
    return [
        _legged("p1", "2026-09-25T10:30:47-04:00", "2026-09-25T10:38:49-04:00", 2.20, 1.90),
        _legged("p2", "2026-09-25T10:38:49-04:00", "2026-09-25T11:01:36-04:00", 2.40, 2.15),
    ]


# --------------------------------------------------------------------------- the replay
def test_an_uncompleted_vertical_counts_at_full_defined_risk_until_it_completes():
    peak = analytics.session_peak_risk(_session_0925())
    # (2.20 - 5) x 100, less the vertical's opening fee and the two-strike settlement reserve.
    expected = -((2.20 - 5.0) * 100 - fly.vertical_open_fee("SPX", 1) - fly.expire_fee(2))
    assert peak["peak"] == round(expected, 2) == 293.44
    assert peak["at"] == "2026-09-25T10:30:47-04:00"


def test_a_completed_fly_counts_its_recorded_floor_not_its_settled_fees():
    # At 10:38:49 the first completes (floor +3.11: contributes nothing) as the second opens.
    assert analytics.exposure_at(_session_0925(), "2026-09-25T10:38:49-04:00") == pytest.approx(
        273.4433, abs=0.01
    )


def test_cancelled_and_voided_rows_are_never_positions():
    rows = _session_0925() + [
        _legged("x1", "2026-09-25T10:30:00-04:00", None, 2.45, None, status="cancelled"),
        {**_legged("x2", "2026-09-25T10:30:00-04:00", None, 2.45, None), "void_reason": "outage"},
    ]
    assert analytics.session_peak_risk(rows)["peak"] == 293.44


def test_a_position_closed_before_the_bell_is_off_the_book_from_its_exit():
    first = _legged("p1", "2026-09-25T10:00:00-04:00", None, 2.20, None, status="closed")
    first["exit_time"] = "2026-09-25T10:20:00-04:00"
    second = _legged("p2", "2026-09-25T10:30:00-04:00", None, 2.40, None)
    peak = analytics.session_peak_risk([first, second])
    assert peak["peak"] == 293.44 and peak["at"] == "2026-09-25T10:00:00-04:00"


def test_a_book_that_held_nothing_carried_no_risk_and_one_with_no_times_is_unknown():
    assert analytics.session_peak_risk([]) == {"peak": 0.0, "at": None}
    assert (
        analytics.session_peak_risk([_legged("x", None, None, 2.2, None, status="cancelled")])["peak"] == 0.0
    )
    untimed = _legged("p", None, None, 2.2, None)
    assert analytics.session_peak_risk([untimed]) == {"peak": None, "at": None}


# --------------------------------------------------------------------------- recorded at settlement
def test_settlement_records_the_books_peak(tmp_path):
    conn = dbmod.connect(str(tmp_path / "paper_trades.db"))
    config = {"defaults": {**BASE_CONFIG["defaults"], "entry_modes": ["legged"]}, "arms": {"control": {}}}
    bookmod.process_snapshot(snapshot(underlying_price=5998.0), config, conn, "control")
    result = bookmod.settle_book(conn, "2026-07-20", "control", "SPX", 5990.0, config)
    book = conn.execute(
        "SELECT peak_risk, peak_risk_at FROM fly_books WHERE book_id = ?", (result["book_id"],)
    ).fetchone()
    [row] = dbmod.book_positions(conn, result["book_id"])
    # One uncompleted vertical all session: its peak is its open worst case from its entry, priced
    # with its OPENING fee. It settled in the money, so the row's fees now also hold the settlement
    # charge; pricing the settled row would count that charge on top of the reserve for it.
    opening = {**analytics._state_at(row, row["entry_time"]), "fees": fly.vertical_open_fee("SPX", 1)}
    assert book["peak_risk"] == round(-fly.position_floor(opening), 2) == round(-row["floor_dollars"], 2)
    assert row["fees"] > fly.vertical_open_fee("SPX", 1), "the fixture must settle in the money"
    assert book["peak_risk_at"] == row["entry_time"]


# --------------------------------------------------------------------------- the backfill and its check
def _ledger(tmp_path, rows, marks, orders):
    conn = dbmod.connect(str(tmp_path / "live_trades.db"))
    for r in rows:
        dbmod.save_position(conn, {k: v for k, v in r.items()})
    dbmod.save_book(conn, {"book_id": f"{DAY}|control|SPX", "trade_date": DAY, "arm": "control", "symbol": "SPX",
                           "status": "settled"})  # fmt: skip
    for ts, margin in marks:
        conn.execute(
            "INSERT INTO fly_live_marks (iteration_ts, trade_date, position_id, open_margin) VALUES (?,?,?,?)",
            (ts, DAY, "p1", margin),
        )
    for pid, limit, fill in orders:
        conn.execute(
            "INSERT INTO fly_live_orders (order_id, trade_date, position_id, leg, limit_price, broker_fill_price) "
            "VALUES (?,?,?,?,?,?)",
            (f"o-{pid}", DAY, pid, "entry", limit, fill),
        )
    conn.commit()
    return conn


def test_backfill_is_a_dry_run_unless_written(tmp_path):
    conn = _ledger(tmp_path, _session_0925(), [], [])
    out = cli.backfill_peak_risk(conn, write=False)
    assert out["recorded"] == 1
    assert conn.execute("SELECT peak_risk FROM fly_books").fetchone()["peak_risk"] is None
    cli.backfill_peak_risk(conn, write=True)
    assert conn.execute("SELECT peak_risk FROM fly_books").fetchone()["peak_risk"] == 293.44
    # Recorded books are left alone unless asked to re-derive them.
    assert cli.backfill_peak_risk(conn, write=False)["books"] == 0
    assert cli.backfill_peak_risk(conn, write=False, restamp=True)["books"] == 1


def test_a_recorded_peak_that_matches_the_replay_is_matched(tmp_path):
    conn = _ledger(tmp_path, _session_0925(), [("2026-09-25T10:30:47-04:00", 293.44)], [])
    out = cli.backfill_peak_risk(conn, write=False)
    assert (out["checked"], out["matched"], out["explained"], out["unexplained"]) == (1, 1, [], [])


def test_a_gap_that_is_the_fill_beating_the_limit_is_explained(tmp_path):
    conn = _ledger(tmp_path, _session_0925(), [("2026-09-25T10:30:47-04:00", 298.44)], [("p1", 2.15, 2.20)])
    out = cli.backfill_peak_risk(conn, write=False)
    [e] = out["explained"]
    assert e["by"] == [{"what": "improved p1", "dollars": 5.0}]
    assert out["unexplained"] == []


def test_a_gap_that_is_an_order_working_at_the_tick_and_never_filled_is_explained(tmp_path):
    rows = _session_0925() + [
        _legged("u1", "2026-09-25T10:30:47-04:00", None, 2.45, None, status="cancelled")
    ]
    working = 293.44 + (5.0 - 2.45) * 100 + fly.vertical_open_fee("SPX", 1) + fly.expire_fee(2)
    conn = _ledger(tmp_path, rows, [("2026-09-25T10:30:47-04:00", round(working, 2))], [])
    out = cli.backfill_peak_risk(conn, write=False)
    [e] = out["explained"]
    assert [p["what"] for p in e["by"]] == ["unfilled u1"]


def test_any_other_gap_is_unexplained(tmp_path):
    """The guard: a recorded peak the replay cannot account for must be named, not absorbed."""
    conn = _ledger(tmp_path, _session_0925(), [("2026-09-25T10:30:47-04:00", 350.00)], [("p1", 2.15, 2.20)])
    out = cli.backfill_peak_risk(conn, write=False)
    assert out["explained"] == [] and len(out["unexplained"]) == 1
    assert out["unexplained"][0]["recorded"] == 350.0
