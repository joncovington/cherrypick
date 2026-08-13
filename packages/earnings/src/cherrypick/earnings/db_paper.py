"""SQLite persistence for EarningsAgent's PAPER TRADING simulation.

A deliberately separate database (data/paper_trades.db) and separate CLI
from db.py/earnings_trades.db -- paper and real trade data must never be
queryable through the same connection or file, so there is no --paper
flag on db.py and no shared code path that could blend the two.

Schema is strategy-agnostic (see db.py's own docstring for the rationale):
`trades.strategy` identifies which strategy opened a position, and
`legs_json` holds that strategy's actual order legs verbatim.

Commands:
  init_db
  get_open_positions
  save_trade --data '{"order_id": "...", "strategy": "iron_fly", "symbol": "...",
      "expiration": "YYYY-MM-DD", "short_strike": F, "long_call_strike": F,
      "long_put_strike": F, "legs_json": "...", "entry_credit": F,
      "profile": "balanced", "quantity": N, "capital_at_risk": F, "entry_cost": F,
      "entry_context": {...}}'
  save_close --data '{"order_id": "...", "exit_debit": F, "pnl": F, "exit_cost": F,
      "exit_reason": "profit_target"}'
  get_open_legs --order_id X
  save_leg_close --data '{"order_id": "...", "leg_role": "...", "close_price": F}'
  log_scan --data '{"scan_date": "YYYY-MM-DD", "symbol": "...", "strategy": "iron_fly",
      "tier": "...", "outcome": "...", "reason": "...", "profile": "balanced"}'
  get_pnl_summary [--strategy X] [--profile X]
  record_mark / record_management_event / record_iteration --data '{...}'
  set_open_legs --data '{"order_id": "...", "streamer_symbols": [...]}' / clear_open_legs --order_id X
  record_measurement_break --data '{...}' / get_measurement_breaks
  get_marks / get_management_events [--order_id X] [--session_date D] [--limit N]
  get_iterations [--session_date D] [--limit N]

Positions are MANAGED between entry and exit rather than force-closed the next morning, so the
schema keeps the path and not just the endpoints: `position_marks` (what it was worth each tick,
and whether the quotes were good enough to act on), `management_events` (every verdict, including
the ones an execution gate held back), and `loop_iterations` (the loop's own vital signs). On
`trades`, `status`/`exit_reason`/`hold_days` and the excursion columns record how a position ended
and how long it took. `open_leg_symbols` is the flat set the market-data producer subscribes from,
and `measurement_breaks` records the dates across which results must never be pooled.

`legs` (optional array on save_trade, each `{leg_role, symbol, action, quantity}`) is for
strategies with independently-closeable legs (e.g. double_calendar's threatened-side close)
-- iron fly never passes it, so it never gets `trade_legs` rows. A trade's `trades.closed_at`
stays NULL until every one of its legs is closed via save_leg_close and save_close is called
for the position as a whole.

`profile` (defaults to 'default') tags which named risk profile / test book opened a trade
or produced a scan_log row (see docs/strat-test-portfolios.md) -- lets many isolated books
share this one file without ever mixing their P&L or candidate history. `quantity` and
`capital_at_risk` come from sizing.compute_position_size; `entry_cost`/`exit_cost` come from
costs.py's tastytrade fee+slippage model (kept separate from entry_credit/exit_debit/pnl so
cost impact is analyzable on its own). `entry_context` is a small JSON blob of the market
conditions at entry (iv_rv_ratio, dispersion, skew, winrate_sample_size) for regime slicing
in strategy_metrics.py -- stored verbatim, never parsed by db_paper.py itself.
"""

import argparse
import json
import sqlite3
import sys
import time
from datetime import date as _date

# Make `import paths` resolve when this file is imported (not run as the __main__ script, which
# gets its own directory on sys.path automatically) -- mirrors credentials.py's self-insert.
from cherrypick.core import calendar as _calendar
from cherrypick.core import db as _db
from cherrypick.core import profiles as _profiles

from cherrypick.earnings import paths as _paths

# A hold this long is a bug, not a trade; the span walk stops rather than spinning on a timestamp
# that parsed but means nothing.
_MAX_SESSION_SPAN = 400

# Failed close attempts before a position is called `stranded`. Two, not one: a single missed
# sweep is a slow open or a halted name, both of which usually clear by the next attempt.
STRANDED_AFTER_ATTEMPTS = 2

# Resolved from the shared cherrypick data home (~/.cherrypick/data/earnings by default, or
# EARNINGS_DATA_DIR) so this checkout and the orchestrator read/write the same paper book. See paths.py.
DB_PATH = _paths.paper_db_path()

