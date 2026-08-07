"""SQLite CLI helper for MEICAgent. All commands print JSON to stdout."""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

# Make `import paths` resolve when this file is imported (not run as the __main__ script, which
# gets its own directory on sys.path automatically) -- mirrors credentials.py's self-insert.
sys.path.insert(0, os.path.dirname(__file__))

from cherrypick.core import db as _db
from cherrypick.core import profiles as _profiles

from cherrypick.meic import paths as _paths

try:  # stdlib zoneinfo first (tzdata supplies the db on Windows); pytz only as fallback
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - only where zoneinfo has no tz database
    import pytz

    _ET = pytz.timezone("America/New_York")


def _now_et():
    return datetime.now(_ET)


def _today_et():
    return _now_et().strftime("%Y-%m-%d")


_DEFAULT_DB_PATH = str(_paths.live_db_path())  # ~/.cherrypick/data/meic/meic_trades.db (or MEIC_DATA_DIR)
# MEIC_DB_PATH lets the paper-trading engine (src/paper.py) and its skills point every
# db.py subcommand at paper_trades.db instead, without duplicating this module.
_DB_PATH = os.environ.get("MEIC_DB_PATH") or _DEFAULT_DB_PATH


def _connect():
    # cherrypick.core.db handles mkdir + row_factory=Row + pragmas. MEIC's additive/index/drop
    # migrations in cmd_init_db stay module-local (they're not the plain additive-only form).
    return _db.connect(_DB_PATH, pragmas=("journal_mode=WAL", "foreign_keys=ON"))


def _out(data):
    print(json.dumps(data, default=str))


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS ic_trades (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date                TEXT NOT NULL,
    entry_time                TEXT,
    expiration                TEXT,
    symbol                    TEXT NOT NULL,
    put_strike                REAL,
    call_strike               REAL,
    wing_width                REAL,
    put_symbol                TEXT,
    call_symbol               TEXT,
    long_put_symbol           TEXT,
    long_call_symbol          TEXT,
    put_credit                REAL,
    call_credit               REAL,
    net_credit                REAL,
    quantity                  INTEGER DEFAULT 1,
    put_delta_at_entry        REAL,
    call_delta_at_entry       REAL,
    long_put_delta_at_entry   REAL,
    long_call_delta_at_entry  REAL,
    underlying_price_entry    REAL,
    iv_rank_at_entry          REAL,
    iv_pct_at_entry           REAL,
    session_quality           TEXT,
    -- GEX regime as it stood when this entry was accepted. Recorded because the GEX gates are the
    -- one regime input whose effect could not be evaluated after the fact: `gex_positive` decides
    -- entries today, and the two opt-in variants (regime_gex_require_positive,
    -- regime_gex_min_flip_distance_pct) cannot be back-tested at all without knowing what GEX was
    -- at the moment of each fill. gamma_flip + spot are stored as a pair so flip DISTANCE is
    -- reconstructable, which is what the magnitude variant actually gates on.
    gex_net_at_entry          REAL,
    gex_positive_at_entry     INTEGER,
    gamma_flip_at_entry       REAL,
    gex_spot_at_entry         REAL,
    gex_net_vol_at_entry      REAL,
    pin_risk_applied          INTEGER,
    -- Stop-rule instrumentation. The per-side stop is the single largest loss mechanism in the paper
    -- book, and none of it was measurable after the fact: no per-leg intraday marks are stored, so an
    -- alternative threshold cannot be replayed from history.
    --   *_max_cost      the highest cost-to-close observed on that side while it was open, so "would
    --                   a wider/tighter trigger have fired?" is answerable without the full path.
    --   *_settle_value  what the side would have been worth held to settlement, recorded for stopped
    --                   sides too. `settle_value < stop_cost` == the stop paid more than holding.
    --   settle_underlying  the price those settle values were computed against.
    put_max_cost              REAL,
    call_max_cost             REAL,
    put_settle_value          REAL,
    call_settle_value         REAL,
    settle_underlying         REAL,
    --   unmarked_iterations  loop iterations this trade could not be marked (missing or crossed
    --                        leg quotes). last_unmarked_at is when that last happened. A stalled
    --                        streamer and a quiet market must not look identical in this table.
    --                        NOTE this DDL is split on semicolons - none may appear in comments.
    unmarked_iterations       INTEGER DEFAULT 0,
    last_unmarked_at          TEXT,
    --   slippage_dollars  cumulative modeled slippage conceded on this trade's fills (entry +
    --                     each priced exit). Slippage is linear in slippage_frac_of_spread, so
    --                     net P&L at a stressed 2x fraction = net - slippage_dollars exactly.
    slippage_dollars          REAL DEFAULT 0,
    iv_skew_signal            TEXT,
    price_action_signal       TEXT,
    ai_entry_reasoning        TEXT,
    ic_order_id                  TEXT UNIQUE NOT NULL,
    put_spread_entry_order_id    TEXT,
    call_spread_entry_order_id   TEXT,
    put_stop_order_id            TEXT,
    call_stop_order_id           TEXT,
    stop_trigger_original     REAL,
    stop_limit_original       REAL,
    stop_trigger_current      REAL,
    stop_limit_current        REAL,
    stop_adjustment_count     INTEGER DEFAULT 0,
    stop_adjustment_history   TEXT DEFAULT '[]',
    status                    TEXT DEFAULT 'pending',
    exit_time                 TEXT,
    exit_price                REAL,
    exit_reason               TEXT,
    exit_analysis             TEXT,
    put_stop_cost             REAL,
    call_stop_cost            REAL,
    pnl                       REAL,
    fees                      REAL,
    dollar_multiplier         REAL DEFAULT 100,
    fill_confirmed_at         TEXT,
    risk_profile              TEXT,
    execution_mode            TEXT,
    iv_rank_source            TEXT,
    created_at                TEXT NOT NULL,
    updated_at                TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ic_trades_date_status ON ic_trades(trade_date, status);
CREATE INDEX IF NOT EXISTS idx_ic_trades_symbol_status ON ic_trades(symbol, status);

CREATE TABLE IF NOT EXISTS ic_spread_legs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ic_order_id       TEXT NOT NULL REFERENCES ic_trades(ic_order_id),
    side              TEXT NOT NULL CHECK (side IN ('put', 'call')),
    status            TEXT NOT NULL DEFAULT 'open',
    exit_time         TEXT,
    exit_reason       TEXT,
    exit_price        REAL,
    pnl               REAL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE(ic_order_id, side)
);

