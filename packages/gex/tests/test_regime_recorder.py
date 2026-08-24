"""The market-regime sampler's guards, each verified by breaking the invariant on purpose during
development and watching the test report the right thing (the suite rule: a guard has to be shown
to fail).

Covers: the frozen-quote refusal (a stale print becomes a refusal row, never a value), the
missing-quote refusal, the RTH gate, the DB-side throttle, the daily-close harvest, the
declaration-coverage guard (driven off ``regime.READINGS`` itself, so a new reading without its
subscription fails here the moment it is declared), and the dropped-readings stale-checkout guard.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pytest
from cherrypick.core import streamcache
from cherrypick.core.clock import ET

from cherrypick.gex import regime, stream_request

# A Tuesday, mid-session, not a holiday.
RTH_NOW = datetime(2026, 8, 18, 13, 0, 0, tzinfo=ET)


@pytest.fixture()
def cfg(tmp_path):
    return {
        "symbols": ["SPX"],
        "stream_cache_db": tmp_path / "stream_cache.db",
        "history_db_path": tmp_path / "gex_history.db",
    }


def seed_cache(cfg, quotes: dict[str, tuple[float, float]], summary_rows=()):
    """Create a real-DDL stream cache holding `quotes` = {symbol: (last, updated_at)}."""
    conn = streamcache.connect(cfg["stream_cache_db"])
    try:
        for sym, (last, updated) in quotes.items():
            conn.execute(
                "INSERT OR REPLACE INTO stream_trades (symbol, last, updated_at) VALUES (?,?,?)",
                (sym, last, updated),
            )
        for sym, trade_date, day_close in summary_rows:
            conn.execute(
                "INSERT OR REPLACE INTO stream_summary (symbol, trade_date, day_close, updated_at) "
                "VALUES (?,?,?,?)",
                (sym, trade_date, day_close, RTH_NOW.timestamp()),
            )
        conn.commit()
    finally:
        conn.close()


def history_rows(cfg, sql, params=()):
    conn = sqlite3.connect(cfg["history_db_path"])
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def all_fresh_quotes(now_ts):
    return {sym: (100.0 + i, now_ts - 5) for i, sym in enumerate(sorted(set(regime.READINGS.values())))}


def test_fresh_quotes_write_usable_rows(cfg):
    now_ts = RTH_NOW.timestamp()
    seed_cache(cfg, all_fresh_quotes(now_ts))
    out = regime.sample(cfg, now=RTH_NOW)
    assert out["status"] == "sampled"
    assert out["written"] == len(regime.READINGS)
    assert out["usable"] == len(regime.READINGS)
    rows = history_rows(cfg, "SELECT * FROM market_regime_history WHERE reading = 'vix'")
    assert len(rows) == 1
    assert rows[0]["usable"] == 1
    assert rows[0]["value"] is not None
    assert rows[0]["basis_ts"] == pytest.approx(now_ts - 5)
    assert rows[0]["symbol"] == "VIX"


def test_frozen_quote_is_refused_not_recorded(cfg):
    """THE frozen-quote guard: a print older than the age gate lands as usable=0 with the reason
    and NO value — never the stale number (the flat-line failure the spot trail already fixed)."""
    now_ts = RTH_NOW.timestamp()
    quotes = all_fresh_quotes(now_ts)
    quotes["VIX"] = (55.5, now_ts - regime.MAX_QUOTE_AGE_SECONDS - 60)  # frozen
    seed_cache(cfg, quotes)
    out = regime.sample(cfg, now=RTH_NOW)
    assert out["usable"] == len(regime.READINGS) - 1
    row = history_rows(cfg, "SELECT * FROM market_regime_history WHERE reading = 'vix'")[0]
    assert row["usable"] == 0
    assert row["reason"] == "stale_quote"
    assert row["value"] is None
    assert row["basis_ts"] is not None  # the evidence of HOW stale is kept


def test_missing_quote_is_refused(cfg):
    now_ts = RTH_NOW.timestamp()
    quotes = all_fresh_quotes(now_ts)
    del quotes["VVIX"]
    seed_cache(cfg, quotes)
    regime.sample(cfg, now=RTH_NOW)
    row = history_rows(cfg, "SELECT * FROM market_regime_history WHERE reading = 'vvix'")[0]
    assert row["usable"] == 0
    assert row["reason"] == "no_quote"


def test_outside_rth_writes_nothing(cfg):
    seed_cache(cfg, all_fresh_quotes(RTH_NOW.timestamp()))
    evening = RTH_NOW.replace(hour=19)
    assert regime.sample(cfg, now=evening) == {"status": "closed", "written": 0, "usable": 0}
    saturday = datetime(2026, 8, 22, 13, 0, 0, tzinfo=ET)
    assert regime.sample(cfg, now=saturday)["status"] == "closed"
    # And no table rows at all — outside-RTH silence is the legible gap, not a refusal row.
    conn = sqlite3.connect(cfg["history_db_path"]) if cfg["history_db_path"].exists() else None
    if conn is not None:
        try:
            regime.ensure_tables(conn)
            assert conn.execute("SELECT COUNT(*) FROM market_regime_history").fetchone()[0] == 0
        finally:
            conn.close()


def test_throttled_within_interval(cfg):
    seed_cache(cfg, all_fresh_quotes(RTH_NOW.timestamp()))
    assert regime.sample(cfg, now=RTH_NOW)["status"] == "sampled"
    soon = RTH_NOW.replace(second=30)
    assert regime.sample(cfg, now=soon)["status"] == "throttled"
    minute_later = RTH_NOW.replace(minute=1)
    assert regime.sample(cfg, now=minute_later)["status"] == "sampled"


def test_daily_closes_harvest_is_append_only(cfg):
    now_ts = RTH_NOW.timestamp()
    seed_cache(
        cfg,
        all_fresh_quotes(now_ts),
        summary_rows=[("XLK", "2026-08-17", 231.5), ("SPY", "2026-08-17", 645.2)],
    )
    regime.sample(cfg, now=RTH_NOW)
    rows = history_rows(cfg, "SELECT * FROM daily_closes ORDER BY symbol")
    assert [(r["symbol"], r["trade_date"], r["close"]) for r in rows] == [
        ("SPY", "2026-08-17", 645.2),
        ("XLK", "2026-08-17", 231.5),
    ]
    # A second harvest (next sample) must not duplicate or rewrite.
    regime.sample(cfg, now=RTH_NOW.replace(minute=2))
    assert len(history_rows(cfg, "SELECT * FROM daily_closes")) == 2


def test_every_reading_symbol_is_declared_to_the_streamer(cfg, managed_home):
    """Declaration coverage, driven off READINGS itself: a reading added without a subscription
    fails here the moment it is declared, with no hand-kept list to forget."""
    path = stream_request.write(cfg["symbols"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    declared = set(payload["symbols"]) | set(payload["legs"])
    for reading, symbol in regime.READINGS.items():
        assert symbol in declared, f"reading '{reading}' ({symbol}) has no stream subscription"
    # And the legs carry a bounded history request so daily_closes backfills on day one.
    for leg in payload["legs"]:
        assert payload["history_days"].get(leg) == 270


def test_dropped_readings_flags_a_stale_checkout(cfg):
    seed_cache(cfg, all_fresh_quotes(RTH_NOW.timestamp()))
    regime.sample(cfg, now=RTH_NOW)
    conn = sqlite3.connect(cfg["history_db_path"])
    try:
        # A newer checkout recorded a reading this code does not declare, yesterday.
        conn.execute(
            "INSERT INTO market_regime_history "
            "(trade_date, ts, reading, symbol, value, basis_ts, usable, reason) "
            "VALUES ('2026-08-17', ?, 'skew', 'SKEW', 141.0, ?, 1, NULL)",
            (RTH_NOW.timestamp() - 86400, RTH_NOW.timestamp() - 86400),
        )
        conn.commit()
        assert regime.dropped_readings(conn, today="2026-08-18") == {"skew"}
        # Nothing recorded before today -> nothing to flag.
        conn.execute("DELETE FROM market_regime_history WHERE trade_date < '2026-08-18'")
        conn.commit()
        assert regime.dropped_readings(conn, today="2026-08-18") == set()
    finally:
        conn.close()