_DDL = """
CREATE TABLE IF NOT EXISTS trades (
    order_id        TEXT PRIMARY KEY,
    strategy        TEXT NOT NULL DEFAULT 'iron_fly',
    symbol          TEXT NOT NULL,
    expiration      TEXT NOT NULL,
    short_strike    REAL,
    long_call_strike REAL,
    long_put_strike REAL,
    legs_json       TEXT,
    entry_credit    REAL,
    exit_debit      REAL,
    pnl             REAL,
    opened_at       REAL,
    closed_at       REAL,
    profile         TEXT NOT NULL DEFAULT 'default',
    quantity        INTEGER,
    capital_at_risk REAL,
    entry_cost      REAL,
    exit_cost       REAL,
    entry_context   TEXT,
    entry_iv        REAL,
    exit_iv         REAL,
    status          TEXT NOT NULL DEFAULT 'open',
    exit_reason     TEXT,
    hold_days       INTEGER,
    max_unrealized_pnl REAL,
    min_unrealized_pnl REAL
);

CREATE TABLE IF NOT EXISTS trade_legs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    TEXT NOT NULL,
    leg_role    TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    action      TEXT NOT NULL,
    quantity    INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    close_price REAL,
    closed_at   REAL,
    UNIQUE(order_id, leg_role)
);

CREATE TABLE IF NOT EXISTS scan_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_date      TEXT NOT NULL,
    strategy       TEXT NOT NULL DEFAULT 'iron_fly',
    symbol         TEXT NOT NULL,
    tier           TEXT,
    outcome        TEXT,
    reason         TEXT,
    stage          TEXT NOT NULL DEFAULT 'screen',
    reject_details TEXT,
    logged_at      REAL,
    profile        TEXT NOT NULL DEFAULT 'default'
);

CREATE TABLE IF NOT EXISTS daily_summary (
    summary_date    TEXT PRIMARY KEY,
    positions_opened INTEGER,
    positions_closed INTEGER,
    net_pnl        REAL
);

-- One row per position per monitoring tick: what the position was worth, on what quotes, and
-- whether those quotes were good enough to act on. Stored per tick on purpose. MEIC's schema
-- deliberately kept no intraday marks and its own DDL records the regret -- with only entry and
-- exit on file, nothing about the path between them was measurable after the fact, so "would a
-- tighter target have caught this?" had no answer. `usable` is the mark's own verdict on itself:
-- a refused mark is still written (with `refusal`), because a stalled feed and a quiet market must
-- not look identical in the record.
CREATE TABLE IF NOT EXISTS position_marks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        TEXT NOT NULL,
    marked_at       REAL NOT NULL,
    session_date    TEXT NOT NULL,
    exit_debit      REAL,
    unrealized_pnl  REAL,
    spot            REAL,
    source          TEXT,
    quotes_fresh    INTEGER,
    quotes_stale    INTEGER,
    max_leg_spread_pct REAL,
    usable          INTEGER NOT NULL DEFAULT 0,
    refusal         TEXT
);
CREATE INDEX IF NOT EXISTS idx_position_marks_order ON position_marks(order_id, marked_at);
CREATE INDEX IF NOT EXISTS idx_position_marks_session ON position_marks(session_date);

-- Every management verdict, including the ones that did NOT act. A decision blocked by an
-- execution gate (before the execution window, spread too wide to trust, quotes unusable) is
-- recorded with executed=0 and the gate that held it, so "the target was hit at 09:33 and taken
-- at 09:41" is legible rather than looking like a late exit for no reason.
CREATE TABLE IF NOT EXISTS management_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id     TEXT NOT NULL,
    occurred_at  REAL NOT NULL,
    session_date TEXT NOT NULL,
    phase        TEXT,
    action       TEXT NOT NULL,
    reason       TEXT NOT NULL,
    executed     INTEGER NOT NULL DEFAULT 0,
    gate         TEXT,
    detail_json  TEXT,
    mark_id      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_management_events_order ON management_events(order_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_management_events_session ON management_events(session_date);

-- One row per loop tick that did something (in-session only). This is what makes "the loop is
-- alive but the market is quiet" distinguishable from "the loop is dead" without reading logs --
-- the same job flies' fly_snapshots does for its feed. `status` is 'ok' or the refusal reason.
CREATE TABLE IF NOT EXISTS loop_iterations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at         REAL NOT NULL,
    session_date   TEXT NOT NULL,
    phase          TEXT NOT NULL,
    status         TEXT NOT NULL,
    open_positions INTEGER,
    marks_written  INTEGER,
    actions_taken  INTEGER,
    quotes_fresh   INTEGER,
    quotes_stale   INTEGER,
    open_capital   REAL,
    duration_ms    INTEGER,
    note           TEXT
);
CREATE INDEX IF NOT EXISTS idx_loop_iterations_session ON loop_iterations(session_date, ran_at);

-- The streamer symbols of every open position's legs, flat. The streamer's `leg_sources` runs a
-- plain single SELECT against this (see cherrypick.core.streamrequests) -- legs_json holds the
-- same symbols, but reaching them needs JSON extraction whose availability varies by SQLite build,
-- and the producer must never depend on that.
CREATE TABLE IF NOT EXISTS open_leg_symbols (
    order_id        TEXT NOT NULL,
    streamer_symbol TEXT NOT NULL,
    PRIMARY KEY (order_id, streamer_symbol)
);

-- Dates on which the way results are produced changed, so no report ever silently pools results
-- from either side of one. Same discipline as the flies tick-cadence change: the break is a row,
-- not a memory.
CREATE TABLE IF NOT EXISTS measurement_breaks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    break_date  TEXT NOT NULL,
    key         TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    note        TEXT,
    recorded_at REAL,
    UNIQUE(break_date, key)
);

CREATE TABLE IF NOT EXISTS market_context (
    context_date  TEXT PRIMARY KEY,
    vix           REAL,
    vix1d         REAL,
    updated_at    REAL
);

CREATE TABLE IF NOT EXISTS entry_reviews (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_date      TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    timing         TEXT,
    timing_assumed INTEGER,
    strategy       TEXT,
    price          REAL,
    volume         REAL,
    winrate        REAL,
    winrate_sample INTEGER,
    iv_rv_ratio    REAL,
    iv_rv_source   TEXT,
    term_structure REAL,
    market_cap     REAL,
    expected_move  REAL,
    expected_move_pct       REAL,
    combined_open_interest  REAL,
    combined_option_volume  REAL,
    bid_ask_spread_pct      REAL,
    net_combo_spread_pct    REAL,
    avg_actual_move_pct     REAL,
    move_dispersion_pct     REAL,
    max_actual_move_pct     REAL,
    implied_vs_avg_actual   REAL,
    move_tail_veto INTEGER,
    iv_rank        REAL,
    iv_percentile  REAL,
    composite_score REAL,
    best_tier      TEXT,
    selected       INTEGER NOT NULL DEFAULT 0,
    reason         TEXT,
    criteria_json  TEXT,
    logged_at      REAL,
    profile        TEXT NOT NULL DEFAULT 'default',
    UNIQUE(scan_date, symbol, profile)
);
"""