CREATE TABLE IF NOT EXISTS daily_summary (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    summary_date        TEXT UNIQUE NOT NULL,
    symbol              TEXT,
    total_entries       INTEGER DEFAULT 0,
    entries_filled      INTEGER DEFAULT 0,
    entries_stopped     INTEGER DEFAULT 0,
    entries_expired     INTEGER DEFAULT 0,
    entries_cancelled   INTEGER DEFAULT 0,
    gross_credit        REAL DEFAULT 0,
    gross_pnl           REAL DEFAULT 0,
    fees                REAL DEFAULT 0,
    net_pnl             REAL DEFAULT 0,
    closing_nlv         REAL,
    session_init_at     TEXT,
    win_count           INTEGER DEFAULT 0,
    win_rate_pct        REAL,
    avg_iv_rank         REAL,
    sessions_entered    TEXT DEFAULT '[]',
    ai_day_summary      TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS loop_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    loop_time        TEXT NOT NULL,
    loop_date        TEXT NOT NULL,
    symbol           TEXT,
    action           TEXT,
    reasoning        TEXT,
    open_trades_n    INTEGER DEFAULT 0,
    today_count      INTEGER DEFAULT 0,
    today_pnl        REAL DEFAULT 0,
    iv_rank          REAL,
    underlying_price REAL,
    session_quality  TEXT,
    mcp_errors       TEXT DEFAULT '[]',
    duration_ms      INTEGER,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_context (
    context_date  TEXT PRIMARY KEY,
    vix           REAL,
    vix1d         REAL,
    vix1d_ratio   REAL,
    symbols_json  TEXT DEFAULT '{}',
    updated_at    TEXT NOT NULL
);

"""


# Columns added to ic_trades after the first release. CREATE TABLE IF NOT EXISTS silently does
# nothing on an existing database, so a plain schema edit would leave older paper/live DBs missing
# these and every write against them would fail at runtime rather than at startup. A flat
# {column_name: sql_type} dict, mirroring packages/flies/src/cherrypick/flies/db.py's
# _ADDED_POSITION_COLUMNS — single source of truth for both _migrate below and
# stale_writer_columns' comparison against what regime.classify_regime actually writes.
_ADDED_TRADE_COLUMNS = {
    "long_put_delta_at_entry": "REAL",
    "long_call_delta_at_entry": "REAL",
    "iv_skew_signal": "TEXT",
    "price_action_signal": "TEXT",
    "put_spread_entry_order_id": "TEXT",
    "call_spread_entry_order_id": "TEXT",
    "dollar_multiplier": "REAL DEFAULT 100",
    "risk_profile": "TEXT",
    "execution_mode": "TEXT",
    "iv_rank_source": "TEXT",
    # GEX regime at entry (see the CREATE above for why). Additive only — the orchestrator reads
    # this table through its `meic_ic` adapter, so columns may be appended but never renamed.
    "gex_net_at_entry": "REAL",
    "gex_positive_at_entry": "INTEGER",
    "gamma_flip_at_entry": "REAL",
    "gex_spot_at_entry": "REAL",
    "gex_net_vol_at_entry": "REAL",
    "pin_risk_applied": "INTEGER",
    # Stop-rule instrumentation (see the CREATE above). Additive only — the orchestrator reads
    # this table through its `meic_ic` adapter, so columns may be appended but never renamed.
    "put_max_cost": "REAL",
    "call_max_cost": "REAL",
    "put_settle_value": "REAL",
    "call_settle_value": "REAL",
    "settle_underlying": "REAL",
    # Feed-quality instrumentation: how many loop iterations this trade could not be
    # marked (missing/crossed leg quotes) and when that last happened. Distinguishes
    # a stalled streamer from a quiet market — the flies fly_snapshots lesson.
    "unmarked_iterations": "INTEGER DEFAULT 0",
    "last_unmarked_at": "TEXT",
    # Cost-sensitivity instrumentation: cumulative modeled slippage dollars (entry +
    # priced exits). Linear in the slippage fraction, so stressed-2x net reads off it.
    "slippage_dollars": "REAL DEFAULT 0",
    # Regime tags (regime.classify_regime, 'entry' phase — MEIC's ic_trades has no legging step,
    # so there is no separate 'completion' snapshot the way flies' fly_positions has one). Bucket
    # + the continuous float it was cut from, per dimension, so a threshold can be re-derived from
    # history instead of re-run. Additive only, same orchestrator constraint as everything above.
    "entry_vol_implied_bucket": "TEXT",
    "entry_vol_implied_value": "REAL",
    "entry_vol_event_bucket": "TEXT",
    "entry_vol_event_value": "REAL",
    "entry_vol_realized_bucket": "TEXT",
    "entry_vol_realized_value": "REAL",
    "entry_vol_intraday_bucket": "TEXT",
    "entry_vol_intraday_value": "REAL",
    "entry_gex_bucket": "TEXT",
    "entry_gex_value": "REAL",
    "entry_skew_bucket": "TEXT",
    "entry_skew_value": "REAL",
    "entry_center_offset_bucket": "TEXT",
    "entry_center_offset_value": "REAL",
    "entry_trend_bucket": "TEXT",
    "entry_trend_value": "REAL",
    # Float-only trade-economics covariates alongside the regime tags — not re-cuttable buckets,
    # see regime.py's module docstring for why these are kept separate from the 8 dimensions above.
    "credit_richness": "REAL",
    "put_credit_fraction": "REAL",
    "minutes_to_close": "INTEGER",
    # First-touch instrumentation: the earliest time and spot level at which the underlying
    # reached (crossed toward ITM) each short strike — write-once per side. Recorded, not acted
    # on, same convention as the settle_* counterfactual fields; what a strike-touch stop policy
    # (Phase 3) is computed from after the fact. See paper._first_touch_updates.
    "put_touch_time": "TEXT",
    "put_touch_spot": "REAL",
    "call_touch_time": "TEXT",
    "call_touch_spot": "REAL",
    # Sampling era. 'book' for every row that predates the arms/uncapped-sampling cutover (the
    # profile-ladder era, where max_concurrent_ics and entry spacing bounded each portfolio);
    # 'sample' for every row after. The two eras differ in selection intensity by roughly an
    # order of magnitude, so pooling them in one aggregate reads as one book when it is really two
    # incomparable ones — analytics._period_clause defaults to the current era for exactly this
    # reason. Stamped 'book' on every pre-existing row the ONE time this column is added (see
    # _migrate below); the SQL default handles every row inserted after.
    "era": "TEXT DEFAULT 'sample'",
}


def _migrate(conn: sqlite3.Connection) -> list[str]:
    """Add any ic_trades columns missing from an older paper/live DB. Returns what it added (for
    tests and logs). Deliberately NOT cherrypick.core.db.apply_additive_migrations' plain
    additive-only form — cmd_init_db also drops a retired column (trend_signal), which that
    helper does not support."""
    added = []
    existing = {row[1] for row in conn.execute("PRAGMA table_info(ic_trades)")}
    for column, sql_type in _ADDED_TRADE_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE ic_trades ADD COLUMN {column} {sql_type}")
            added.append(f"ic_trades.{column}")
            if column == "era":
                # SQLite's ADD COLUMN ... DEFAULT 'sample' just stamped EVERY existing row
                # 'sample' as a side effect of the ALTER above — backwards, since every row
                # already in the table at this exact point predates this migration by
                # definition (the column did not exist for any of them to have written anything
                # else). Correct them the one time this branch runs; a later INSERT that omits
                # `era` keeps hitting the column's own default ('sample'), which is what a NEW
                # arms-era row actually is.
                conn.execute("UPDATE ic_trades SET era = 'book'")
    if "ic_trades.entry_center_offset_value" in added:
        _backfill_center_offset(conn)
    if "ic_trades.credit_richness" in added:
        _backfill_credit_richness(conn)
    if added:
        conn.commit()
    return added


def _backfill_center_offset(conn: sqlite3.Connection) -> None:
    """Free history: entry_center_offset_{bucket,value} are derivable from columns every row
    already has (underlying_price_entry, put_strike, call_strike), so this backfills all 290+
    existing rows the one time the columns appear rather than waiting for new rows only. Goes
    through regime._classify_center_offset itself (not a duplicated SQL threshold) so the
    backfilled value can never drift from what a live entry would have tagged."""
    from cherrypick.meic import regime as _regime

    rows = conn.execute(
        "SELECT ic_order_id, underlying_price_entry, put_strike, call_strike FROM ic_trades "
        "WHERE put_strike IS NOT NULL AND call_strike IS NOT NULL AND underlying_price_entry IS NOT NULL"
    ).fetchall()
    for order_id, spot, put_strike, call_strike in rows:
        bucket, value = _regime._classify_center_offset(
            {"underlying_price": spot}, {}, put_strike, call_strike
        )
        conn.execute(
            "UPDATE ic_trades SET entry_center_offset_bucket = ?, entry_center_offset_value = ? "
            "WHERE ic_order_id = ?",
            (bucket, value, order_id),
        )


def _backfill_credit_richness(conn: sqlite3.Connection) -> None:
    """Free history: credit_richness (net_credit / wing_width) is derivable from columns every
    row already has. Goes through regime.credit_richness itself so the backfilled value can
    never drift from what a live entry would compute."""
    from cherrypick.meic import regime as _regime

    rows = conn.execute(
        "SELECT ic_order_id, net_credit, wing_width FROM ic_trades "
        "WHERE net_credit IS NOT NULL AND wing_width IS NOT NULL"
    ).fetchall()
    for order_id, net_credit, wing_width in rows:
        value = _regime.credit_richness(net_credit, wing_width)
        conn.execute("UPDATE ic_trades SET credit_richness = ? WHERE ic_order_id = ?", (value, order_id))


def stale_writer_columns(conn: sqlite3.Connection) -> list[str]:
    """Regime columns present in this DB file but not written by the running
    regime.classify_regime — a dimension renamed or removed in code but never migrated out of an
    existing paper/live DB. Diagnostic only; nothing calls this automatically, matching
    packages/flies/src/cherrypick/flies/db.py's stale_writer_columns. Matches on the
    entry_<dim>_{bucket,value} naming SHAPE rather than an exclusion list, so a new dimension
    can't slip past unnoticed and unrelated entry_-prefixed columns are excluded structurally."""
    from cherrypick.meic import regime as _regime

    written = {f"entry_{key}" for key in _regime.classify_regime({}, {})}
    # Positional index, not r["name"] -- PRAGMA table_info rows are read here with a plain
    # connection (no guaranteed row_factory=Row), matching the same convention _migrate above and
    # every other PRAGMA read in this module already uses.
    present = {r[1] for r in conn.execute("PRAGMA table_info(ic_trades)")}
    regime_cols = {c for c in present if c.startswith("entry_") and c.endswith(("_bucket", "_value"))}
    return sorted(regime_cols - written)


def cmd_init_db(_args):
    conn = _connect()
    for statement in _DDL.split(";"):
        stmt = statement.strip()
        if stmt:
            conn.execute(stmt)
    existing_before = {row[1] for row in conn.execute("PRAGMA table_info(ic_trades)")}
    _migrate(conn)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ic_trades_profile_date ON ic_trades(risk_profile, trade_date, status)"
    )
    # Drop columns removed from the schema
    if "trend_signal" in existing_before:
        conn.execute("ALTER TABLE ic_trades DROP COLUMN trend_signal")
    existing_ds = {row[1] for row in conn.execute("PRAGMA table_info(daily_summary)")}
    for col, col_type in [("closing_nlv", "REAL"), ("session_init_at", "TEXT")]:
        if col not in existing_ds:
            conn.execute(f"ALTER TABLE daily_summary ADD COLUMN {col} {col_type}")
    existing_ll = {row[1] for row in conn.execute("PRAGMA table_info(loop_log)")}
    if "symbol" not in existing_ll:
        conn.execute("ALTER TABLE loop_log ADD COLUMN symbol TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_loop_log_symbol_date ON loop_log(symbol, loop_date)")
    conn.commit()
    conn.close()
    _out({"ok": True, "message": "Database initialized"})


