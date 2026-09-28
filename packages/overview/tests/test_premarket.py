"""The pre-market tape: index futures against their prior settle, and the other cash indexes.

The contract a reading samples comes from `state/futures_contracts.json`, never assembled here; a
missing or stale map is no futures at all. The change is measured against a SETTLE from Summary
rows only: for a live contract with no settle on file, the last trade is the live print itself, and
measuring against it would print a confident 0.00%.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from cherrypick.core import home

from cherrypick.overview import facts, paths, symbols

SESSION = "2026-09-28"
NOW = datetime(2026, 9, 28, 12, 25, tzinfo=UTC)  # 08:25 ET, Monday
NOW_TS = NOW.timestamp()
ES = "/ESZ26:XCME"


def _map(refreshed=NOW, contracts=None):
    contracts = contracts or {"ES": [{"streamer_symbol": ES}], "CL": [{"streamer_symbol": "/CLX26:XNYM"}]}
    path = home.state_dir() / "futures_contracts.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"refreshed_at": refreshed.isoformat(), "contracts": contracts}), encoding="utf-8"
    )


def _cache(summary=(), trades=()):
    path = paths.stream_cache_db()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE stream_trades (symbol TEXT PRIMARY KEY, last REAL, change REAL, "
        "volume REAL, updated_at REAL NOT NULL, event_at REAL);"
        "CREATE TABLE stream_summary (symbol TEXT NOT NULL, trade_date TEXT NOT NULL, "
        "day_open REAL, day_high REAL, day_low REAL, day_close REAL, prev_day_close REAL, "
        "updated_at REAL NOT NULL, PRIMARY KEY (symbol, trade_date));"
    )
    conn.executemany(
        "INSERT INTO stream_summary (symbol, trade_date, day_close, prev_day_close, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        summary,
    )
    conn.executemany(
        "INSERT INTO stream_trades (symbol, last, updated_at, event_at) VALUES (?, ?, ?, ?)", trades
    )
    conn.commit()
    return conn


def _open():
    from cherrypick.core import db

    return db.connect_ro(paths.stream_cache_db())


def test_a_live_future_is_measured_against_the_prior_settle():
    _map()
    _cache(
        summary=[(ES, "2026-09-25", 7700.0, 7690.0, NOW_TS)], trades=[(ES, 7777.0, NOW_TS - 60, NOW_TS - 60)]
    ).close()
    es = facts._premarket(_open(), SESSION, NOW_TS)["futures"]["es"]
    assert es["basis"] == "live" and es["prior_settle"] == 7700.0
    assert es["change_vs_prior_close_pct"] == 1.0


def test_todays_row_prev_day_close_is_the_settle_when_the_producer_has_written_it():
    _map()
    _cache(
        summary=[(ES, "2026-09-25", 7650.0, 7690.0, NOW_TS), (ES, SESSION, None, 7700.0, NOW_TS)],
        trades=[(ES, 7777.0, NOW_TS - 60, NOW_TS - 60)],
    ).close()
    assert facts._premarket(_open(), SESSION, NOW_TS)["futures"]["es"]["prior_settle"] == 7700.0


def test_no_settle_on_file_is_unmeasured_never_a_false_zero():
    """The live-test finding: new legs print before their first Summary lands."""
    _map()
    _cache(trades=[(ES, 7777.0, NOW_TS - 60, NOW_TS - 60)]).close()
    es = facts._premarket(_open(), SESSION, NOW_TS)["futures"]["es"]
    assert es["basis"] == "live" and es["value"] == 7777.0
    assert es["change_vs_prior_close_pct"] is None and es["change_reason"] == "no_prior_settle"


def test_a_stale_print_is_not_a_pre_market_move():
    _map()
    stale = NOW_TS - 3 * 86400
    _cache(summary=[(ES, "2026-09-25", 7700.0, 7690.0, stale)], trades=[(ES, 7710.0, stale, stale)]).close()
    es = facts._premarket(_open(), SESSION, NOW_TS)["futures"]["es"]
    assert es["change_vs_prior_close_pct"] is None and es["change_reason"] == "no_live_print"


def test_a_stale_or_missing_futures_map_means_no_futures_legs_or_readings():
    assert symbols.futures_legs() == {}
    _map(refreshed=NOW - timedelta(days=9))
    assert symbols.futures_legs(now=NOW) == {}
    _cache().close()
    es = facts._premarket(_open(), SESSION, NOW_TS)["futures"]["es"]
    assert es["value"] is None and es["reason"] == "no_contract_mapped"


def test_the_legs_come_from_the_map_not_assembled():
    _map()
    assert symbols.futures_legs(now=NOW) == {"es": ES, "cl": "/CLX26:XNYM"}


def test_every_premarket_leg_asks_for_history_so_it_gets_a_settle_row():
    """Legs get daily rows only from the history backfill; a leg without `history_days` never has a
    settle, and its change is unmeasured every morning."""
    _map()
    days = symbols._history_days(now=NOW)
    for symbol in [ES, "/CLX26:XNYM", "NDX", "DJX", "IWM"]:
        assert days.get(symbol, 0) >= 5, symbol
    assert days["VIX"] == symbols.HISTORY_LOOKBACK, "the 270-day requests are untouched"
