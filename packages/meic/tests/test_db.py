"""Unit tests for db.py's multi-symbol support: loop_log.symbol column (with
migration from pre-multi-symbol databases), and --symbol filters on the
account-wide read commands (get_open_trades, get_today_count, get_today_pnl).

No credentials or live connection required — all tests operate on a temp
SQLite database file.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from cherrypick.meic import db


@pytest.fixture
def db_path(monkeypatch, tmp_path):
    path = str(tmp_path / "meic_trades.db")
    monkeypatch.setattr(db, "_DB_PATH", path)
    monkeypatch.setattr(db, "_today_et", lambda: "2026-07-02")
    db.cmd_init_db(None)
    return path


def _insert_trade(db_path, **kwargs):
    defaults = dict(
        trade_date="2026-07-02",
        entry_time="2026-07-02T10:00:00",
        symbol="XSP",
        put_strike=590,
        call_strike=600,
        wing_width=2,
        net_credit=0.5,
        quantity=1,
        status="expired",
        pnl=1.0,
        fees=0.2,
        ic_order_id="IC-1",
        created_at="2026-07-02T10:00:00",
        updated_at="2026-07-02T10:00:00",
    )
    defaults.update(kwargs)
    conn = sqlite3.connect(db_path)
    cols = ", ".join(defaults)
    placeholders = ", ".join("?" * len(defaults))
    conn.execute(f"INSERT INTO ic_trades ({cols}) VALUES ({placeholders})", list(defaults.values()))
    conn.commit()
    conn.close()


# ── loop_log.symbol ─────────────────────────────────────────────────────────


def test_init_db_creates_loop_log_symbol_column(db_path):
    conn = sqlite3.connect(db_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(loop_log)")}
    conn.close()
    assert "symbol" in cols


def test_init_db_creates_loop_log_symbol_index(db_path):
    conn = sqlite3.connect(db_path)
    idx = {row[1] for row in conn.execute("PRAGMA index_list(loop_log)")}
    conn.close()
    assert "idx_loop_log_symbol_date" in idx


def test_init_db_migrates_preexisting_loop_log_without_symbol(monkeypatch, tmp_path):
    """A database created before multi-symbol support has no `symbol` column on
    loop_log; init_db must add it (and the index) without erroring."""
    path = str(tmp_path / "meic_trades.db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE loop_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            loop_time TEXT NOT NULL, loop_date TEXT NOT NULL,
            action TEXT, reasoning TEXT,
            open_trades_n INTEGER DEFAULT 0, today_count INTEGER DEFAULT 0, today_pnl REAL DEFAULT 0,
            iv_rank REAL, underlying_price REAL, session_quality TEXT,
            mcp_errors TEXT DEFAULT '[]', duration_ms INTEGER, created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

    monkeypatch.setattr(db, "_DB_PATH", path)
    db.cmd_init_db(None)

    conn = sqlite3.connect(path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(loop_log)")}
    idx = {row[1] for row in conn.execute("PRAGMA index_list(loop_log)")}
    conn.close()
    assert "symbol" in cols
    assert "idx_loop_log_symbol_date" in idx


def test_log_loop_action_stores_symbol(db_path):
    args = argparse.Namespace(
        symbol="XSP",
        action="entry",
        reasoning="test",
        market_context="{}",
        iv_rank=0.4,
        session_quality="prime",
        underlying_price=600.0,
        open_trades=1,
        today_count=1,
        today_pnl=0.5,
        duration_ms=None,
    )
    db.cmd_log_loop_action(args)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT symbol, action FROM loop_log ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert row["symbol"] == "XSP"
    assert row["action"] == "entry"


def test_log_loop_action_symbol_none_for_iteration_summary(db_path):
    """An iteration-level summary row (not tied to one symbol) stores symbol=NULL."""
    args = argparse.Namespace(
        symbol=None,
        action="iteration_summary",
        reasoning="",
        market_context="{}",
        iv_rank=None,
        session_quality=None,
        underlying_price=None,
        open_trades=None,
        today_count=None,
        today_pnl=None,
        duration_ms=None,
    )
    db.cmd_log_loop_action(args)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT symbol FROM loop_log ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert row["symbol"] is None


def _log_action(symbol, action, duration_ms):
    db.cmd_log_loop_action(
        argparse.Namespace(
            symbol=symbol,
            action=action,
            reasoning="",
            market_context="{}",
            iv_rank=None,
            session_quality=None,
            underlying_price=None,
            open_trades=None,
            today_count=None,
            today_pnl=None,
            duration_ms=duration_ms,
        )
    )


def test_log_loop_action_stores_duration_ms(db_path):
    _log_action(None, "timing_stop_management", 842)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT duration_ms FROM loop_log ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert row["duration_ms"] == 842


def test_get_step_timing_summarizes_by_action(db_path, capsys):
    _log_action(None, "timing_stop_management", 800)
    _log_action(None, "timing_stop_management", 1200)
    _log_action("XSP", "timing_entry_evaluation", 3000)

    db.cmd_get_step_timing(argparse.Namespace(action=None, symbol=None, lookback_days=None))
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    assert out["ok"] is True
    assert out["by_action"]["timing_stop_management"]["sample_size"] == 2
    assert out["by_action"]["timing_stop_management"]["avg_ms"] == 1000.0
    assert out["by_action"]["timing_entry_evaluation"]["sample_size"] == 1


def test_get_step_timing_filters_by_action(db_path, capsys):
    _log_action(None, "timing_stop_management", 500)
    _log_action("SPX", "timing_entry_evaluation", 2500)

    db.cmd_get_step_timing(
        argparse.Namespace(action="timing_entry_evaluation", symbol=None, lookback_days=None)
    )
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    assert list(out["by_action"].keys()) == ["timing_entry_evaluation"]


# ── --symbol filters on account-wide read commands ──────────────────────────


def test_get_open_trades_no_filter_returns_all_symbols(db_path, capsys):
    _insert_trade(db_path, ic_order_id="IC-X", symbol="XSP", status="open")
    _insert_trade(db_path, ic_order_id="IC-S", symbol="SPX", status="open")
    db.cmd_get_open_trades(argparse.Namespace(symbol=None))
    out = json.loads(capsys.readouterr().out)
    assert len(out["open_trades"]) == 2


def test_get_open_trades_filtered_by_symbol(db_path, capsys):
    _insert_trade(db_path, ic_order_id="IC-X", symbol="XSP", status="open")
    _insert_trade(db_path, ic_order_id="IC-S", symbol="SPX", status="open")
    db.cmd_get_open_trades(argparse.Namespace(symbol="XSP"))
    out = json.loads(capsys.readouterr().out)
    assert len(out["open_trades"]) == 1
    assert out["open_trades"][0]["symbol"] == "XSP"


def test_get_open_trades_filter_is_case_insensitive(db_path, capsys):
    _insert_trade(db_path, ic_order_id="IC-X", symbol="XSP", status="open")
    db.cmd_get_open_trades(argparse.Namespace(symbol="xsp"))
    out = json.loads(capsys.readouterr().out)
    assert len(out["open_trades"]) == 1


def test_get_today_count_no_filter_counts_all_symbols(db_path, capsys):
    _insert_trade(db_path, ic_order_id="IC-X", symbol="XSP")
    _insert_trade(db_path, ic_order_id="IC-S", symbol="SPX")
    db.cmd_get_today_count(argparse.Namespace(symbol=None))
    out = json.loads(capsys.readouterr().out)
    assert out["today_count"] == 2


def test_get_today_count_filtered_by_symbol(db_path, capsys):
    _insert_trade(db_path, ic_order_id="IC-X", symbol="XSP")
    _insert_trade(db_path, ic_order_id="IC-S", symbol="SPX")
    db.cmd_get_today_count(argparse.Namespace(symbol="SPX"))
    out = json.loads(capsys.readouterr().out)
    assert out["today_count"] == 1


def test_get_today_pnl_no_filter_sums_all_symbols(db_path, capsys):
    _insert_trade(db_path, ic_order_id="IC-X", symbol="XSP", pnl=1.0)
    _insert_trade(db_path, ic_order_id="IC-S", symbol="SPX", pnl=3.0)
    db.cmd_get_today_pnl(argparse.Namespace(symbol=None))
    out = json.loads(capsys.readouterr().out)
    assert out["today_pnl"] == 4.0


def test_get_today_pnl_filtered_by_symbol(db_path, capsys):
    _insert_trade(db_path, ic_order_id="IC-X", symbol="XSP", pnl=1.0)
    _insert_trade(db_path, ic_order_id="IC-S", symbol="SPX", pnl=3.0)
    db.cmd_get_today_pnl(argparse.Namespace(symbol="XSP"))
    out = json.loads(capsys.readouterr().out)
    assert out["today_pnl"] == 1.0


def test_get_eod_summary_spans_all_symbols(db_path, capsys):
    """EOD summary is intentionally account-wide (one combined report per day covering
    every symbol) — see CLAUDE.md Step 8 and eod-report.md."""
    _insert_trade(db_path, ic_order_id="IC-X", symbol="XSP", pnl=1.0, fees=0.1)
    _insert_trade(db_path, ic_order_id="IC-S", symbol="SPX", pnl=3.0, fees=0.3)
    db.cmd_get_eod_summary(None)
    out = json.loads(capsys.readouterr().out)
    assert out["total_entries"] == 2
    assert abs(out["net_pnl"] - 3.6) < 0.01


def test_get_eod_summary_counts_wins_by_net_pnl(db_path, capsys):
    """One win definition module-wide: an expired IC that lost money (ITM short) is a
    LOSS, a profitable force-close is a WIN, and fees can flip a small gross winner.
    Status is a lifecycle fact, not a verdict — matches _range_stats_for_rows and the
    orchestrator's calibrate reading."""
    # Expired but net-negative (short went ITM): pnl -2.0 — must count as a loss.
    _insert_trade(db_path, ic_order_id="IC-EXP-LOSS", status="expired", pnl=-2.0, fees=0.2)
    # Force-closed at a profit: must count as a win.
    _insert_trade(db_path, ic_order_id="IC-FC-WIN", status="force_closed", pnl=1.5, fees=0.2)
    # Fees flip a small gross winner negative: net 0.1 - 0.2 <= 0 — a loss.
    _insert_trade(db_path, ic_order_id="IC-FEE-FLIP", status="expired", pnl=0.1, fees=0.2)
    # Still open (no pnl yet): excluded from the win-rate denominator entirely.
    _insert_trade(db_path, ic_order_id="IC-OPEN", status="open", pnl=None, fees=None)
    db.cmd_get_eod_summary(None)
    out = json.loads(capsys.readouterr().out)
    assert out["win_count"] == 1
    # 3 resolved trades (open excluded): 1 win / 3 = 33.3%
    assert out["win_rate_pct"] == 33.3