# ---------------------------------------------------------------------------
# Read commands
# ---------------------------------------------------------------------------


def cmd_get_open_trades(args):
    # --date lets callers iterating a non-"real-today" trade_date (chiefly the paper-trading
    # replay engine, which walks historical trading days) query that day's open positions
    # instead of always the live system clock's date.
    today = getattr(args, "date", None) or _today_et()
    symbol = getattr(args, "symbol", None)
    conn = _connect()
    sql = "SELECT * FROM ic_trades WHERE status IN ('pending','open','partial','partial_entry') AND trade_date = ?"
    params: list = [today]
    if symbol:
        sql += " AND symbol = ?"
        params.append(symbol.upper())
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    _out({"ok": True, "open_trades": [dict(r) for r in rows]})


def cmd_get_unsettled_stopped_trades(args):
    """Trades whose BOTH sides already stopped for real (status='stopped') and so dropped out of
    cmd_get_open_trades' status filter entirely — without a separate query they never reach the
    settle counterfactual, which is exactly the trades a double-stop analysis needs most (see
    paper._settle_stopped_trades, the virtual un-stopped path). settle_underlying IS NULL is the
    not-yet-backfilled guard: once a trade's counterfactual is written this query stops returning
    it, so a later real settlement/force-close pass for the same symbol+date doesn't re-derive
    (and can't accidentally overwrite) a value that's already there."""
    today = getattr(args, "date", None) or _today_et()
    symbol = getattr(args, "symbol", None)
    conn = _connect()
    sql = "SELECT * FROM ic_trades WHERE status = 'stopped' AND settle_underlying IS NULL AND trade_date = ?"
    params: list = [today]
    if symbol:
        sql += " AND symbol = ?"
        params.append(symbol.upper())
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    _out({"ok": True, "open_trades": [dict(r) for r in rows]})