# Idempotent migration for databases created before profile/sizing/cost attribution
# existed (CREATE TABLE IF NOT EXISTS is a no-op on an already-existing table, so new
# columns never appear there without this). Each entry: (table, column, ADD COLUMN clause).
_MIGRATIONS = [
    ("trades", "profile", "ALTER TABLE trades ADD COLUMN profile TEXT NOT NULL DEFAULT 'default'"),
    ("trades", "quantity", "ALTER TABLE trades ADD COLUMN quantity INTEGER"),
    ("trades", "capital_at_risk", "ALTER TABLE trades ADD COLUMN capital_at_risk REAL"),
    ("trades", "entry_cost", "ALTER TABLE trades ADD COLUMN entry_cost REAL"),
    ("trades", "exit_cost", "ALTER TABLE trades ADD COLUMN exit_cost REAL"),
    ("trades", "entry_context", "ALTER TABLE trades ADD COLUMN entry_context TEXT"),
    ("trades", "entry_iv", "ALTER TABLE trades ADD COLUMN entry_iv REAL"),
    ("trades", "exit_iv", "ALTER TABLE trades ADD COLUMN exit_iv REAL"),
    # Stranded-close accounting: a position whose legs can't be quoted at the close sweep
    # must accumulate visible failed attempts, not silently stay open forever.
    ("trades", "close_attempts", "ALTER TABLE trades ADD COLUMN close_attempts INTEGER NOT NULL DEFAULT 0"),
    ("trades", "last_close_error", "ALTER TABLE trades ADD COLUMN last_close_error TEXT"),
    ("trades", "last_close_attempt_at", "ALTER TABLE trades ADD COLUMN last_close_attempt_at REAL"),
    # Cost-sensitivity: the slippage component of entry_cost/exit_cost, stored separately.
    # Slippage is linear in slippage_frac_of_spread, so net P&L at a stressed 2x fraction
    # = net - (entry_slippage + exit_slippage) exactly.
    ("trades", "entry_slippage", "ALTER TABLE trades ADD COLUMN entry_slippage REAL"),
    ("trades", "exit_slippage", "ALTER TABLE trades ADD COLUMN exit_slippage REAL"),
    ("scan_log", "profile", "ALTER TABLE scan_log ADD COLUMN profile TEXT NOT NULL DEFAULT 'default'"),
    # A candidate's life has two stages and only the first was ever recorded: one that cleared the
    # screen and then died in order building, sizing, the risk cap or a missing quote left no trace.
    # 'screen' rows carry the accept/reject verdict, 'execution' rows what happened after it.
    # Defaulting historical rows to 'screen' is accurate -- that is the only stage that existed.
    ("scan_log", "stage", "ALTER TABLE scan_log ADD COLUMN stage TEXT NOT NULL DEFAULT 'screen'"),
    # The measured value and the threshold it missed, per reason (scanner.explain_reject_reasons).
    # A reason name alone says a gate fired; it cannot say whether the name was one basis point or
    # two orders of magnitude away, which is the only thing that tells you a threshold is mistuned.
    ("scan_log", "reject_details", "ALTER TABLE scan_log ADD COLUMN reject_details TEXT"),
    # Extended entry_reviews columns (research-backed screening metrics: implied-vs-historical
    # move, bid-ask spread quality, IV rank/percentile, move-history tail flag) added for an
    # entry_reviews table that pre-existed these columns -- see docs/screening-criteria.md.
    ("entry_reviews", "strategy", "ALTER TABLE entry_reviews ADD COLUMN strategy TEXT"),
    ("entry_reviews", "iv_rv_source", "ALTER TABLE entry_reviews ADD COLUMN iv_rv_source TEXT"),
    ("entry_reviews", "expected_move_pct", "ALTER TABLE entry_reviews ADD COLUMN expected_move_pct REAL"),
    (
        "entry_reviews",
        "combined_open_interest",
        "ALTER TABLE entry_reviews ADD COLUMN combined_open_interest REAL",
    ),
    (
        "entry_reviews",
        "combined_option_volume",
        "ALTER TABLE entry_reviews ADD COLUMN combined_option_volume REAL",
    ),
    (
        "entry_reviews",
        "bid_ask_spread_pct",
        "ALTER TABLE entry_reviews ADD COLUMN bid_ask_spread_pct REAL",
    ),
    (
        "entry_reviews",
        "net_combo_spread_pct",
        "ALTER TABLE entry_reviews ADD COLUMN net_combo_spread_pct REAL",
    ),
    (
        "entry_reviews",
        "avg_actual_move_pct",
        "ALTER TABLE entry_reviews ADD COLUMN avg_actual_move_pct REAL",
    ),
    (
        "entry_reviews",
        "move_dispersion_pct",
        "ALTER TABLE entry_reviews ADD COLUMN move_dispersion_pct REAL",
    ),
    (
        "entry_reviews",
        "max_actual_move_pct",
        "ALTER TABLE entry_reviews ADD COLUMN max_actual_move_pct REAL",
    ),
    (
        "entry_reviews",
        "implied_vs_avg_actual",
        "ALTER TABLE entry_reviews ADD COLUMN implied_vs_avg_actual REAL",
    ),
    ("entry_reviews", "move_tail_veto", "ALTER TABLE entry_reviews ADD COLUMN move_tail_veto INTEGER"),
    ("entry_reviews", "iv_rank", "ALTER TABLE entry_reviews ADD COLUMN iv_rank REAL"),
    ("entry_reviews", "iv_percentile", "ALTER TABLE entry_reviews ADD COLUMN iv_percentile REAL"),
    ("entry_reviews", "composite_score", "ALTER TABLE entry_reviews ADD COLUMN composite_score REAL"),
    # Nullable on purpose, and NOT backfilled: rows written before the entry calendar started
    # admitting unannotated `when` values genuinely had a calendar-stated timing, but so did most
    # rows after it. NULL here means "this row predates the distinction", which is the truth --
    # defaulting it to 0 would assert every historical row's timing was calendar-sourced.
    ("entry_reviews", "timing_assumed", "ALTER TABLE entry_reviews ADD COLUMN timing_assumed INTEGER"),
    # Position lifecycle: a position is now MANAGED between entry and exit rather than force-closed
    # the next morning, so how it ended and how long it was held are facts worth keeping. `status`
    # is deliberately nullable here (the fresh-DB DDL defaults it to 'open') so the backfill below
    # can tell "never set" from "set to open" -- adding it NOT NULL DEFAULT 'open' would have
    # relabelled every historical closed trade as open, invisibly.
    ("trades", "status", "ALTER TABLE trades ADD COLUMN status TEXT"),
    ("trades", "exit_reason", "ALTER TABLE trades ADD COLUMN exit_reason TEXT"),
    ("trades", "hold_days", "ALTER TABLE trades ADD COLUMN hold_days INTEGER"),
    ("trades", "max_unrealized_pnl", "ALTER TABLE trades ADD COLUMN max_unrealized_pnl REAL"),
    ("trades", "min_unrealized_pnl", "ALTER TABLE trades ADD COLUMN min_unrealized_pnl REAL"),
]