# ── _migrate / stale_writer_columns / era backfill ──────────────────────────


def test_migrate_adds_every_added_trade_column(db_path):
    """db_path's fixture already ran cmd_init_db once; every _ADDED_TRADE_COLUMNS key must be
    present on a freshly-created database, not just one migrated from an older file."""
    conn = sqlite3.connect(db_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(ic_trades)")}
    conn.close()
    for column in db._ADDED_TRADE_COLUMNS:
        assert column in cols, column


def test_migrate_on_a_database_missing_every_added_column(tmp_path, monkeypatch):
    """The realistic upgrade path: a pre-regime-tagging ic_trades table (only the columns from
    the original CREATE TABLE, none of _ADDED_TRADE_COLUMNS) must gain all of them, including the
    new regime/era/covariate columns, without erroring."""
    path = str(tmp_path / "meic_trades.db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE ic_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL, symbol TEXT NOT NULL, status TEXT DEFAULT 'pending',
            put_strike REAL, call_strike REAL, underlying_price_entry REAL,
            net_credit REAL, wing_width REAL,
            ic_order_id TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT INTO ic_trades (trade_date, symbol, ic_order_id, created_at, updated_at) "
        "VALUES ('2026-07-01', 'SPX', 'PRE-EXISTING-1', 'x', 'x')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(db, "_DB_PATH", path)
    db.cmd_init_db(None)

    conn = sqlite3.connect(path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(ic_trades)")}
    row = conn.execute("SELECT era FROM ic_trades WHERE ic_order_id = 'PRE-EXISTING-1'").fetchone()
    conn.close()
    for column in db._ADDED_TRADE_COLUMNS:
        assert column in cols, column
    assert row[0] == "book"  # pre-existing row backfilled, not left at the 'sample' SQL default


def test_migrate_stamps_book_once_and_never_overwrites_a_later_value(db_path):
    """The era backfill is a one-time correction guarded on the column having just been added
    (mirrors flies' _VOID_BACKFILL guard) — a later deliberate change to a row's era must survive
    a subsequent cmd_init_db call, not get silently reset."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ic_trades (trade_date, symbol, ic_order_id, era, created_at, updated_at) "
        "VALUES ('2026-08-07', 'SPX', 'IC-ERA-1', 'sample', 'x', 'x')"
    )
    conn.commit()
    conn.close()

    db.cmd_init_db(None)  # era column already exists -> the backfill branch must NOT re-run

    conn = sqlite3.connect(db_path)
    era = conn.execute("SELECT era FROM ic_trades WHERE ic_order_id = 'IC-ERA-1'").fetchone()[0]
    conn.close()
    assert era == "sample"


def test_migrate_returns_added_columns(tmp_path, monkeypatch):
    path = str(tmp_path / "meic_trades.db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE ic_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, trade_date TEXT NOT NULL, symbol TEXT NOT NULL,
            put_strike REAL, call_strike REAL, underlying_price_entry REAL,
            net_credit REAL, wing_width REAL,
            ic_order_id TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    added = db._migrate(conn)
    conn.close()
    assert "ic_trades.era" in added
    assert "ic_trades.entry_trend_bucket" in added
    assert len(added) == len(db._ADDED_TRADE_COLUMNS)


def test_migrate_is_a_noop_on_an_already_migrated_table(db_path):
    conn = sqlite3.connect(db_path)
    added = db._migrate(conn)
    conn.close()
    assert added == []


def test_stale_writer_columns_empty_on_a_fresh_database(db_path):
    conn = sqlite3.connect(db_path)
    stale = db.stale_writer_columns(conn)
    conn.close()
    assert stale == []


def test_stale_writer_columns_flags_a_column_classify_regime_no_longer_writes(db_path):
    """A regime dimension renamed or removed in code, but never migrated out of an existing
    paper/live DB, must be flagged rather than silently ignored — the shape match
    (entry_<dim>_{bucket,value}) is what catches it without an exclusion list to maintain."""
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE ic_trades ADD COLUMN entry_retired_dim_bucket TEXT")
    conn.execute("ALTER TABLE ic_trades ADD COLUMN entry_retired_dim_value REAL")
    stale = db.stale_writer_columns(conn)
    conn.close()
    assert stale == ["entry_retired_dim_bucket", "entry_retired_dim_value"]


def test_stale_writer_columns_ignores_non_regime_entry_prefixed_columns(db_path):
    """Only columns matching the entry_<dim>_{bucket,value} SHAPE are regime columns — an
    unrelated entry_-prefixed column (e.g. a future entry_time-adjacent field) must not be
    mistaken for a stale regime writer."""
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE ic_trades ADD COLUMN entry_time_zone TEXT")
    stale = db.stale_writer_columns(conn)
    conn.close()
    assert stale == []


# ── free-history backfill (center_offset, credit_richness) ─────────────────


def test_migrate_backfills_center_offset_on_preexisting_rows(tmp_path, monkeypatch):
    """center_offset is derivable from columns every pre-arms row already has — it must be
    backfilled the one time the column is added, not left NULL until the row's next write."""
    path = str(tmp_path / "meic_trades.db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE ic_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL, symbol TEXT NOT NULL, status TEXT DEFAULT 'pending',
            put_strike REAL, call_strike REAL, underlying_price_entry REAL,
            net_credit REAL, wing_width REAL,
            ic_order_id TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT INTO ic_trades (trade_date, symbol, put_strike, call_strike, underlying_price_entry, "
        "net_credit, wing_width, ic_order_id, created_at, updated_at) "
        "VALUES ('2026-07-01', 'SPX', 6990.0, 7060.0, 7000.0, 2.0, 10.0, 'PRE-1', 'x', 'x')"
    )
    # A row with no strikes/credit recorded (a cancelled entry attempt) must not error the backfill.
    conn.execute(
        "INSERT INTO ic_trades (trade_date, symbol, ic_order_id, created_at, updated_at) "
        "VALUES ('2026-07-01', 'SPX', 'PRE-2', 'x', 'x')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(db, "_DB_PATH", path)
    db.cmd_init_db(None)

    conn = sqlite3.connect(path)
    row = conn.execute(
        "SELECT entry_center_offset_bucket, entry_center_offset_value, credit_richness "
        "FROM ic_trades WHERE ic_order_id = 'PRE-1'"
    ).fetchone()
    row2 = conn.execute(
        "SELECT entry_center_offset_bucket, credit_richness FROM ic_trades WHERE ic_order_id = 'PRE-2'"
    ).fetchone()
    conn.close()
    # midpoint (6990+7060)/2 = 7025, spot 7000 -> offset (7025-7000)/7000 = 0.00357 > 0.001 default
    assert row[0] == "above_spot"
    assert abs(row[1] - 0.0035714285714285713) < 1e-9
    assert row[2] == 0.2  # 2.0 / 10.0
    # No strikes/credit at all -> the backfill's WHERE clause skips it entirely, leaving both
    # columns NULL rather than a computed 'unknown' -- honestly "never derivable", not "tried and
    # failed to classify".
    assert row2 == (None, None)


def test_migrate_does_not_reoverwrite_center_offset_on_a_later_call(db_path):
    """The backfill runs only the migration that ADDS the column — a later cmd_init_db call
    (already-migrated table) must not touch a row's regime tags again."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ic_trades (trade_date, symbol, ic_order_id, entry_center_offset_bucket, "
        "entry_center_offset_value, created_at, updated_at) "
        "VALUES ('2026-08-07', 'SPX', 'IC-CO-1', 'above_spot', 0.0099, 'x', 'x')"
    )
    conn.commit()
    conn.close()

    db.cmd_init_db(None)  # columns already exist -> backfill branch must NOT re-run

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT entry_center_offset_bucket, entry_center_offset_value FROM ic_trades "
        "WHERE ic_order_id = 'IC-CO-1'"
    ).fetchone()
    conn.close()
    assert row == ("above_spot", 0.0099)


# --------------------------------------------------------------------------- iteration_regime


def _save_iteration(db_path, **over):
    args = dict(
        date="2026-08-10",
        time="2026-08-10 11:00:00",
        symbol="SPX",
        underlying_price=7500.0,
        entries_n=0,
        blocked_n=3,
        regime=json.dumps({"vol_implied_bucket": "low", "vol_implied_value": 0.28}),
    )
    args.update(over)
    db.cmd_save_iteration_regime(argparse.Namespace(**args))


def test_save_iteration_regime_records_a_tick_that_entered_nothing(db_path):
    """The point of the table: a tick where every gate refused still leaves a regime record. Without
    it the recorded regime distribution is censored by the gates it would be used to evaluate."""
    _save_iteration(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM iteration_regime").fetchone()
    conn.close()

    assert row["symbol"] == "SPX"
    assert row["entries_n"] == 0 and row["blocked_n"] == 3
    assert row["underlying_price"] == 7500.0
    assert row["vol_implied_bucket"] == "low"
    assert row["vol_implied_value"] == 0.28
    # A dimension the payload omitted is NULL, not an error.
    assert row["gex_bucket"] is None


def test_save_iteration_regime_is_append_only(db_path):
    """Two ticks in the same minute are two observations. Collapsing them would weight a
    slow-polling stretch the same as a fast one."""
    _save_iteration(db_path)
    _save_iteration(db_path, blocked_n=4)

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT blocked_n FROM iteration_regime ORDER BY id").fetchall()
    conn.close()
    assert [r[0] for r in rows] == [3, 4]


def test_save_iteration_regime_drops_unknown_keys(db_path):
    """The payload is interpolated into SQL by column name, so an unrecognised key must be dropped
    rather than reaching the statement."""
    _save_iteration(db_path, regime=json.dumps({"vol_implied_bucket": "low", "nonsense; DROP": 1}))

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT vol_implied_bucket FROM iteration_regime").fetchone()
    conn.close()
    assert row[0] == "low"


def test_save_iteration_regime_survives_a_malformed_payload(db_path):
    """Telemetry must never fail an iteration that is otherwise trading fine."""
    _save_iteration(db_path, regime="not json at all")

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT symbol, vol_implied_bucket FROM iteration_regime").fetchone()
    conn.close()
    assert row == ("SPX", None)


def test_save_iteration_regime_creates_the_table_on_an_older_db(tmp_path, monkeypatch):
    """Works against a paper/live DB that predates the table without re-running init_db — the same
    on-demand pattern save_market_context uses."""
    path = str(tmp_path / "old.db")
    monkeypatch.setattr(db, "_DB_PATH", path)
    sqlite3.connect(path).close()  # an empty DB: no init_db, no tables at all

    _save_iteration(path)

    conn = sqlite3.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM iteration_regime").fetchone()[0] == 1
    conn.close()


def test_save_iteration_regime_coerces_string_kwargs(tmp_path, monkeypatch):
    """db.call() hands kwargs through as strings (argparse's type= never runs on that path), which
    is how the paper loop reaches this command."""
    path = str(tmp_path / "coerce.db")
    monkeypatch.setattr(db, "_DB_PATH", path)
    sqlite3.connect(path).close()

    _save_iteration(path, underlying_price="7500.5", entries_n="2", blocked_n="1")

    conn = sqlite3.connect(path)
    row = conn.execute("SELECT underlying_price, entries_n, blocked_n FROM iteration_regime").fetchone()
    conn.close()
    assert row == (7500.5, 2, 1)


def test_iteration_regime_fields_track_the_market_dimensions():
    """Derived from regime.MARKET_DIMENSIONS rather than re-listed, so adding a dimension there
    cannot leave this command silently writing NULL for it."""
    from cherrypick.meic import regime

    expected = {f"{d}_{s}" for d in regime.MARKET_DIMENSIONS for s in ("bucket", "value")}
    assert set(db._ITERATION_REGIME_FIELDS) == expected


def test_cmd_save_trade_stamps_the_current_era(db_path):
    """The save path stamps `era` from analytics.CURRENT_ERA explicitly.

    The column's SQL DEFAULT only covers databases created after an era changes -- an existing
    ledger's ALTERed column keeps its old default forever, so without the explicit stamp every era
    change would silently keep writing the previous era's tag. One constant, one chokepoint: all
    three writers (paper, live, practice) insert through this verb.
    """
    import argparse
    import json as _json

    from cherrypick.meic.analytics import CURRENT_ERA

    payload = dict(
        trade_date="2026-08-21",
        symbol="SPX",
        put_strike=7400,
        call_strike=7500,
        wing_width=5,
        net_credit=1.5,
        quantity=1,
        status="open",
        ic_order_id="ERA-1",
    )
    db.cmd_save_trade(argparse.Namespace(data=_json.dumps(payload)))

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT era FROM ic_trades WHERE ic_order_id = 'ERA-1'").fetchone()
    conn.close()
    assert row == (CURRENT_ERA,)


def test_cmd_save_trade_respects_an_explicit_era(db_path):
    """setdefault, not overwrite: a deliberate per-row era (a backfill, a test) survives."""
    import argparse
    import json as _json

    payload = dict(
        trade_date="2026-08-01",
        symbol="SPX",
        put_strike=7400,
        call_strike=7500,
        wing_width=5,
        net_credit=1.5,
        quantity=1,
        status="expired",
        ic_order_id="ERA-2",
        era="book",
    )
    db.cmd_save_trade(argparse.Namespace(data=_json.dumps(payload)))

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT era FROM ic_trades WHERE ic_order_id = 'ERA-2'").fetchone()
    conn.close()
    assert row == ("book",)