def cmd_get_today_count(args):
    today = _today_et()
    symbol = getattr(args, "symbol", None)
    conn = _connect()
    sql = "SELECT COUNT(*) AS n FROM ic_trades WHERE trade_date = ? AND status != 'cancelled'"
    params: list = [today]
    if symbol:
        sql += " AND symbol = ?"
        params.append(symbol.upper())
    row = conn.execute(sql, params).fetchone()
    conn.close()
    _out({"ok": True, "today_count": row["n"]})


def cmd_get_today_pnl(args):
    today = _today_et()
    symbol = getattr(args, "symbol", None)
    conn = _connect()
    sql = "SELECT COALESCE(SUM(pnl), 0) AS total FROM ic_trades WHERE trade_date = ?"
    params: list = [today]
    if symbol:
        sql += " AND symbol = ?"
        params.append(symbol.upper())
    row = conn.execute(sql, params).fetchone()
    conn.close()
    _out({"ok": True, "today_pnl": round(float(row["total"]), 2)})


def cmd_get_eod_summary(_args):
    today = _today_et()
    conn = _connect()
    trades = conn.execute("SELECT * FROM ic_trades WHERE trade_date = ?", (today,)).fetchall()
    trades = [dict(r) for r in trades]

    total = len(trades)
    filled = sum(1 for t in trades if t["status"] not in ("pending", "cancelled"))
    stopped = sum(1 for t in trades if t["status"] == "stopped")
    expired = sum(1 for t in trades if t["status"] == "expired")
    cancelled = sum(1 for t in trades if t["status"] == "cancelled")
    gross_credit = sum((t["net_credit"] or 0) for t in trades)
    gross_pnl = sum((t["pnl"] or 0) for t in trades)
    fees = sum((t["fees"] or 0) for t in trades)
    net_pnl = gross_pnl - fees
    # One win definition module-wide (matches _range_stats_for_rows and the orchestrator's
    # calibrate reading): a resolved trade whose net P&L (pnl - fees) is positive. Status is
    # not a verdict -- an expired IC with an ITM short is a loss, a profitable force-close
    # is a win.
    resolved = [t for t in trades if t["pnl"] is not None]
    wins = sum(1 for t in resolved if (t["pnl"] or 0) - (t["fees"] or 0) > 0)
    win_rate = round(wins / len(resolved) * 100, 1) if resolved else None
    iv_values = [t["iv_rank_at_entry"] for t in trades if t["iv_rank_at_entry"] is not None]
    avg_iv = round(sum(iv_values) / len(iv_values), 1) if iv_values else None
    sessions = list({t["session_quality"] for t in trades if t["session_quality"]})

    loop_rows = conn.execute(
        "SELECT * FROM loop_log WHERE loop_date = ? ORDER BY loop_time DESC LIMIT 20", (today,)
    ).fetchall()
    summary_row = conn.execute(
        "SELECT ai_day_summary, closing_nlv FROM daily_summary WHERE summary_date = ?", (today,)
    ).fetchone()
    conn.close()

    _out(
        {
            "ok": True,
            "date": today,
            "total_entries": total,
            "entries_filled": filled,
            "entries_stopped": stopped,
            "entries_expired": expired,
            "entries_cancelled": cancelled,
            "gross_credit": round(gross_credit, 2),
            "gross_pnl": round(gross_pnl, 2),
            "fees": round(fees, 2),
            "net_pnl": round(net_pnl, 2),
            "win_count": wins,
            "win_rate_pct": win_rate,
            "avg_iv_rank": avg_iv,
            "sessions_entered": sessions,
            "ai_day_summary": summary_row["ai_day_summary"] if summary_row else None,
            "closing_nlv": float(summary_row["closing_nlv"])
            if summary_row and summary_row["closing_nlv"]
            else None,
            "trades": trades,
            "loop_log": [dict(r) for r in loop_rows],
        }
    )


def _range_stats_for_rows(rows: list[dict]) -> dict:
    """Compute financial stats for one already-filtered group of ic_trades rows.

    net pnl per trade = pnl - fees (matches get_eod_summary's net_pnl = gross_pnl - fees
    convention, applied per-trade so profit factor / avg win-loss are dollar-accurate).
    """
    total_trades = len(rows)
    gross_credit = sum((r["net_credit"] or 0) for r in rows)
    gross_pnl = sum((r["pnl"] or 0) for r in rows)
    fees = sum((r["fees"] or 0) for r in rows)

    resolved = [r for r in rows if r["pnl"] is not None]
    net_pnls = [(r["pnl"] or 0) - (r["fees"] or 0) for r in resolved]
    wins = [p for p in net_pnls if p > 0]
    losses = [p for p in net_pnls if p <= 0]
    win_count = len(wins)
    loss_count = len(losses)
    resolved_count = win_count + loss_count

    gross_win_total = sum(wins)
    gross_loss_total = abs(sum(losses))
    profit_factor = round(gross_win_total / gross_loss_total, 3) if gross_loss_total > 0 else None
    avg_win = round(gross_win_total / win_count, 2) if win_count else None
    avg_loss = round(sum(losses) / loss_count, 2) if loss_count else None
    win_rate_pct = round(win_count / resolved_count * 100, 1) if resolved_count else None
    net_pnl_total = sum(net_pnls)
    expectancy = round(net_pnl_total / resolved_count, 2) if resolved_count else None

    max_consecutive_losses = 0
    streak = 0
    for r in rows:
        if r["pnl"] is None:
            continue
        net = (r["pnl"] or 0) - (r["fees"] or 0)
        if net <= 0:
            streak += 1
            max_consecutive_losses = max(max_consecutive_losses, streak)
        else:
            streak = 0

    # Per-day net-pnl series with a running cumulative sum, for equity-curve /
    # drawdown / worst-day computation by the caller (report or dashboard).
    by_date: dict[str, float] = {}
    for r in rows:
        if r["pnl"] is None:
            continue
        by_date.setdefault(r["trade_date"], 0.0)
        by_date[r["trade_date"]] += (r["pnl"] or 0) - (r["fees"] or 0)
    daily_pnl = []
    running = 0.0
    peak = 0.0
    max_drawdown = 0.0
    worst_day = None
    for date in sorted(by_date):
        day_pnl = round(by_date[date], 2)
        running += day_pnl
        peak = max(peak, running)
        max_drawdown = max(max_drawdown, peak - running)
        worst_day = day_pnl if worst_day is None else min(worst_day, day_pnl)
        daily_pnl.append({"date": date, "net_pnl": day_pnl, "cumulative_pnl": round(running, 2)})

    return {
        "total_trades": total_trades,
        "gross_credit": round(gross_credit, 2),
        "gross_pnl": round(gross_pnl, 2),
        "fees": round(fees, 2),
        "net_pnl": round(net_pnl_total, 2),
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate_pct": win_rate_pct,
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy_per_trade": expectancy,
        "max_consecutive_losses": max_consecutive_losses,
        "max_drawdown": round(max_drawdown, 2),
        "worst_day": worst_day,
        "daily_pnl": daily_pnl,
    }


