"""core.excursions' quote-history paths (2026-10-17): flies and MEIC positions with no recorded marks,
priced at mid from the gex recorder's SPX 0DTE quote history.

The guards, each broken on purpose while this was written: a flies position is priced as its opening
vertical until `completed_at` and as a fly after; a MEIC side that has exited counts its realised P&L,
never a later quote; a sample missing a leg is refused; and recorded live marks always win.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from cherrypick.core import excursions
from cherrypick.core.clock import ET

DAY = "2026-10-15"
PUT, CALL = 0, 1


def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 10, 15, hour, minute, tzinfo=ET)


def _history(tmp_path, samples: dict) -> str:
    """samples: {(hour, minute): {(side, strike): mid}}"""
    path = tmp_path / "gex_history.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE gex_quote_samples (id INTEGER PRIMARY KEY, symbol TEXT, trade_date TEXT, ts REAL, "
        "expiration TEXT)"
    )
    conn.execute(
        "CREATE TABLE gex_quotes (sample_id INTEGER, side INTEGER, strike REAL, bid REAL, ask REAL, "
        "mid REAL, "
        "age_s REAL, delta REAL)"
    )
    for (h, m), quotes in samples.items():
        cur = conn.execute(
            "INSERT INTO gex_quote_samples (symbol, trade_date, ts, expiration) VALUES ('SPX', ?, ?, ?)",
            (DAY, _at(h, m).timestamp(), DAY),
        )
        for (side, strike), mid in quotes.items():
            conn.execute(
                "INSERT INTO gex_quotes VALUES (?,?,?,?,?,?,0.5,NULL)",
                (cur.lastrowid, side, strike, mid - 0.05, mid + 0.05, mid),
            )
    conn.commit()
    conn.close()
    return str(path)


def _flies(tmp_path, *, entry_mode="legged", completed=True, marks=False) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "flies.db")
    conn.execute(
        "CREATE TABLE fly_positions (position_id TEXT, trade_date TEXT, symbol TEXT, entry_mode TEXT, "
        "kind TEXT, "
        "side TEXT, center REAL, wing_width REAL, quantity INTEGER, net REAL, credit REAL, debit REAL, "
        "entry_time TEXT, completed_at TEXT, exit_time TEXT, settlement_price REAL, gross_pnl REAL, "
        "pnl REAL, "
        "closed_before_expiry INTEGER)"
    )
    conn.execute(
        "CREATE TABLE fly_live_marks (iteration_ts TEXT, position_id TEXT, kind TEXT, structure_mid REAL, "
        "mark_pnl REAL)"
    )
    if entry_mode == "legged":
        # Put credit spread 7600/7595 for 2.00, completed at 10:30 by a 1.50 debit: a fly, net 0.50.
        kind, net, credit, debit = ("fly", 0.5, 2.0, 1.5) if completed else ("short_vertical", 2.0, 2.0, None)
    else:
        # debit_first put: +7605 / -7600 for 1.20, completed by a 1.70 credit: net 0.50.
        kind, net, credit, debit = ("fly", 0.5, 1.7, 1.2) if completed else ("long_vertical", -1.2, None, 1.2)
    settle = 7600.0  # a fly pins: payoff 5; the short vertical settles worthless (payoff 0)
    payoff = {"fly": 5.0, "short_vertical": 0.0, "long_vertical": 0.0}[kind]
    conn.execute(
        "INSERT INTO fly_positions VALUES ('F', ?, 'SPX', ?, ?, 'put', 7600, 5, 1, ?, ?, ?, ?, ?, ?, ?, ?, "
        "?, 0)",
        (
            DAY, entry_mode, kind, net, credit, debit, _at(10, 0).isoformat(),
            _at(10, 30).isoformat() if completed else None, _at(16, 20).isoformat(),
            settle, (net + payoff) * 100, (net + payoff) * 100 - 7,
        ),
    )  # fmt: skip
    if marks:
        conn.execute(
            "INSERT INTO fly_live_marks VALUES (?, 'F', 'fly', 1.0, 143.0)", (_at(11, 0).isoformat(),)
        )
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


QUOTES = {
    # 10:15, before completion: put 7600 at 3.00, 7595 at 2.20, 7605 at 4.10
    (10, 15): {(PUT, 7595): 2.2, (PUT, 7600): 3.0, (PUT, 7605): 4.1},
    # 11:00, after: 7595 1.0, 7600 1.6, 7605 2.6 -> fly 1.0 - 3.2 + 2.6 = 0.4
    (11, 0): {(PUT, 7595): 1.0, (PUT, 7600): 1.6, (PUT, 7605): 2.6},
    # 12:00, a leg missing: refused
    (12, 0): {(PUT, 7595): 1.0, (PUT, 7600): 1.6},
}


def test_a_legged_fly_is_its_short_vertical_until_completion_and_a_fly_after(tmp_path):
    hist = _history(tmp_path, QUOTES)
    x = excursions.excursion(_flies(tmp_path), "fly_book", "F", history_db=hist)
    assert x["basis"] == excursions.QUOTE_BASIS
    # 10:15 short vertical: (2.00 + (2.20 - 3.00)) x 100 = 120; 11:00 fly: (0.50 + 0.40) x 100 = 90.
    assert [round(v, 2) for _, v in x["path"]] == [0.0, 120.0, 90.0, 550.0]
    assert (x["ticks"], x["refused_ticks"]) == (3, 1)
    assert x["reconciles"] is True and x["endpoint"] == 550.0


def test_a_debit_first_fly_opens_as_its_debit_vertical(tmp_path):
    hist = _history(tmp_path, QUOTES)
    x = excursions.excursion(_flies(tmp_path, entry_mode="debit_first"), "fly_book", "F", history_db=hist)
    # 10:15 long vertical (+7605 / -7600): (-1.20 + (4.10 - 3.00)) x 100 = -10.
    assert round(x["path"][1][1], 2) == -10.0
    assert x["reconciles"] is True


def test_an_uncompleted_vertical_stays_a_vertical(tmp_path):
    hist = _history(tmp_path, QUOTES)
    x = excursions.excursion(_flies(tmp_path, completed=False), "fly_book", "F", history_db=hist)
    # 11:00 still a short vertical: (2.00 + (1.00 - 1.60)) x 100 = 140.
    assert [round(v, 2) for _, v in x["path"][1:3]] == [120.0, 140.0]
    assert x["reconciles"] is True


def test_recorded_live_marks_win_over_the_quote_history(tmp_path):
    hist = _history(tmp_path, QUOTES)
    x = excursions.excursion(_flies(tmp_path, marks=True), "fly_book", "F", history_db=hist)
    assert x["basis"] == "net_of_fees_as_recorded"


def test_a_session_before_the_quote_history_is_not_priced(tmp_path):
    hist = _history(tmp_path, {})
    x = excursions.excursion(_flies(tmp_path), "fly_book", "F", history_db=hist)
    assert x["recorded"] is False and "quote history" in x["reason"]


def _meic(tmp_path, *, put_exit=(13, 0), put_pnl=-150.0) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "meic.db")
    conn.execute(
        "CREATE TABLE ic_trades (ic_order_id TEXT, trade_date TEXT, symbol TEXT, entry_time TEXT, "
        "exit_time TEXT, "
        "wing_width REAL, put_strike REAL, call_strike REAL, put_credit REAL, call_credit REAL, "
        "quantity INTEGER, "
        "dollar_multiplier REAL, pnl REAL)"
    )
    conn.execute("CREATE TABLE ic_spread_legs (ic_order_id TEXT, side TEXT, exit_time TEXT, pnl REAL)")
    conn.execute(
        "INSERT INTO ic_trades VALUES ('M', ?, 'SPX', ?, ?, 5, 7590, 7610, 0.80, 0.70, 1, 100, ?)",
        (DAY, _at(10, 0).isoformat(), _at(16, 0).isoformat(), put_pnl + 70.0),
    )
    conn.executemany(
        "INSERT INTO ic_spread_legs VALUES ('M', ?, ?, ?)",
        [("put", _at(*put_exit).isoformat(), put_pnl), ("call", _at(16, 0).isoformat(), 70.0)],
    )
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


MEIC_QUOTES = {
    # put 7590/7585 costs 1.00 to close; call 7610/7615 costs 0.40: (0.80-1.00 + 0.70-0.40) x 100 = 10
    (11, 0): {(PUT, 7590): 1.6, (PUT, 7585): 0.6, (CALL, 7610): 0.9, (CALL, 7615): 0.5},
    # 14:00, after the put side stopped at 13:00: realised -150 + call (0.70 - 0.10) x 100 = -90.
    # The put quotes here would read a recovery; they must not count.
    (14, 0): {(PUT, 7590): 0.2, (PUT, 7585): 0.1, (CALL, 7610): 0.3, (CALL, 7615): 0.2},
}


def test_a_meic_side_that_exited_counts_its_realised_pnl_never_a_later_quote(tmp_path):
    hist = _history(tmp_path, MEIC_QUOTES)
    x = excursions.excursion(_meic(tmp_path), "meic_ic", "M", history_db=hist)
    assert x["basis"] == excursions.QUOTE_BASIS
    assert [round(v, 2) for _, v in x["path"]] == [0.0, 10.0, -90.0, -80.0]
    assert x["endpoint"] == -80.0 and x["reconciles"] is True


def test_a_meic_condor_whose_sides_do_not_sum_to_its_pnl_says_so(tmp_path):
    hist = _history(tmp_path, MEIC_QUOTES)
    conn = _meic(tmp_path)
    conn.execute("UPDATE ic_trades SET pnl = 0")
    assert excursions.excursion(conn, "meic_ic", "M", history_db=hist)["reconciles"] is False
