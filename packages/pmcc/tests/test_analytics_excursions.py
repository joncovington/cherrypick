"""`analytics.excursions` — MAE/MFE per closed position (docs/metrics-plan.md Phase 2).

core.metrics.excursions owns the pure MAE/MFE math (tested there); this covers the module-specific
half -- pairing pmcc_marks' per-tick long_call/short_call leg mids (by marked_at, one shared
timestamp per tick) into `long_call.mid - short_call.mid`, the same formula
engine.worksheet_metrics uses to compute net_debit at entry.
"""

from cherrypick.pmcc import analytics, db


def _position(conn, position_id, symbol="TQQQ", book="control", net_debit=10.0, quantity=1, status="closed", era="redesign"):
    conn.execute(
        "INSERT INTO pmcc_positions (position_id, symbol, book, entry_session, quantity, "
        "long_expiration, long_strike, short_expiration, short_strike, net_debit, status, era) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (position_id, symbol, book, "2026-08-20", quantity, "2026-09-11", 50.0, "2026-08-28", 72.0, net_debit, status, era),
    )


def _mark(conn, position_id, marked_at, long_mid, short_mid, usable=1):
    conn.execute(
        "INSERT INTO pmcc_marks (position_id, leg_role, marked_at, session_date, mid, usable) "
        "VALUES (?, 'long_call', ?, '2026-08-20', ?, ?)",
        (position_id, marked_at, long_mid, usable),
    )
    conn.execute(
        "INSERT INTO pmcc_marks (position_id, leg_role, marked_at, session_date, mid, usable) "
        "VALUES (?, 'short_call', ?, '2026-08-20', ?, ?)",
        (position_id, marked_at, short_mid, usable),
    )


def test_excursions_reports_mae_mfe_per_position():
    conn = db.connect(":memory:")
    _position(conn, "p1", net_debit=10.0, quantity=2)
    # value = long - short. net_debit=10.0, pnl = value - 10.0.
    _mark(conn, "p1", 1, long_mid=22.0, short_mid=12.0)  # value 10.0, pnl 0.00
    _mark(conn, "p1", 2, long_mid=21.0, short_mid=13.5)  # value 7.50, pnl -2.50 (worst)
    _mark(conn, "p1", 3, long_mid=23.0, short_mid=10.5)  # value 12.50, pnl +2.50 (best)
    conn.commit()

    out = analytics.excursions(conn, era="redesign")
    assert len(out["positions"]) == 1
    p = out["positions"][0]
    assert p["position_id"] == "p1"
    mult = 100 * 2
    assert p["mfe"] == round(2.50 * mult, 2)
    assert p["mae"] == round(-2.50 * mult, 2)
    assert p["n"] == 3


def test_excursions_skips_ticks_missing_either_leg():
    conn = db.connect(":memory:")
    _position(conn, "p1", net_debit=10.0)
    _mark(conn, "p1", 1, long_mid=22.0, short_mid=12.0)  # pnl 0.00
    conn.execute(
        "INSERT INTO pmcc_marks (position_id, leg_role, marked_at, session_date, mid, usable) "
        "VALUES ('p1', 'long_call', 2, '2026-08-20', 99.0, 1)"
    )
    conn.commit()

    out = analytics.excursions(conn, era="redesign")
    assert out["positions"][0]["n"] == 1


def test_excursions_skips_unusable_marks():
    conn = db.connect(":memory:")
    _position(conn, "p1", net_debit=10.0)
    _mark(conn, "p1", 1, long_mid=22.0, short_mid=12.0)  # pnl 0.00
    _mark(conn, "p1", 2, long_mid=99.0, short_mid=0.01, usable=0)  # refusal row
    conn.commit()

    out = analytics.excursions(conn, era="redesign")
    p = out["positions"][0]
    assert p["n"] == 1
    assert p["mae"] == 0.0 and p["mfe"] == 0.0


def test_excursions_scopes_to_current_era_by_default():
    conn = db.connect(":memory:")
    _position(conn, "old", net_debit=10.0, era="pre-redesign")
    _mark(conn, "old", 1, long_mid=25.0, short_mid=10.0)
    _position(conn, "new", net_debit=10.0, era="redesign")
    _mark(conn, "new", 1, long_mid=25.0, short_mid=10.0)
    conn.commit()

    out = analytics.excursions(conn)  # default era
    assert [p["position_id"] for p in out["positions"]] == ["new"]

    out_all = analytics.excursions(conn, era="ALL")
    assert {p["position_id"] for p in out_all["positions"]} == {"old", "new"}


def test_excursions_skips_positions_with_no_net_debit_or_no_usable_ticks():
    conn = db.connect(":memory:")
    _position(conn, "p1", net_debit=None)
    _position(conn, "p2", net_debit=10.0)  # no marks
    _position(conn, "p3", net_debit=10.0, status="open")
    conn.commit()

    out = analytics.excursions(conn, era="redesign")
    assert out["positions"] == []
    assert out["mae_distribution"] == {"median": None, "n": 0}
    assert out["mfe_distribution"] == {"median": None, "n": 0}
