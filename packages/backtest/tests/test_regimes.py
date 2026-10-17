"""Regime rows derived from a stored session: written beside the profile, read back through core.regime
(the join flies' wall-clear gate makes), and rewritten in place rather than doubled."""

from __future__ import annotations

import sqlite3

from cherrypick.core import regime as core_regime
from test_fetch import DAY, FakeClient, _rows, _settings, _ts

from cherrypick.backtest import fetch, regimes, store


class Lopsided(FakeClient):
    """Equal call and put OI everywhere nets to zero GEX at every strike, so no wall exists: put the
    calls' OI above spot and the puts' below, as a real chain has it."""

    def open_interest(self, root, day):
        oi = super().open_interest(root, day)
        oi[("CALL", 6050.0)] = 5000
        oi[("PUT", 5950.0)] = 5000
        return oi


def _fetched():
    s = _settings()
    fetch.fetch(s, Lopsided({DAY: _rows(DAY)}), "SPX", DAY, DAY, log=lambda _m: None)
    return s, store.db_path(s, "SPX")


def test_a_fetched_session_carries_regime_rows_core_regime_can_read():
    s, db = _fetched()
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM gex_regime_history WHERE trade_date = ?", (DAY,)).fetchone()[0]
    assert n == 77, "one per stored profile"
    reading = core_regime.gex_at(_ts(DAY, 12, 0), "SPX", history_db=db)
    assert reading["status"] == "measured"
    assert (reading["call_wall"], reading["put_wall"]) == (6050.0, 5950.0)


def test_derive_rewrites_in_place():
    s, db = _fetched()
    conn = store.connect(s, "SPX")
    assert regimes.derive(conn, "SPX", DAY) == 77
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM gex_regime_history").fetchone()[0] == 77


def test_an_empty_profile_is_no_reading():
    assert regimes.regime_row([], 6000.0) is None