def cmd_get_range_summary(args):
    """Multi-day / multi-week P&L, win-rate, and drawdown rollup — the aggregation that
    get_eod_summary/get_today_pnl don't provide (both are hardcoded to today). Used by the
    paper-trading weekly report and, optionally, live multi-day review.

    Returns three views over the same rows, so nothing is double-counted:
      - `portfolios`: the atomic unit — one entry per (profile × symbol) pair, which is how the
        paper study is actually run (each pair is its own book with its own max_concurrent_ics and
        daily-target budget). Nothing nets across profiles OR symbols here.
      - `profiles`: a profile rolled up across its symbols (the historical view).
      - `by_symbol`: a symbol rolled up across profiles — "which instrument paid".
    "unassigned" collects rows with no profile tag (e.g. live trades).
    """
    if not args.start or not args.end:
        _out({"ok": False, "error": "Both --start and --end are required (YYYY-MM-DD)"})
        return
    conn = _connect()
    where = ["trade_date >= ?", "trade_date <= ?", "status NOT IN ('cancelled','pending','partial_entry')"]
    params: list = [args.start, args.end]
    if args.symbol:
        where.append("symbol = ?")
        params.append(args.symbol.upper())
    if args.profile:
        where.append("risk_profile = ?")
        params.append(args.profile)
    rows = _rows_dicts(conn, where, params)
    conn.close()

    profiles = _profiles.compare_profiles(rows, tag_key="risk_profile", summarize=_range_stats_for_rows)

    # Atomic (profile × symbol) portfolios, plus the by-symbol lens.
    buckets: dict[tuple, list] = {}
    by_symbol_rows: dict[str, list] = {}
    for r in rows:
        prof = _profiles.attribution_tag(r.get("risk_profile"))
        sym = (r.get("symbol") or "?").upper()
        buckets.setdefault((prof, sym), []).append(r)
        by_symbol_rows.setdefault(sym, []).append(r)
    portfolios = {
        f"{prof}:{sym}": {**_range_stats_for_rows(rs), "profile": prof, "symbol": sym}
        for (prof, sym), rs in buckets.items()
    }
    by_symbol = {s: _range_stats_for_rows(rs) for s, rs in by_symbol_rows.items()}

    _out(
        {
            "ok": True,
            "start": args.start,
            "end": args.end,
            "symbol": args.symbol.upper() if args.symbol else None,
            "portfolios": portfolios,
            "profiles": profiles,
            "by_symbol": by_symbol,
        }
    )


def _rows_dicts(conn: sqlite3.Connection, where: list[str], params: list) -> list[dict]:
    sql = (
        "SELECT trade_date, risk_profile, symbol, pnl, fees, net_credit, status "
        f"FROM ic_trades WHERE {' AND '.join(where)} ORDER BY trade_date, id"
    )
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _ic_open_fee_fallback(symbol: str):
    """Computed per-symbol IC open-fee from the shared tastytrade schedule (cherrypick.core.fees), replacing
    MEIC's hand-maintained fee_estimate_fallback_per_contract constants. Returns None if cherrypick-core
    isn't installed."""
    try:
        from cherrypick.core.fees import ic_open_fee
    except Exception:
        return None
    return ic_open_fee(symbol)


def cmd_get_fee_estimate(args):
    """Estimate $/contract fee drag for a symbol from recent closed trades.

    Used by the fee-adjusted credit floor (Step 6): a fixed pct-of-width
    credit floor can pass a trade whose entire credit gets consumed by fees
    on symbols/wing-widths with high fee-to-premium ratios (e.g. XSP 2026-06-30:
    $4.00 gross credit, $4.96 fees, net -$0.97). Sample size is reported so
    the caller can fall back to the computed `fallback_per_contract` (from the
    shared fee schedule) when the sample is thin.
    """
    symbol = (args.symbol or "").upper()
    lookback = args.lookback or 20
    conn = _connect()
    rows = conn.execute(
        "SELECT fees, quantity FROM ic_trades "
        "WHERE symbol = ? AND status NOT IN ('pending', 'cancelled', 'partial_entry') "
        "AND fees IS NOT NULL AND quantity IS NOT NULL AND quantity > 0 "
        "ORDER BY id DESC LIMIT ?",
        (symbol, lookback),
    ).fetchall()
    conn.close()

    sample_size = len(rows)
    total_fees = sum((r["fees"] or 0) for r in rows)
    total_contracts = sum((r["quantity"] or 0) for r in rows)
    avg_fee_per_contract = round(total_fees / total_contracts, 2) if total_contracts else None

    _out(
        {
            "ok": True,
            "symbol": symbol,
            "sample_size": sample_size,
            "avg_fee_per_contract": avg_fee_per_contract,
            "fallback_per_contract": _ic_open_fee_fallback(symbol),
            "total_fees": round(total_fees, 2),
            "total_contracts": total_contracts,
        }
    )


def cmd_get_step_timing(args):
    """Summarize logged step latency (see `timing_stop_management` / `timing_entry_evaluation`
    rows written by log_loop_action) so entry-evaluation vs stop-management wall-clock cost can
    actually be compared, rather than inferred from the loop_log row timestamps (which cluster
    within milliseconds of each other since rows are written back-to-back at logging time, not
    spread across the work each step does).
    """
    conn = _connect()
    where = ["duration_ms IS NOT NULL"]
    params: list = []
    if args.action:
        where.append("action = ?")
        params.append(args.action)
    if args.symbol:
        where.append("symbol = ?")
        params.append(args.symbol.upper())
    if args.lookback_days:
        where.append("loop_date >= date('now', ?)")
        params.append(f"-{args.lookback_days} days")
    rows = conn.execute(
        f"SELECT action, symbol, duration_ms FROM loop_log WHERE {' AND '.join(where)} ORDER BY id DESC",
        params,
    ).fetchall()
    conn.close()

    by_action: dict[str, list[int]] = {}
    for r in rows:
        by_action.setdefault(r["action"], []).append(r["duration_ms"])

    summary = {}
    for action, durations in by_action.items():
        summary[action] = {
            "sample_size": len(durations),
            "avg_ms": round(sum(durations) / len(durations), 1),
            "min_ms": min(durations),
            "max_ms": max(durations),
        }

    _out({"ok": True, "sample_size": len(rows), "by_action": summary})