# Backfills that run once, when their column is first added. Keyed by "table.column" so a fresh
# database (whose DDL already has the column) never runs them, and a migrated one runs them exactly
# once -- `apply_additive_migrations` reports what it actually added.
_BACKFILLS = {
    # closed_at stays the authority on open-vs-closed; status mirrors it in the same statement that
    # sets it, and additionally carries 'stranded', which was previously derived per-run inside the
    # close sweep and so never survived the run that noticed it.
    "trades.status": (
        "UPDATE trades SET status = CASE "
        "  WHEN closed_at IS NOT NULL THEN 'closed' "
        "  WHEN COALESCE(close_attempts, 0) >= 2 THEN 'stranded' "
        "  ELSE 'open' END "
        "WHERE status IS NULL"
    ),
    # Every historical exit was the unconditional next-morning sweep. Naming that rather than
    # leaving it NULL keeps pre-lifecycle exits distinguishable from a managed exit whose reason
    # simply failed to record.
    "trades.exit_reason": (
        "UPDATE trades SET exit_reason = 'legacy_next_morning' "
        "WHERE exit_reason IS NULL AND closed_at IS NOT NULL"
    ),
}


def session_span(opened_at, until) -> int | None:
    """Trading sessions a position has been held across: 0 = same session, 1 = the standard
    overnight earnings hold, 2+ = carried.

    Counted in TRADING days, never calendar days, because the hold budget is spent in sessions: a
    Friday entry still open on Monday has been held one session, not three, and a weekend must not
    consume two thirds of a three-session cap. Returns None when either timestamp is unusable,
    which callers must treat as "unknown", never as zero.
    """
    try:
        start = _date.fromtimestamp(float(opened_at))
        end = _date.fromtimestamp(float(until))
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    if end <= start:
        return 0
    sessions, cursor = 0, start
    while cursor < end and sessions < _MAX_SESSION_SPAN:
        cursor = _calendar.next_trading_day(cursor)
        sessions += 1
    return sessions


def _migrate(conn: sqlite3.Connection) -> None:
    added = _db.apply_additive_migrations(conn, _MIGRATIONS)
    for column in added:
        backfill = _BACKFILLS.get(column)
        if backfill:
            conn.execute(backfill)
    if added:
        conn.commit()


def _conn() -> sqlite3.Connection:
    conn = _db.connect(DB_PATH)  # mkdir parent + row_factory=Row (see cherrypick.core.db)
    conn.executescript(_DDL)
    _migrate(conn)
    return conn


def cmd_init_db(args) -> dict:
    conn = _conn()
    conn.executescript(_DDL)
    conn.commit()
    conn.close()
    return {"ok": True, "db_path": str(DB_PATH)}


def cmd_get_open_positions(args) -> dict:
    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM trades WHERE closed_at IS NULL ORDER BY opened_at").fetchall()
    finally:
        conn.close()
    return {"ok": True, "positions": [dict(r) for r in rows]}


def cmd_save_trade(args) -> dict:
    spec = json.loads(args.data)
    required = ("order_id", "symbol", "expiration")
    missing = [k for k in required if not spec.get(k)]
    if missing:
        return {"ok": False, "error": f"missing required field(s): {', '.join(missing)}"}

    entry_context = spec.get("entry_context")
    conn = _conn()
    try:
        # `status` is written explicitly rather than left to the column default: on a database
        # migrated from before the lifecycle columns existed, ALTER added it nullable (see
        # _MIGRATIONS), so an INSERT that omitted it would leave new trades with a NULL status.
        conn.execute(
            "INSERT INTO trades "
            "(order_id, strategy, symbol, expiration, short_strike, long_call_strike, "
            " long_put_strike, legs_json, entry_credit, opened_at, profile, quantity, "
            " capital_at_risk, entry_cost, entry_slippage, entry_context, entry_iv, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')",
            (
                spec["order_id"],
                spec.get("strategy", "iron_fly"),
                spec["symbol"],
                spec["expiration"],
                spec.get("short_strike"),
                spec.get("long_call_strike"),
                spec.get("long_put_strike"),
                spec.get("legs_json"),
                spec.get("entry_credit"),
                spec.get("opened_at", time.time()),
                spec.get("profile", "default"),
                spec.get("quantity"),
                spec.get("capital_at_risk"),
                spec.get("entry_cost"),
                spec.get("entry_slippage"),
                json.dumps(entry_context) if entry_context is not None else None,
                spec.get("entry_iv"),
            ),
        )
        for leg in spec.get("legs", []):
            conn.execute(
                "INSERT INTO trade_legs (order_id, leg_role, symbol, action, quantity) "
                "VALUES (?, ?, ?, ?, ?)",
                (spec["order_id"], leg["leg_role"], leg["symbol"], leg["action"], leg["quantity"]),
            )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        return {"ok": False, "error": f"save_trade failed: {exc}"}
    finally:
        conn.close()
    return {"ok": True, "order_id": spec["order_id"]}


def cmd_get_open_legs(args) -> dict:
    order_id = args.order_id
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM trade_legs WHERE order_id = ? AND status = 'open' ORDER BY leg_role",
            (order_id,),
        ).fetchall()
    finally:
        conn.close()
    return {"ok": True, "order_id": order_id, "legs": [dict(r) for r in rows]}


def cmd_save_leg_close(args) -> dict:
    spec = json.loads(args.data)
    required = ("order_id", "leg_role")
    missing = [k for k in required if not spec.get(k)]
    if missing:
        return {"ok": False, "error": f"missing required field(s): {', '.join(missing)}"}

    conn = _conn()
    try:
        cur = conn.execute(
            "UPDATE trade_legs SET status = 'closed', close_price = ?, closed_at = ? "
            "WHERE order_id = ? AND leg_role = ? AND status = 'open'",
            (
                spec.get("close_price"),
                spec.get("closed_at", time.time()),
                spec["order_id"],
                spec["leg_role"],
            ),
        )
        conn.commit()
        if cur.rowcount == 0:
            return {
                "ok": False,
                "error": f"no open leg found for order_id={spec['order_id']} leg_role={spec['leg_role']}",
            }
    finally:
        conn.close()
    return {"ok": True, "order_id": spec["order_id"], "leg_role": spec["leg_role"]}


