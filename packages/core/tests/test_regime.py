"""cherrypick.core.regime — the one nearest-row join every regime consumer goes through.

The two guards verified by breaking them on purpose during development: the join refuses beyond
its staleness bound (never returns the last value as "the regime"), and it never looks ahead (a
sample after the asked-for moment is invisible, however close)."""

from __future__ import annotations

import sqlite3

import pytest

from cherrypick.core import regime

T0 = 1_776_000_000.0  # an arbitrary fixed moment; everything is relative to it


@pytest.fixture()
def history_db(tmp_path):
    path = tmp_path / "gex_history.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE market_regime_history ("
        "trade_date TEXT NOT NULL, ts REAL NOT NULL, reading TEXT NOT NULL, symbol TEXT, "
        "value REAL, basis_ts REAL, usable INTEGER NOT NULL, reason TEXT)"
    )
    conn.execute(
        "CREATE TABLE daily_closes (symbol TEXT NOT NULL, trade_date TEXT NOT NULL, "
        "close REAL NOT NULL, recorded_at REAL NOT NULL, source TEXT NOT NULL, "
        "PRIMARY KEY (symbol, trade_date))"
    )
    conn.execute(
        "CREATE TABLE gex_regime_history ("
        "symbol TEXT NOT NULL, trade_date TEXT NOT NULL, ts REAL NOT NULL, spot REAL, "
        "net_gex REAL, net_gex_vol REAL, zero_gamma REAL, call_wall REAL, put_wall REAL, "
        "expiration TEXT)"
    )

    def mrow(ts, reading, symbol, value, usable=1, reason=None, trade_date="2026-08-18"):
        conn.execute(
            "INSERT INTO market_regime_history VALUES (?,?,?,?,?,?,?,?)",
            (trade_date, ts, reading, symbol, value, ts - 3 if value is not None else None, usable, reason),
        )

    # One full sample at T0, another at T0+60 with a different VIX.
    for ts, vix in ((T0, 15.0), (T0 + 60, 22.0)):
        mrow(ts, "vix", "VIX", vix)
        mrow(ts, "vix3m", "VIX3M", 20.0)
        mrow(ts, "vvix", "VVIX", None, usable=0, reason="stale_quote")
    conn.execute(
        "INSERT INTO gex_regime_history VALUES "
        "('SPX','2026-08-18',?,6400.0,1.2e9,0.8e9,6380.0,6450.0,6300.0,'2026-08-18')",
        (T0,),
    )
    conn.commit()
    conn.close()
    return path


def test_joins_at_or_before_with_derived_ratio(history_db):
    out = regime.regime_at(T0 + 30, history_db=history_db)
    market = out["market"]
    assert market["status"] == "measured"
    assert market["sample_ts"] == T0  # the T0+60 sample is the future and must be invisible
    assert market["readings"]["vix"]["value"] == 15.0
    assert market["derived"]["vix_vix3m_ratio"] == pytest.approx(0.75)
    # A refused reading joins as unusable, and a ratio over it refuses too.
    assert market["readings"]["vvix"]["usable"] is False
    assert market["derived"]["vvix_vix_ratio"] is None
    gex = out["gex"]["SPX"]
    assert gex["status"] == "measured"
    assert gex["zero_gamma"] == 6380.0


def test_no_lookahead_even_when_the_future_sample_is_nearest(history_db):
    # 1s before the T0+60 sample: nearest row is the future one; the join must take T0.
    out = regime.regime_at(T0 + 59, history_db=history_db)
    assert out["market"]["sample_ts"] == T0
    assert out["market"]["readings"]["vix"]["value"] == 15.0


def test_refuses_beyond_the_staleness_bound(history_db):
    out = regime.regime_at(T0 + 60 + 901, history_db=history_db)
    assert out["market"] == {"status": "unmeasured", "reason": "stale_sample"}
    assert out["gex"]["SPX"] == {"status": "unmeasured", "reason": "stale_sample"}
    # Inside the bound the same join measures.
    assert regime.regime_at(T0 + 60 + 899, history_db=history_db)["market"]["status"] == "measured"


def test_missing_db_and_missing_tables_are_unmeasured_never_a_raise(tmp_path):
    out = regime.regime_at(T0, history_db=tmp_path / "absent.db", symbol="SPX")
    assert out["market"]["reason"] == "no_history_db"
    assert out["gex"]["SPX"]["reason"] == "no_history_db"
    bare = tmp_path / "bare.db"
    sqlite3.connect(bare).close()
    out = regime.regime_at(T0, history_db=bare, symbol="SPX")
    assert out["market"]["reason"] == "no_market_regime_table"
    assert out["gex"]["SPX"]["reason"] == "no_gex_regime_table"


def test_sector_dispersion_bounds_closes_before_the_sample_session(tmp_path):
    """The look-ahead trap specific to dispersion: a retroactive join runs after the sample day's
    OWN close landed in daily_closes, and 'latest close' would grade the morning against it."""
    path = tmp_path / "gex_history.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE market_regime_history ("
        "trade_date TEXT NOT NULL, ts REAL NOT NULL, reading TEXT NOT NULL, symbol TEXT, "
        "value REAL, basis_ts REAL, usable INTEGER NOT NULL, reason TEXT)"
    )
    conn.execute(
        "CREATE TABLE daily_closes (symbol TEXT NOT NULL, trade_date TEXT NOT NULL, "
        "close REAL NOT NULL, recorded_at REAL NOT NULL, source TEXT NOT NULL, "
        "PRIMARY KEY (symbol, trade_date))"
    )
    sectors = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"]
    for i, sym in enumerate(sectors):
        conn.execute(
            "INSERT INTO market_regime_history VALUES ('2026-08-18',?,?,?,100.0,?,1,NULL)",
            (T0, sym.lower(), sym, T0 - 3),
        )
        # Prior close 100 for every sector -> all 0% change -> dispersion exactly 0. The same-day
        # closes VARY, so the wrong (look-ahead) reference would produce a nonzero dispersion.
        conn.execute("INSERT INTO daily_closes VALUES (?,'2026-08-17',100.0,?, 'test')", (sym, T0))
        conn.execute("INSERT INTO daily_closes VALUES (?,'2026-08-18',?,?, 'test')", (sym, 80.0 + i, T0))
    conn.commit()
    conn.close()
    out = regime.regime_at(T0 + 10, history_db=path)
    assert out["market"]["derived"]["sector_dispersion"] == pytest.approx(0.0)