# ---------------------------------------------------------------------------
# Write commands
# ---------------------------------------------------------------------------


def cmd_save_trade(args):
    data = json.loads(args.data)
    now = str(_now_et())
    data.setdefault("trade_date", _today_et())
    data.setdefault("created_at", now)
    data["updated_at"] = now
    if "stop_adjustment_history" in data and not isinstance(data["stop_adjustment_history"], str):
        data["stop_adjustment_history"] = json.dumps(data["stop_adjustment_history"])

    cols = list(data.keys())
    placeholders = ", ".join(["?" for _ in cols])
    updates = ", ".join([f"{c} = excluded.{c}" for c in cols if c not in ("ic_order_id", "created_at")])
    sql = (
        f"INSERT INTO ic_trades ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(ic_order_id) DO UPDATE SET {updates}"
    )
    conn = _connect()
    conn.execute(sql, [data[c] for c in cols])
    conn.commit()
    rowid = conn.execute("SELECT id FROM ic_trades WHERE ic_order_id = ?", (data["ic_order_id"],)).fetchone()[
        "id"
    ]
    conn.close()
    _out({"ok": True, "id": rowid})


# Whitelist of columns `update_trade` may write. Defined once and consumed by BOTH the handler and the
# argparse setup below: the two lists were duplicated verbatim, so adding a field to one and not the
# other would have accepted the flag and silently dropped the value (or vice versa).
_UPDATABLE_TRADE_FIELDS = (
    "status",
    "exit_price",
    "exit_time",
    "exit_reason",
    "exit_analysis",
    "put_stop_order_id",
    "call_stop_order_id",
    "put_stop_cost",
    "call_stop_cost",
    "put_spread_entry_order_id",
    "call_spread_entry_order_id",
    "stop_trigger_current",
    "stop_limit_current",
    "pnl",
    "fees",
    "fill_confirmed_at",
    # Stop-rule instrumentation — written on mark-to-market and at settlement, not at entry.
    "put_max_cost",
    "call_max_cost",
    "put_settle_value",
    "call_settle_value",
    "settle_underlying",
    # Feed-quality instrumentation — written when an iteration cannot mark the trade.
    "unmarked_iterations",
    "last_unmarked_at",
    # Cost-sensitivity instrumentation — accumulated on every priced exit.
    "slippage_dollars",
    # Written only on a physically-settled force-close (paper._apply_exit_decision); missing from
    # this whitelist meant every such force-close's update_trade call was rejected by argparse
    # (`unrecognized arguments: --pin_risk_applied`), leaving the row stuck at status='open'
    # forever despite its legs having already been recorded closed.
    "pin_risk_applied",
    # First-touch instrumentation — write-once, the first tick spot reached (crossed toward ITM)
    # each short strike. See paper._first_touch_updates; what a strike-touch stop policy (Phase 3)
    # is computed from after the fact.
    "put_touch_time",
    "put_touch_spot",
    "call_touch_time",
    "call_touch_spot",
)


def cmd_update_trade(args):
    now = str(_now_et())
    fields = {}
    for attr in _UPDATABLE_TRADE_FIELDS:
        val = getattr(args, attr, None)
        if val is not None:
            fields[attr] = val
    if not fields:
        _out({"ok": False, "error": "No fields to update"})
        return
    fields["updated_at"] = now
    set_clause = ", ".join([f"{k} = ?" for k in fields])
    sql = f"UPDATE ic_trades SET {set_clause} WHERE ic_order_id = ?"
    conn = _connect()
    cur = conn.execute(sql, list(fields.values()) + [args.ic_order_id])
    conn.commit()
    conn.close()
    _out({"ok": True, "rows_updated": cur.rowcount})


def cmd_record_stop_adjustment(args):
    """Read-modify-write on stop_adjustment_history/count for one ic_order_id.

    Wrapped in BEGIN IMMEDIATE so the SELECT and UPDATE are atomic against a
    concurrent call for the same trade — without this, two overlapping calls
    could both read the same history, and the second write would silently
    drop the first adjustment.
    """
    now = str(_now_et())
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT stop_adjustment_history, stop_adjustment_count FROM ic_trades WHERE ic_order_id = ?",
            (args.ic_order_id,),
        ).fetchone()
        if not row:
            conn.execute("ROLLBACK")
            _out({"ok": False, "error": f"Trade {args.ic_order_id} not found"})
            return
        history = json.loads(row["stop_adjustment_history"] or "[]")
        history.append(
            {
                "time": now,
                "new_trigger": args.new_trigger,
                "new_limit": args.new_limit,
                "reason": args.reason,
            }
        )
        new_count = (row["stop_adjustment_count"] or 0) + 1
        conn.execute(
            """UPDATE ic_trades
               SET stop_trigger_current = ?,
                   stop_limit_current = ?,
                   stop_adjustment_count = ?,
                   stop_adjustment_history = ?,
                   updated_at = ?
               WHERE ic_order_id = ?""",
            (args.new_trigger, args.new_limit, new_count, json.dumps(history), now, args.ic_order_id),
        )
        conn.commit()
    finally:
        conn.close()
    _out({"ok": True, "stop_adjustment_count": new_count})