def cmd_save_close(args) -> dict:
    """Close a position. `exit_reason` names WHY from the lifecycle taxonomy (profit_target,
    stop_loss, wing_breach, side_breach, leg_delta_stop, time_stop, pin_risk, front_expiry,
    iv_crush_backstop, close_window, forced_eod, manual) -- previously the reason reached only
    scan_log, joinable back to a trade by nothing better than (date, symbol, strategy), which a
    position held across several sessions cannot be identified by at all.

    `status` and `closed_at` are written in the same statement, so the mirror can never drift from
    the fact it mirrors. `hold_days` is derived here rather than stored by the caller, so every
    close counts it the same way.
    """
    spec = json.loads(args.data)
    order_id = spec.get("order_id")
    if not order_id:
        return {"ok": False, "error": "missing required field: order_id"}

    closed_at = spec.get("closed_at", time.time())
    conn = _conn()
    try:
        opened = conn.execute("SELECT opened_at FROM trades WHERE order_id = ?", (order_id,)).fetchone()
        hold_days = spec.get("hold_days")
        if hold_days is None and opened and opened["opened_at"] is not None:
            hold_days = session_span(opened["opened_at"], closed_at)
        cur = conn.execute(
            "UPDATE trades SET exit_debit = ?, pnl = ?, closed_at = ?, exit_cost = ?, "
            "exit_slippage = ?, exit_iv = ?, status = 'closed', exit_reason = ?, hold_days = ? "
            "WHERE order_id = ?",
            (
                spec.get("exit_debit"),
                spec.get("pnl"),
                closed_at,
                spec.get("exit_cost"),
                spec.get("exit_slippage"),
                spec.get("exit_iv"),
                spec.get("exit_reason"),
                hold_days,
                order_id,
            ),
        )
        # A full-position close closes every leg by definition: sweep any trade_legs rows
        # still open so get_open_legs never reports legs of a closed position. Legs closed
        # individually beforehand (the agent path's close_side) keep their own close_price;
        # swept legs record none -- the position-level exit_debit is the priced exit.
        conn.execute(
            "UPDATE trade_legs SET status = 'closed', closed_at = ? WHERE order_id = ? AND status = 'open'",
            (spec.get("closed_at", time.time()), order_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return {"ok": False, "error": f"no open trade found for order_id {order_id}"}
    finally:
        conn.close()
    return {"ok": True, "order_id": order_id}


def cmd_record_close_failure(args) -> dict:
    """One failed close attempt for an open position: bump close_attempts and record why.
    The close sweep calls this on every skip so a position that can't be quoted shows up
    with a growing attempt count in the EOD report and the exit heartbeat, instead of
    silently staying open and vanishing from every closed-trade metric.

    At the second attempt the position becomes `stranded`. That threshold was previously applied
    inside a single sweep, so a position that missed one sweep each day looked fresh every morning
    -- the state never outlived the run that noticed it. It is a status now, so it does.
    """
    spec = json.loads(args.data)
    order_id = spec.get("order_id")
    if not order_id:
        return {"ok": False, "error": "missing required field: order_id"}
    conn = _conn()
    try:
        cur = conn.execute(
            "UPDATE trades SET close_attempts = COALESCE(close_attempts, 0) + 1, "
            "last_close_error = ?, last_close_attempt_at = ?, "
            "status = CASE WHEN COALESCE(close_attempts, 0) + 1 >= ? THEN 'stranded' ELSE status END "
            "WHERE order_id = ? AND closed_at IS NULL",
            (
                spec.get("reason"),
                spec.get("attempted_at", time.time()),
                STRANDED_AFTER_ATTEMPTS,
                order_id,
            ),
        )
        conn.commit()
        if cur.rowcount == 0:
            return {"ok": False, "error": f"no open trade found for order_id {order_id}"}
        row = conn.execute(
            "SELECT close_attempts, status FROM trades WHERE order_id = ?", (order_id,)
        ).fetchone()
    finally:
        conn.close()
    return {
        "ok": True,
        "order_id": order_id,
        "close_attempts": row["close_attempts"],
        "status": row["status"],
    }


def cmd_record_mark(args) -> dict:
    """One monitoring tick's valuation of one open position.

    Written whether or not the quotes were good enough to act on: `usable=0` with a `refusal`
    is a measurement ("we looked, and could not price it"), and dropping it would make a stalled
    feed indistinguishable from a market in which nothing happened. Also carries the running
    excursion onto the trade, so max/min unrealized survive without re-scanning every mark.
    """
    spec = json.loads(args.data)
    order_id = spec.get("order_id")
    if not order_id:
        return {"ok": False, "error": "missing required field: order_id"}

    marked_at = spec.get("marked_at", time.time())
    unrealized = spec.get("unrealized_pnl")
    conn = _conn()
    try:
        cur = conn.execute(
            "INSERT INTO position_marks (order_id, marked_at, session_date, exit_debit, "
            " unrealized_pnl, spot, source, quotes_fresh, quotes_stale, max_leg_spread_pct, "
            " usable, refusal) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                order_id,
                marked_at,
                spec.get("session_date") or _date.fromtimestamp(marked_at).isoformat(),
                spec.get("exit_debit"),
                unrealized,
                spec.get("spot"),
                spec.get("source"),
                spec.get("quotes_fresh"),
                spec.get("quotes_stale"),
                spec.get("max_leg_spread_pct"),
                1 if spec.get("usable") else 0,
                spec.get("refusal"),
            ),
        )
        # Only a usable mark moves the excursion: a refused mark carries no price to speak of, and
        # letting one set a new low would invent a drawdown the position never had.
        if spec.get("usable") and unrealized is not None:
            conn.execute(
                "UPDATE trades SET max_unrealized_pnl = MAX(COALESCE(max_unrealized_pnl, ?), ?), "
                "min_unrealized_pnl = MIN(COALESCE(min_unrealized_pnl, ?), ?) WHERE order_id = ?",
                (unrealized, unrealized, unrealized, unrealized, order_id),
            )
        conn.commit()
        mark_id = cur.lastrowid
    finally:
        conn.close()
    return {"ok": True, "order_id": order_id, "mark_id": mark_id}


def cmd_record_management_event(args) -> dict:
    """One management verdict, executed or not. `gate` names what held a decision back when
    `executed` is 0 -- an unexecuted verdict is the most interesting row in the table, because it
    is the only record that the system saw the exit before it was allowed to take it."""
    spec = json.loads(args.data)
    order_id = spec.get("order_id")
    action, reason = spec.get("action"), spec.get("reason")
    if not order_id or not action or not reason:
        return {"ok": False, "error": "missing required field(s): order_id, action, reason"}

    occurred_at = spec.get("occurred_at", time.time())
    detail = spec.get("detail")
    conn = _conn()
    try:
        cur = conn.execute(
            "INSERT INTO management_events (order_id, occurred_at, session_date, phase, action, "
            " reason, executed, gate, detail_json, mark_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                order_id,
                occurred_at,
                spec.get("session_date") or _date.fromtimestamp(occurred_at).isoformat(),
                spec.get("phase"),
                action,
                reason,
                1 if spec.get("executed") else 0,
                spec.get("gate"),
                json.dumps(detail) if detail is not None else None,
                spec.get("mark_id"),
            ),
        )
        conn.commit()
        event_id = cur.lastrowid
    finally:
        conn.close()
    return {"ok": True, "order_id": order_id, "event_id": event_id}


