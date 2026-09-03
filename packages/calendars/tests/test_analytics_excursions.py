"""`analytics.excursions` — MAE/MFE per closed position (docs/metrics-plan.md Phase 2).

core.metrics.excursions owns the pure MAE/MFE math (tested there); this covers the module-specific
half -- pairing dc_marks' per-tick front/back leg mids (by marked_at, the same pairing
exit_policies.week_data uses) into `back.mid - front.mid`, the same formula engine.plan_entry uses
to compute entry_debit in the first place.
"""

from cherrypick.calendars import analytics, db


def _position(conn, position_id, symbol="SPY", book="control", side="put", entry_debit=1.0, quantity=1, status="closed"):
    conn.execute(
        "INSERT INTO dc_positions (position_id, week_of, entry_session, book, side, symbol, "
        "structure, front_expiration, back_expiration, strike, quantity, entry_debit, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            position_id, "2026-08-17", "2026-08-17", book, side, symbol,
            "dc_4_7", "2026-08-21", "2026-08-24", 640.0, quantity, entry_debit, status,
        ),
    )


def _mark(conn, position_id, marked_at, front_mid, back_mid, usable=1):
    conn.execute(
        "INSERT INTO dc_marks (position_id, leg_role, marked_at, session_date, mid, usable) "
        "VALUES (?, 'front', ?, '2026-08-17', ?, ?)",
        (position_id, marked_at, front_mid, usable),
    )
    conn.execute(
        "INSERT INTO dc_marks (position_id, leg_role, marked_at, session_date, mid, usable) "
        "VALUES (?, 'back', ?, '2026-08-17', ?, ?)",
        (position_id, marked_at, back_mid, usable),
    )


def test_excursions_reports_mae_mfe_per_position():
    conn = db.connect(":memory:")
    _position(conn, "p1", entry_debit=1.00, quantity=2)
    # value = back - front. entry_debit=1.00, so pnl = value - 1.00.
    _mark(conn, "p1", 1, front_mid=2.0, back_mid=3.0)  # value 1.00, pnl 0.00
    _mark(conn, "p1", 2, front_mid=2.5, back_mid=2.6)  # value 0.10, pnl -0.90 (worst)
    _mark(conn, "p1", 3, front_mid=1.5, back_mid=3.2)  # value 1.70, pnl +0.70 (best)
    conn.commit()

    out = analytics.excursions(conn)
    assert len(out["positions"]) == 1
    p = out["positions"][0]
    assert p["position_id"] == "p1"
    mult = 100 * 2
    assert p["mfe"] == round(0.70 * mult, 2)
    assert p["mae"] == round(-0.90 * mult, 2)
    assert p["n"] == 3


def test_excursions_skips_ticks_missing_either_leg():
    conn = db.connect(":memory:")
    _position(conn, "p1", entry_debit=1.00)
    _mark(conn, "p1", 1, front_mid=2.0, back_mid=3.0)  # pnl 0.00
    # A tick with only the front leg usable -- must not be paired with a stale back value.
    conn.execute(
        "INSERT INTO dc_marks (position_id, leg_role, marked_at, session_date, mid, usable) "
        "VALUES ('p1', 'front', 2, '2026-08-17', 9.0, 1)"
    )
    conn.commit()

    out = analytics.excursions(conn)
    assert out["positions"][0]["n"] == 1


def test_excursions_skips_unusable_marks():
    conn = db.connect(":memory:")
    _position(conn, "p1", entry_debit=1.00)
    _mark(conn, "p1", 1, front_mid=2.0, back_mid=3.0)  # pnl 0.00
    _mark(conn, "p1", 2, front_mid=0.01, back_mid=99.0, usable=0)  # refusal row -- must be excluded
    conn.commit()

    out = analytics.excursions(conn)
    p = out["positions"][0]
    assert p["n"] == 1
    assert p["mae"] == 0.0 and p["mfe"] == 0.0


def test_excursions_skips_positions_with_no_entry_debit_or_no_usable_ticks():
    conn = db.connect(":memory:")
    _position(conn, "p1", entry_debit=None)
    _position(conn, "p2", entry_debit=1.0)  # no marks at all
    _position(conn, "p3", entry_debit=1.0, status="open")
    conn.commit()

    out = analytics.excursions(conn)
    assert out["positions"] == []
    assert out["mae_distribution"] == {"median": None, "n": 0}
    assert out["mfe_distribution"] == {"median": None, "n": 0}


def test_excursions_distributions_are_the_median_across_positions():
    conn = db.connect(":memory:")
    _position(conn, "p1", entry_debit=1.00)
    _mark(conn, "p1", 1, front_mid=1.0, back_mid=2.5)  # mfe = (1.5-1.0)*100 = 50.0
    _position(conn, "p2", entry_debit=1.00)
    _mark(conn, "p2", 1, front_mid=1.0, back_mid=2.3)  # mfe = (1.3-1.0)*100 = 30.0
    conn.commit()

    out = analytics.excursions(conn)
    assert out["mfe_distribution"] == {"median": 40.0, "n": 2}
