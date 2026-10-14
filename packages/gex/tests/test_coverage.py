"""The recorder's durable session range (`daily_sessions`) and the per-session coverage verdict."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest
from cherrypick.core import streamcache
from cherrypick.core.clock import ET
from test_regime_recorder import RTH_NOW, all_fresh_quotes, history_rows

from cherrypick.gex import coverage, regime


@pytest.fixture()
def cfg(tmp_path):
    return {
        "symbols": ["SPX"],
        "stream_cache_db": tmp_path / "stream_cache.db",
        "history_db_path": tmp_path / "gex_history.db",
        "quote_history": {"enabled": True, "symbols": ["SPX"]},
    }


def _seed(cfg, summary):
    conn = streamcache.connect(cfg["stream_cache_db"])
    try:
        for sym, (last, updated) in all_fresh_quotes(RTH_NOW.timestamp()).items():
            conn.execute(
                "INSERT OR REPLACE INTO stream_trades (symbol, last, updated_at) VALUES (?,?,?)",
                (sym, last, updated),
            )
        for sym, day, o, h, low, c in summary:
            conn.execute(
                "INSERT OR REPLACE INTO stream_summary (symbol, trade_date, day_open, day_high, day_low, "
                "day_close, updated_at) VALUES (?,?,?,?,?,?,?)",
                (sym, day, o, h, low, c, RTH_NOW.timestamp()),
            )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- daily_sessions
def test_finished_sessions_are_harvested_once_and_today_never(cfg):
    _seed(
        cfg,
        [
            ("SPX", "2026-08-17", 6400.0, 6431.5, 6388.25, 6420.0),
            ("SPX", "2026-08-18", 6421.0, 6440.0, 6410.0, None),  # today: its range still moves
            ("SPX", "2026-08-14", 6390.0, None, 6380.0, 6385.0),  # a cleared high is never filled in
        ],
    )
    regime.sample(cfg, now=RTH_NOW)
    rows = history_rows(cfg, "SELECT * FROM daily_sessions")
    assert [(r["trade_date"], r["day_high"], r["day_low"], r["source"]) for r in rows] == [
        ("2026-08-17", 6431.5, 6388.25, "stream_summary")
    ]
    regime.sample(cfg, now=RTH_NOW + timedelta(minutes=2))
    assert len(history_rows(cfg, "SELECT * FROM daily_sessions")) == 1, "append-only"


# --------------------------------------------------------------------------- the verdict
def test_judge_counts_the_session_edges_as_gaps():
    start, end = coverage.session_bounds("2026-10-14")
    every_15s = [start + 15 * i for i in range(int((end - start) / 15) + 1)]
    assert coverage.judge(every_15s, start, end, 120)["ok"]
    stopped = [t for t in every_15s if t < start + 5 * 3600]  # the 10-13 shape: silent from 14:30
    out = coverage.judge(stopped, start, end, 120)
    assert not out["ok"] and out["gaps"][-1]["to"] == "16:00:00"
    late = [t for t in every_15s if t > start + 600]
    assert coverage.judge(late, start, end, 120)["gaps"][0]["from"] == "09:30:00"
    assert coverage.judge([], start, end, 120)["ok"] is False, "no rows is never a full session"


def test_an_early_close_session_ends_at_13_00():
    start, end = coverage.session_bounds("2026-11-27")  # the day after Thanksgiving
    assert datetime.fromtimestamp(end, ET).strftime("%H:%M") == "13:00"
    assert end - start == 3.5 * 3600


def _history(cfg, session, *, spot_until_h=16.0, quotes=True):
    start, end = coverage.session_bounds(session)
    conn = sqlite3.connect(cfg["history_db_path"])
    regime.ensure_tables(conn)
    conn.execute("CREATE TABLE gex_spot_history (symbol TEXT, trade_date TEXT, ts REAL, spot REAL)")
    conn.execute("CREATE TABLE gex_regime_history (symbol TEXT, trade_date TEXT, ts REAL)")
    conn.execute("CREATE TABLE gex_profile_history (symbol TEXT, trade_date TEXT, ts REAL, strike REAL)")
    conn.execute(
        "CREATE TABLE gex_quote_samples (id INTEGER PRIMARY KEY, symbol TEXT, trade_date TEXT, ts REAL)"
    )
    stop = start + (spot_until_h - 9.5) * 3600
    t = start
    while t <= end:
        if t <= stop:
            conn.execute("INSERT INTO gex_spot_history VALUES ('SPX', ?, ?, 6000)", (session, t))
            if quotes:
                conn.execute(
                    "INSERT INTO gex_quote_samples (symbol, trade_date, ts) VALUES ('SPX', ?, ?)",
                    (session, t),
                )
        if int(t - start) % 300 == 0:
            conn.execute("INSERT INTO gex_regime_history VALUES ('SPX', ?, ?)", (session, t))
            conn.execute("INSERT INTO gex_profile_history VALUES ('SPX', ?, ?, 6000)", (session, t))
        if int(t - start) % 60 == 0:
            conn.execute(
                "INSERT INTO market_regime_history (trade_date, ts, reading, usable) VALUES (?, ?, 'vix', 1)",
                (session, t),
            )
        t += 15
    conn.commit()
    return conn


def test_a_full_session_is_complete_and_a_stopped_spot_trail_is_named(cfg):
    conn = _history(cfg, "2026-10-14")
    out = coverage.session_coverage(conn, "2026-10-14", cfg)
    assert out["ok"] and out["failing"] == []
    assert set(out["series"]) == {"spot:SPX", "gex_regime:SPX", "profile:SPX", "quotes:SPX", "market_regime"}
    conn.close()

    cfg["history_db_path"] = cfg["history_db_path"].with_name("stopped.db")
    conn = _history(cfg, "2026-10-14", spot_until_h=14.9)
    out = coverage.session_coverage(conn, "2026-10-14", cfg)
    assert out["failing"] == ["quotes:SPX", "spot:SPX"], "the GEX series kept going; only these stopped"


def test_quotes_are_judged_only_where_the_machine_records_them(cfg):
    cfg["quote_history"] = {"enabled": False}
    conn = _history(cfg, "2026-10-14", quotes=False)
    assert "quotes:SPX" not in coverage.session_coverage(conn, "2026-10-14", cfg)["series"]


def test_the_daily_session_row_is_judged_for_the_previous_session_from_10_15(cfg):
    conn = _history(cfg, "2026-10-15")
    out = coverage.session_coverage(conn, "2026-10-15", cfg)
    assert (
        out["failing"] == ["daily_session:SPX"]
        and out["series"]["daily_session:SPX"]["session"] == "2026-10-14"
    )
    conn.execute(
        "INSERT INTO daily_sessions VALUES ('SPX', '2026-10-14', 7774.18, 7789.15, 7716.3, 7718.94, 0, 'stream_summary')"
    )
    assert coverage.session_coverage(conn, "2026-10-15", cfg)["ok"]


def test_run_reports_a_missing_history_db_rather_than_raising(cfg):
    out = coverage.run(cfg, "2026-10-14")
    assert out["complete"] is False and out["failing"] == ["history_db_missing"]