def cmd_record_iteration(args) -> dict:
    """One loop tick's own vital signs. Lets a reader tell a live-but-quiet loop from a dead one
    without parsing logs, which is the whole reason flies records the equivalent per tick."""
    spec = json.loads(args.data)
    phase, status = spec.get("phase"), spec.get("status")
    if not phase or not status:
        return {"ok": False, "error": "missing required field(s): phase, status"}

    ran_at = spec.get("ran_at", time.time())
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO loop_iterations (ran_at, session_date, phase, status, open_positions, "
            " marks_written, actions_taken, quotes_fresh, quotes_stale, open_capital, duration_ms, "
            " note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ran_at,
                spec.get("session_date") or _date.fromtimestamp(ran_at).isoformat(),
                phase,
                status,
                spec.get("open_positions"),
                spec.get("marks_written"),
                spec.get("actions_taken"),
                spec.get("quotes_fresh"),
                spec.get("quotes_stale"),
                spec.get("open_capital"),
                spec.get("duration_ms"),
                spec.get("note"),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


def cmd_set_open_legs(args) -> dict:
    """Replace one position's streamer-symbol rows, the flat set the producer subscribes from.

    Replace rather than merge: the legs of a position are known in full at entry, and a merge would
    leave a symbol from a corrected order subscribed forever.
    """
    spec = json.loads(args.data)
    order_id = spec.get("order_id")
    if not order_id:
        return {"ok": False, "error": "missing required field: order_id"}
    symbols = [s.strip() for s in (spec.get("streamer_symbols") or []) if isinstance(s, str) and s.strip()]
    conn = _conn()
    try:
        conn.execute("DELETE FROM open_leg_symbols WHERE order_id = ?", (order_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO open_leg_symbols (order_id, streamer_symbol) VALUES (?, ?)",
            [(order_id, s) for s in symbols],
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "order_id": order_id, "count": len(symbols)}


def cmd_clear_open_legs(args) -> dict:
    """Drop a closed position's leg symbols so the producer stops holding subscriptions for it."""
    order_id = getattr(args, "order_id", None) or json.loads(getattr(args, "data", "{}")).get("order_id")
    if not order_id:
        return {"ok": False, "error": "missing required field: order_id"}
    conn = _conn()
    try:
        cur = conn.execute("DELETE FROM open_leg_symbols WHERE order_id = ?", (order_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "order_id": order_id, "removed": cur.rowcount}


def cmd_record_measurement_break(args) -> dict:
    """Record that results before and after `break_date` are not comparable. Upserted on
    (break_date, key) so re-running the change that caused it never doubles the record."""
    spec = json.loads(args.data)
    break_date, key = spec.get("break_date"), spec.get("key")
    if not break_date or not key:
        return {"ok": False, "error": "missing required field(s): break_date, key"}
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO measurement_breaks (break_date, key, old_value, new_value, note, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(break_date, key) DO UPDATE SET "
            "old_value = excluded.old_value, new_value = excluded.new_value, note = excluded.note",
            (
                break_date,
                key,
                spec.get("old_value"),
                spec.get("new_value"),
                spec.get("note"),
                spec.get("recorded_at", time.time()),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "break_date": break_date, "key": key}


def cmd_get_measurement_breaks(args) -> dict:
    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM measurement_breaks ORDER BY break_date, key").fetchall()
    finally:
        conn.close()
    return {"ok": True, "breaks": [dict(r) for r in rows]}


def cmd_get_marks(args) -> dict:
    """Marks for one position (newest first) or one session, for the EOD narrative and the
    console's position timeline."""
    order_id = getattr(args, "order_id", None)
    session_date = getattr(args, "session_date", None)
    limit = int(getattr(args, "limit", None) or 500)
    query, params = "SELECT * FROM position_marks", []
    clauses = []
    if order_id:
        clauses.append("order_id = ?")
        params.append(order_id)
    if session_date:
        clauses.append("session_date = ?")
        params.append(session_date)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    conn = _conn()
    try:
        rows = conn.execute(query + " ORDER BY marked_at DESC LIMIT ?", [*params, limit]).fetchall()
    finally:
        conn.close()
    return {"ok": True, "marks": [dict(r) for r in rows]}


def cmd_get_management_events(args) -> dict:
    order_id = getattr(args, "order_id", None)
    session_date = getattr(args, "session_date", None)
    limit = int(getattr(args, "limit", None) or 500)
    query, params = "SELECT * FROM management_events", []
    clauses = []
    if order_id:
        clauses.append("order_id = ?")
        params.append(order_id)
    if session_date:
        clauses.append("session_date = ?")
        params.append(session_date)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    conn = _conn()
    try:
        rows = conn.execute(query + " ORDER BY occurred_at DESC LIMIT ?", [*params, limit]).fetchall()
    finally:
        conn.close()
    return {"ok": True, "events": [dict(r) for r in rows]}


def cmd_get_iterations(args) -> dict:
    session_date = getattr(args, "session_date", None)
    limit = int(getattr(args, "limit", None) or 200)
    conn = _conn()
    try:
        if session_date:
            rows = conn.execute(
                "SELECT * FROM loop_iterations WHERE session_date = ? ORDER BY ran_at DESC LIMIT ?",
                (session_date, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM loop_iterations ORDER BY ran_at DESC LIMIT ?", (limit,)
            ).fetchall()
    finally:
        conn.close()
    return {"ok": True, "iterations": [dict(r) for r in rows]}


def cmd_save_market_context(args) -> dict:
    """Upsert one per-day VIX snapshot for the EOD analysis report. Called at the entry pass
    (evening) and the close pass (next morning); the report reads the close-session row plus the
    prior day's row to show the overnight VIX move. Best-effort context, never a trading input."""
    spec = json.loads(args.data)
    date = spec.get("context_date")
    if not date:
        return {"ok": False, "error": "missing required field: context_date"}
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO market_context (context_date, vix, vix1d, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(context_date) DO UPDATE SET vix=excluded.vix, vix1d=excluded.vix1d, "
            "updated_at=excluded.updated_at",
            (date, spec.get("vix"), spec.get("vix1d"), spec.get("updated_at", time.time())),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "context_date": date}


_ENTRY_REVIEW_COLUMNS = (
    "scan_date",
    "symbol",
    "timing",
    "timing_assumed",
    "strategy",
    "price",
    "volume",
    "winrate",
    "winrate_sample",
    "iv_rv_ratio",
    "iv_rv_source",
    "term_structure",
    "market_cap",
    "expected_move",
    "expected_move_pct",
    "combined_open_interest",
    "combined_option_volume",
    "bid_ask_spread_pct",
    "net_combo_spread_pct",
    "avg_actual_move_pct",
    "move_dispersion_pct",
    "max_actual_move_pct",
    "implied_vs_avg_actual",
    "move_tail_veto",
    "iv_rank",
    "iv_percentile",
    "composite_score",
    "selected",
    "reason",
    "criteria_json",
    "logged_at",
    "profile",
)


def _entry_review_values(spec: dict) -> tuple:
    """Positional values for _ENTRY_REVIEW_COLUMNS, applying each field's own default --
    shared by db.py's identical INSERT so the two modules' entry_reviews rows never drift
    apart (see this module's own docstring on schema parity)."""
    crit = spec.get("criteria") if spec.get("criteria") is not None else spec.get("criteria_json")
    return (
        spec["scan_date"],
        spec["symbol"],
        spec.get("timing"),
        (1 if spec.get("timing_assumed") else (0 if spec.get("timing_assumed") is not None else None)),
        spec.get("strategy"),
        spec.get("price"),
        spec.get("volume"),
        spec.get("winrate"),
        spec.get("winrate_sample"),
        spec.get("iv_rv_ratio"),
        spec.get("iv_rv_source"),
        spec.get("term_structure"),
        spec.get("market_cap"),
        spec.get("expected_move"),
        spec.get("expected_move_pct"),
        spec.get("combined_open_interest"),
        spec.get("combined_option_volume"),
        spec.get("bid_ask_spread_pct"),
        spec.get("net_combo_spread_pct"),
        spec.get("avg_actual_move_pct"),
        spec.get("move_dispersion_pct"),
        spec.get("max_actual_move_pct"),
        spec.get("implied_vs_avg_actual"),
        (1 if spec.get("move_tail_veto") else (0 if spec.get("move_tail_veto") is not None else None)),
        spec.get("iv_rank"),
        spec.get("iv_percentile"),
        spec.get("composite_score"),
        1 if spec.get("selected") else 0,
        spec.get("reason"),
        json.dumps(crit) if crit is not None else None,
        spec.get("logged_at", time.time()),
        spec.get("profile", "default"),
    )


def cmd_save_entry_review(args) -> dict:
    """Upsert one per-symbol entry-review record — the data reviewed for a symbol during an entry scan
    plus the chosen/rejected decision (see scanner.build_entry_review_spec for the field set). Read by
    the orchestrator's trade-notify (per-symbol push), the EOD analysis, and scout's read-only earnings
    page. Idempotent on (scan_date, symbol, profile) so a re-run of the scan overwrites."""
    spec = json.loads(args.data)
    for req in ("scan_date", "symbol"):
        if not spec.get(req):
            return {"ok": False, "error": f"missing required field: {req}"}
    conn = _conn()
    try:
        cols = ", ".join(_ENTRY_REVIEW_COLUMNS)
        placeholders = ", ".join("?" for _ in _ENTRY_REVIEW_COLUMNS)
        updates = ", ".join(
            f"{c}=excluded.{c}" for c in _ENTRY_REVIEW_COLUMNS if c not in ("scan_date", "symbol", "profile")
        )
        conn.execute(
            f"INSERT INTO entry_reviews ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(scan_date, symbol, profile) DO UPDATE SET {updates}",
            _entry_review_values(spec),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "scan_date": spec["scan_date"], "symbol": spec["symbol"]}


def cmd_get_entry_reviews(args) -> dict:
    """Entry reviews for a scan date (default: the most recent scan on or before --date, i.e. the entry
    round whose positions are settling by that session). Ordered selected-first, then by symbol."""
    conn = _conn()
    try:
        scan_date = getattr(args, "scan_date", None)
        if not scan_date:
            on_or_before = getattr(args, "date", None)
            q = "SELECT MAX(scan_date) FROM entry_reviews"
            params: list = []
            if on_or_before:
                q += " WHERE scan_date <= ?"
                params.append(on_or_before)
            row = conn.execute(q, params).fetchone()
            scan_date = row[0] if row else None
        if not scan_date:
            return {"ok": True, "scan_date": None, "reviews": []}
        rows = conn.execute(
            "SELECT * FROM entry_reviews WHERE scan_date = ? ORDER BY selected DESC, symbol", (scan_date,)
        ).fetchall()
    finally:
        conn.close()
    return {"ok": True, "scan_date": scan_date, "reviews": [dict(r) for r in rows]}


def cmd_get_market_context(args) -> dict:
    """Return the market_context row for --date plus the most recent earlier row (for the overnight
    VIX delta). Either may be None."""
    conn = _conn()
    try:
        today = conn.execute("SELECT * FROM market_context WHERE context_date = ?", (args.date,)).fetchone()
        prior = conn.execute(
            "SELECT * FROM market_context WHERE context_date < ? ORDER BY context_date DESC LIMIT 1",
            (args.date,),
        ).fetchone()
    finally:
        conn.close()
    return {"ok": True, "today": dict(today) if today else None, "prior": dict(prior) if prior else None}


def cmd_log_scan(args) -> dict:
    spec = json.loads(args.data)
    required = ("scan_date", "symbol")
    missing = [k for k in required if not spec.get(k)]
    if missing:
        return {"ok": False, "error": f"missing required field(s): {', '.join(missing)}"}

    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO scan_log (scan_date, strategy, symbol, tier, outcome, reason, stage, "
            "reject_details, logged_at, profile) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                spec["scan_date"],
                spec.get("strategy", "iron_fly"),
                spec["symbol"],
                spec.get("tier"),
                spec.get("outcome"),
                spec.get("reason"),
                spec.get("stage", "screen"),
                json.dumps(spec["reject_details"]) if spec.get("reject_details") else None,
                spec.get("logged_at", time.time()),
                spec.get("profile", "default"),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


def cmd_get_pnl_summary(args) -> dict:
    strategy = getattr(args, "strategy", None)
    profile = getattr(args, "profile", None)
    conn = _conn()
    try:
        query = "SELECT * FROM trades WHERE closed_at IS NOT NULL"
        params: list = []
        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)
        if profile:
            query += " AND profile = ?"
            params.append(profile)
        rows = conn.execute(query + " ORDER BY closed_at", params).fetchall()
    finally:
        conn.close()

    closed = [dict(r) for r in rows]
    pnls = [r["pnl"] for r in closed if r["pnl"] is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    by_strategy: dict[str, list[float]] = {}
    for r in closed:
        if r["pnl"] is not None:
            by_strategy.setdefault(r["strategy"], []).append(r["pnl"])

    def _pnl_bundle(group: list) -> dict:
        vals = [r["pnl"] for r in group]
        return {"trades": len(vals), "total_pnl": sum(vals), "avg_pnl": sum(vals) / len(vals)}

    scored = [r for r in closed if r["pnl"] is not None]

    return {
        "ok": True,
        "strategy_filter": strategy,
        "profile_filter": profile,
        "total_trades": len(closed),
        "total_pnl": sum(pnls) if pnls else 0.0,
        "avg_pnl": (sum(pnls) / len(pnls)) if pnls else None,
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": (len(wins) / len(pnls)) if pnls else None,
        "avg_win": (sum(wins) / len(wins)) if wins else None,
        "avg_loss": (sum(losses) / len(losses)) if losses else None,
        "by_strategy": {
            s: {"trades": len(vals), "total_pnl": sum(vals), "avg_pnl": sum(vals) / len(vals)}
            for s, vals in by_strategy.items()
        },
        "by_profile": _profiles.compare_profiles(
            scored, tag_key="profile", summarize=_pnl_bundle, untagged="default"
        ),
        "trades": closed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init_db")
    sub.add_parser("get_open_positions")

    p_pnl = sub.add_parser("get_pnl_summary")
    p_pnl.add_argument("--strategy", default=None)
    p_pnl.add_argument("--profile", default=None)

    p_save_trade = sub.add_parser("save_trade")
    p_save_trade.add_argument("--data", required=True)

    p_save_close = sub.add_parser("save_close")
    p_save_close.add_argument("--data", required=True)

    p_close_fail = sub.add_parser("record_close_failure")
    p_close_fail.add_argument("--data", required=True)

    p_get_open_legs = sub.add_parser("get_open_legs")
    p_get_open_legs.add_argument("--order_id", required=True)

    p_save_leg_close = sub.add_parser("save_leg_close")
    p_save_leg_close.add_argument("--data", required=True)

    p_log_scan = sub.add_parser("log_scan")
    p_log_scan.add_argument("--data", required=True)

    p_save_mctx = sub.add_parser("save_market_context")
    p_save_mctx.add_argument("--data", required=True)

    p_get_mctx = sub.add_parser("get_market_context")
    p_get_mctx.add_argument("--date", required=True)

    p_save_rev = sub.add_parser("save_entry_review")
    p_save_rev.add_argument("--data", required=True)

    p_get_rev = sub.add_parser("get_entry_reviews")
    p_get_rev.add_argument("--date", default=None, help="Most recent scan on or before this session day")
    p_get_rev.add_argument("--scan_date", default=None, help="Exact scan date (overrides --date)")

    for name in ("record_mark", "record_management_event", "record_iteration", "set_open_legs"):
        sub.add_parser(name).add_argument("--data", required=True)

    p_clear_legs = sub.add_parser("clear_open_legs")
    p_clear_legs.add_argument("--order_id", required=True)

    p_break = sub.add_parser("record_measurement_break")
    p_break.add_argument("--data", required=True)
    sub.add_parser("get_measurement_breaks")

    for name in ("get_marks", "get_management_events"):
        p = sub.add_parser(name)
        p.add_argument("--order_id", default=None)
        p.add_argument("--session_date", default=None)
        p.add_argument("--limit", default=None)

    p_iters = sub.add_parser("get_iterations")
    p_iters.add_argument("--session_date", default=None)
    p_iters.add_argument("--limit", default=None)

    args = parser.parse_args()
    dispatch = {
        "init_db": cmd_init_db,
        "get_open_positions": cmd_get_open_positions,
        "save_trade": cmd_save_trade,
        "save_close": cmd_save_close,
        "record_close_failure": cmd_record_close_failure,
        "get_open_legs": cmd_get_open_legs,
        "save_leg_close": cmd_save_leg_close,
        "log_scan": cmd_log_scan,
        "save_market_context": cmd_save_market_context,
        "get_market_context": cmd_get_market_context,
        "save_entry_review": cmd_save_entry_review,
        "get_entry_reviews": cmd_get_entry_reviews,
        "get_pnl_summary": cmd_get_pnl_summary,
        "record_mark": cmd_record_mark,
        "record_management_event": cmd_record_management_event,
        "record_iteration": cmd_record_iteration,
        "set_open_legs": cmd_set_open_legs,
        "clear_open_legs": cmd_clear_open_legs,
        "record_measurement_break": cmd_record_measurement_break,
        "get_measurement_breaks": cmd_get_measurement_breaks,
        "get_marks": cmd_get_marks,
        "get_management_events": cmd_get_management_events,
        "get_iterations": cmd_get_iterations,
    }
    result = dispatch[args.command](args)
    json.dump(result, sys.stdout, default=str)


if __name__ == "__main__":
    main()
