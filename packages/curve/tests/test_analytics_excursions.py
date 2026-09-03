"""`analytics.excursions` — MAE/MFE per closed position (docs/metrics-plan.md Phase 2).

core.metrics.excursions owns the pure MAE/MFE math (tested there); this covers the module-specific
half -- pairing curve_marks' per-tick close_cost to a closed position and turning it into the
ordered P&L-relative-to-entry series `entry_credit - close_cost` represents.
"""

from cherrypick.curve import analytics, db


def _position(conn, position_id, symbol="VXX", book="control", entry_credit=0.5, quantity=1, status="closed"):
    conn.execute(
        "INSERT INTO curve_positions (position_id, symbol, book, entry_session, quantity, "
        "expiration, short_strike, long_strike, entry_credit, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (position_id, symbol, book, "2026-08-20", quantity, "2026-09-19", 20.0, 21.0, entry_credit, status),
    )


def _mark(conn, position_id, close_cost, marked_at, usable=1):
    conn.execute(
        "INSERT INTO curve_marks (position_id, leg_role, marked_at, session_date, close_cost, usable) "
        "VALUES (?, 'short_call', ?, '2026-08-20', ?, ?)",
        (position_id, marked_at, close_cost, usable),
    )


def test_excursions_reports_mae_mfe_per_position():
    conn = db.connect(":memory:")
    _position(conn, "p1", entry_credit=0.50, quantity=2)
    # close_cost dips to 0.10 (favorable: credit 0.50 - cost 0.10 = +0.40) then rises to 0.70
    # (adverse: 0.50 - 0.70 = -0.20).
    _mark(conn, "p1", 0.50, 1)  # pnl 0.00
    _mark(conn, "p1", 0.10, 2)  # pnl +0.40 (best)
    _mark(conn, "p1", 0.70, 3)  # pnl -0.20 (worst)
    _mark(conn, "p1", 0.30, 4)  # pnl +0.20 (closed here)
    conn.commit()

    out = analytics.excursions(conn)
    assert len(out["positions"]) == 1
    p = out["positions"][0]
    assert p["position_id"] == "p1"
    mult = 100 * 2
    assert p["mfe"] == round(0.40 * mult, 2)
    assert p["mae"] == round(-0.20 * mult, 2)
    assert p["n"] == 4


def test_excursions_skips_unusable_marks():
    conn = db.connect(":memory:")
    _position(conn, "p1", entry_credit=0.50)
    _mark(conn, "p1", 0.50, 1)
    _mark(conn, "p1", 9.99, 2, usable=0)  # a refusal row -- must not be read as a real spike
    conn.commit()

    out = analytics.excursions(conn)
    p = out["positions"][0]
    assert p["n"] == 1
    assert p["mae"] == 0.0 and p["mfe"] == 0.0


def test_excursions_skips_positions_with_no_entry_credit_or_no_usable_marks():
    conn = db.connect(":memory:")
    _position(conn, "p1", entry_credit=None)  # pre-instrumentation row
    _position(conn, "p2", entry_credit=0.50)  # no marks at all
    _position(conn, "p3", entry_credit=0.50, status="open")  # not closed
    conn.commit()

    out = analytics.excursions(conn)
    assert out["positions"] == []
    assert out["mae_distribution"] == {"median": None, "n": 0}
    assert out["mfe_distribution"] == {"median": None, "n": 0}


def test_excursions_distributions_are_the_median_across_positions():
    conn = db.connect(":memory:")
    _position(conn, "p1", entry_credit=0.50)
    _mark(conn, "p1", 0.10, 1)  # mfe = 0.40 * 100 = 40.0
    _position(conn, "p2", entry_credit=0.50)
    _mark(conn, "p2", 0.30, 1)  # mfe = 0.20 * 100 = 20.0
    conn.commit()

    out = analytics.excursions(conn)
    assert out["mfe_distribution"] == {"median": 30.0, "n": 2}
