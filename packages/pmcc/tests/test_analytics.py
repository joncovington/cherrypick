"""`headline`'s era scoping: the 2026-08-23 redesign boundary must not pool pre-redesign rows."""

from cherrypick.pmcc import analytics, db


def _closed_row(position_id, *, book, symbol, net, era):
    return {
        "position_id": position_id,
        "symbol": symbol,
        "book": book,
        "entry_session": "2026-08-17",
        "long_expiration": "2026-09-04",
        "long_strike": 50.0,
        "short_expiration": "2026-08-28",
        "short_strike": 67.0,
        "status": "closed",
        "gross_pnl": net,
        "fees": 0.0,
        "era": era,
    }


def test_headline_excludes_pre_era_rows_by_default(tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    db.save_position(conn, _closed_row("A", book="keltner", symbol="TQQQ", net=100.0, era=None))
    db.save_position(
        conn, _closed_row("B", book="control", symbol="TQQQ", net=50.0, era=analytics.CURRENT_ERA)
    )

    result = analytics.headline(conn)

    assert "keltner" not in result["books"]
    assert result["books"]["control"]["TQQQ"]["net_pnl"] == 50.0


def test_headline_era_all_pools_every_row(tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    db.save_position(conn, _closed_row("A", book="keltner", symbol="TQQQ", net=100.0, era=None))
    db.save_position(
        conn, _closed_row("B", book="control", symbol="TQQQ", net=50.0, era=analytics.CURRENT_ERA)
    )

    result = analytics.headline(conn, era="ALL")

    assert result["books"]["keltner"]["TQQQ"]["net_pnl"] == 100.0
    assert result["books"]["control"]["TQQQ"]["net_pnl"] == 50.0