def cmd_record_leg_exit(args):
    now = str(_now_et())
    conn = _connect()
    row = conn.execute("SELECT id FROM ic_trades WHERE ic_order_id = ?", (args.ic_order_id,)).fetchone()
    if not row:
        conn.close()
        _out({"ok": False, "error": f"Trade {args.ic_order_id} not found"})
        return
    conn.execute(
        """INSERT INTO ic_spread_legs
               (ic_order_id, side, status, exit_time, exit_reason, exit_price, pnl, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(ic_order_id, side) DO UPDATE SET
               status = excluded.status,
               exit_time = excluded.exit_time,
               exit_reason = excluded.exit_reason,
               exit_price = excluded.exit_price,
               pnl = excluded.pnl,
               updated_at = excluded.updated_at""",
        (
            args.ic_order_id,
            args.side,
            args.status,
            args.exit_time,
            args.exit_reason,
            args.exit_price,
            args.pnl,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    _out({"ok": True})


def cmd_get_spread_legs(args):
    conn = _connect()
    rows = conn.execute("SELECT * FROM ic_spread_legs WHERE ic_order_id = ?", (args.ic_order_id,)).fetchall()
    conn.close()
    _out({"ok": True, "legs": [dict(r) for r in rows]})


def cmd_log_loop_action(args):
    now_et = _now_et()
    now_str = str(now_et)
    today = now_et.strftime("%Y-%m-%d")
    ctx = {}
    if args.market_context and args.market_context != "{}":
        try:
            ctx = json.loads(args.market_context)
        except json.JSONDecodeError:
            pass
    # Flat args override JSON context when provided
    if args.iv_rank is not None:
        ctx["iv_rank"] = args.iv_rank
    if args.session_quality is not None:
        ctx["session_quality"] = args.session_quality
    if args.underlying_price is not None:
        ctx["underlying_price"] = args.underlying_price
    if args.open_trades is not None:
        ctx["open_trades"] = args.open_trades
    if args.today_count is not None:
        ctx["today_count"] = args.today_count
    if args.today_pnl is not None:
        ctx["today_pnl"] = args.today_pnl
    if args.duration_ms is not None:
        ctx["duration_ms"] = args.duration_ms
    conn = _connect()
    conn.execute(
        """INSERT INTO loop_log
           (loop_time, loop_date, symbol, action, reasoning,
            open_trades_n, today_count, today_pnl,
            iv_rank, underlying_price, session_quality,
            mcp_errors, duration_ms, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            now_str,
            today,
            args.symbol,
            args.action,
            args.reasoning,
            ctx.get("open_trades", 0),
            ctx.get("today_count", 0),
            ctx.get("today_pnl", 0),
            ctx.get("iv_rank"),
            ctx.get("underlying_price"),
            ctx.get("session_quality"),
            json.dumps(ctx.get("mcp_errors", [])),
            ctx.get("duration_ms"),
            now_str,
        ),
    )
    conn.commit()
    conn.close()
    _out({"ok": True})


def cmd_get_session_init(_args):
    today = _today_et()
    conn = _connect()
    row = conn.execute(
        "SELECT session_init_at FROM daily_summary WHERE summary_date = ?", (today,)
    ).fetchone()
    conn.close()
    already_run = bool(row and row["session_init_at"])
    _out({"already_run": already_run})


def cmd_set_session_init(_args):
    now = str(_now_et())
    today = _today_et()
    conn = _connect()
    conn.execute(
        """INSERT INTO daily_summary (summary_date, session_init_at, created_at, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(summary_date) DO UPDATE SET
             session_init_at = excluded.session_init_at,
             updated_at = excluded.updated_at""",
        (today, now, now, now),
    )
    conn.commit()
    conn.close()
    _out({"ok": True, "session_init_at": now})


def cmd_save_daily_summary(args):
    now = str(_now_et())
    date = args.date or _today_et()
    if not args.summary and args.closing_nlv is None:
        _out({"ok": False, "error": "Provide --summary and/or --closing_nlv"})
        return
    conn = _connect()
    conn.execute(
        """INSERT INTO daily_summary (summary_date, ai_day_summary, closing_nlv, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(summary_date) DO UPDATE SET
             ai_day_summary = COALESCE(excluded.ai_day_summary, ai_day_summary),
             closing_nlv = COALESCE(excluded.closing_nlv, closing_nlv),
             updated_at = excluded.updated_at""",
        (date, args.summary, args.closing_nlv, now, now),
    )
    conn.commit()
    conn.close()
    _out({"ok": True, "date": date})


_MARKET_CONTEXT_DDL = """
CREATE TABLE IF NOT EXISTS market_context (
    context_date  TEXT PRIMARY KEY,
    vix           REAL,
    vix1d         REAL,
    vix1d_ratio   REAL,
    symbols_json  TEXT DEFAULT '{}',
    updated_at    TEXT NOT NULL
)"""


def cmd_save_market_context(args):
    """Upsert one per-day market-context snapshot (VIX / VIX1D / per-symbol price+IV rank) for the
    EOD analysis report. Called once per paper-loop iteration; the last write of the session wins,
    landing closest to the close. Creates the table on demand so it works on paper DBs that predate
    this schema addition without re-running init_db."""
    now = str(_now_et())
    date = args.date or _today_et()
    try:
        json.loads(args.symbols)  # validate; stored verbatim
    except (TypeError, ValueError):
        args.symbols = "{}"
    conn = _connect()
    conn.execute(_MARKET_CONTEXT_DDL)
    conn.execute(
        """INSERT INTO market_context (context_date, vix, vix1d, vix1d_ratio, symbols_json, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(context_date) DO UPDATE SET
             vix = excluded.vix,
             vix1d = excluded.vix1d,
             vix1d_ratio = excluded.vix1d_ratio,
             symbols_json = excluded.symbols_json,
             updated_at = excluded.updated_at""",
        (date, args.vix, args.vix1d, args.vix1d_ratio, args.symbols, now),
    )
    conn.commit()
    conn.close()
    _out({"ok": True, "date": date})


# ---------------------------------------------------------------------------
# CLI dispatch
# ---------------------------------------------------------------------------

# Single source of truth for command name -> handler, shared by main()'s CLI dispatch and call()'s
# in-process dispatch below. Was two separately-maintained dicts (this one didn't exist; main() had
# its own inline literal) -- the same failure class _UPDATABLE_TRADE_FIELDS's comment already warns
# about elsewhere in this file: a command added to one and not the other silently 404s from the path
# that was missed.
_COMMANDS = {
    "init_db": cmd_init_db,
    "get_open_trades": cmd_get_open_trades,
    "get_unsettled_stopped_trades": cmd_get_unsettled_stopped_trades,
    "get_today_count": cmd_get_today_count,
    "get_today_pnl": cmd_get_today_pnl,
    "get_eod_summary": cmd_get_eod_summary,
    "get_session_init": cmd_get_session_init,
    "set_session_init": cmd_set_session_init,
    "get_range_summary": cmd_get_range_summary,
    "get_fee_estimate": cmd_get_fee_estimate,
    "get_step_timing": cmd_get_step_timing,
    "save_trade": cmd_save_trade,
    "update_trade": cmd_update_trade,
    "save_daily_summary": cmd_save_daily_summary,
    "save_market_context": cmd_save_market_context,
    "record_stop_adjustment": cmd_record_stop_adjustment,
    "log_loop_action": cmd_log_loop_action,
    "record_leg_exit": cmd_record_leg_exit,
    "get_spread_legs": cmd_get_spread_legs,
}


def call(command: str, db_path: str, **kwargs) -> dict:
    """In-process entry point for another module in this process (paper.py, and live_loop.py via
    paper.py) to invoke a db.py command without spawning a subprocess.

    Every cmd_* handler was written for CLI use: it reads its inputs off an argparse.Namespace and
    prints its result via the module-level _out(). This bypasses argparse (kwargs go straight onto
    a Namespace) and captures _out()'s payload instead of letting it print -- callers get back
    exactly the dict a CLI invocation's stdout would have parsed to. kwargs values are expected to
    be strings, mirroring what argparse would hand a handler from sys.argv (none of the commands
    reachable from paper.py declare a non-string `type=`, so this is a lossless replacement of the
    subprocess boundary, not an approximation of it).

    _DB_PATH is a module global (set once from --db / MEIC_DB_PATH at CLI startup); every cmd_*
    reads it indirectly via _connect(). Save/restore around the call so a caller can pass whichever
    db_path it needs without disturbing any other in-process caller -- safe because this module is
    only ever driven single-threaded (the paper loop's one iteration at a time; concurrent callers
    from separate processes each get their own copy of this global, as today).
    """
    fn = _COMMANDS.get(command)
    if fn is None:
        return {"ok": False, "error": f"unknown command {command!r}"}
    global _DB_PATH, _out
    prev_path, prev_out = _DB_PATH, _out
    captured: dict = {}

    def _capture(data):
        captured["result"] = data

    _DB_PATH = db_path
    _out = _capture
    try:
        fn(argparse.Namespace(**kwargs))
    except Exception as exc:  # a CLI invocation's non-zero exit -> {"ok": False, "error": ...}
        return {"ok": False, "error": str(exc)}
    finally:
        _DB_PATH = prev_path
        _out = prev_out
    return captured.get("result", {"ok": False, "error": f"{command!r} produced no output"})


def main():
    global _DB_PATH
    parser = argparse.ArgumentParser(description="MEICAgent DB helper")
    parser.add_argument(
        "--db",
        default=None,
        help="Override the database path (defaults to MEIC_DB_PATH env var, "
        "then the data home's meic_trades.db). Used by the paper-trading "
        "engine to point at paper_trades.db.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init_db")
    p_open = sub.add_parser("get_open_trades")
    p_open.add_argument("--symbol", default=None, help="Filter to one symbol; omit for all symbols")
    p_open.add_argument(
        "--date",
        default=None,
        help="Override trade_date to query (YYYY-MM-DD); defaults to the real "
        "system today. Used by paper-trading replay to query a historical day.",
    )
    p_unsettled = sub.add_parser("get_unsettled_stopped_trades")
    p_unsettled.add_argument("--symbol", default=None, help="Filter to one symbol; omit for all symbols")
    p_unsettled.add_argument(
        "--date",
        default=None,
        help="Override trade_date to query (YYYY-MM-DD); defaults to the real system today.",
    )
    p_cnt = sub.add_parser("get_today_count")
    p_cnt.add_argument("--symbol", default=None, help="Filter to one symbol; omit for all symbols")
    p_pnl = sub.add_parser("get_today_pnl")
    p_pnl.add_argument("--symbol", default=None, help="Filter to one symbol; omit for all symbols")
    sub.add_parser("get_eod_summary")
    sub.add_parser("get_session_init")
    sub.add_parser("set_session_init")

    p_range = sub.add_parser("get_range_summary")
    p_range.add_argument("--start", required=True, help="Inclusive start date, YYYY-MM-DD")
    p_range.add_argument("--end", required=True, help="Inclusive end date, YYYY-MM-DD")
    p_range.add_argument(
        "--profile", default=None, help="Filter to one risk_profile; omit to group by every profile present"
    )
    p_range.add_argument("--symbol", default=None, help="Filter to one symbol; omit for all symbols")

    p_fee = sub.add_parser("get_fee_estimate")
    p_fee.add_argument("--symbol", required=True)
    p_fee.add_argument("--lookback", default=20, type=int)

    p_timing = sub.add_parser("get_step_timing")
    p_timing.add_argument(
        "--action",
        default=None,
        help="Filter to one action, e.g. timing_stop_management or timing_entry_evaluation",
    )
    p_timing.add_argument("--symbol", default=None)
    p_timing.add_argument("--lookback_days", default=None, type=int)

    p_save = sub.add_parser("save_trade")
    p_save.add_argument("--data", required=True)

    p_upd = sub.add_parser("update_trade")
    p_upd.add_argument("--ic_order_id", required=True)
    for f in _UPDATABLE_TRADE_FIELDS:
        p_upd.add_argument(f"--{f}", default=None)

    p_adj = sub.add_parser("record_stop_adjustment")
    p_adj.add_argument("--ic_order_id", required=True)
    p_adj.add_argument("--new_trigger", required=True, type=float)
    p_adj.add_argument("--new_limit", required=True, type=float)
    p_adj.add_argument("--reason", required=True)

    p_leg = sub.add_parser("record_leg_exit")
    p_leg.add_argument("--ic_order_id", required=True)
    p_leg.add_argument("--side", required=True, choices=["put", "call"])
    p_leg.add_argument("--status", required=True)
    p_leg.add_argument("--exit_time", default=None)
    p_leg.add_argument("--exit_reason", default=None)
    p_leg.add_argument("--exit_price", default=None, type=float)
    p_leg.add_argument("--pnl", default=None, type=float)

    p_getlegs = sub.add_parser("get_spread_legs")
    p_getlegs.add_argument("--ic_order_id", required=True)

    p_dsum = sub.add_parser("save_daily_summary")
    p_dsum.add_argument("--date", default=None)
    p_dsum.add_argument("--summary", default=None)
    p_dsum.add_argument("--closing_nlv", default=None, type=float)

    p_mctx = sub.add_parser("save_market_context")
    p_mctx.add_argument("--date", default=None)
    p_mctx.add_argument("--vix", default=None, type=float)
    p_mctx.add_argument("--vix1d", default=None, type=float)
    p_mctx.add_argument("--vix1d_ratio", default=None, type=float)
    p_mctx.add_argument(
        "--symbols", default="{}", help="JSON: {SYM: {price, iv_rank}} snapshot for the EOD analysis report"
    )

    p_log = sub.add_parser("log_loop_action")
    p_log.add_argument(
        "--symbol",
        default=None,
        help="Symbol this log row is for; omit for an iteration-level summary row spanning all symbols",
    )
    p_log.add_argument("--action", required=True)
    p_log.add_argument("--reasoning", default="")
    p_log.add_argument("--market_context", default="{}")
    p_log.add_argument("--iv_rank", default=None, type=float)
    p_log.add_argument("--session_quality", default=None)
    p_log.add_argument("--underlying_price", default=None, type=float)
    p_log.add_argument("--open_trades", default=None, type=int)
    p_log.add_argument("--today_count", default=None, type=int)
    p_log.add_argument("--today_pnl", default=None, type=float)
    p_log.add_argument(
        "--duration_ms",
        default=None,
        type=int,
        help="Elapsed wall-clock milliseconds for the step this row represents (e.g. stop management or one symbol's entry evaluation)",
    )

    args = parser.parse_args()

    if args.db:
        _DB_PATH = args.db
    elif "MEIC_DB_PATH" in os.environ:
        _DB_PATH = os.environ["MEIC_DB_PATH"]

    fn = _COMMANDS.get(args.command)
    if fn is None:
        parser.print_help()
        sys.exit(1)
    fn(args)


if __name__ == "__main__":
    main()
