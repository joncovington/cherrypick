-- earnings' paper ledger schema, as its own _conn() builds it. GENERATED -- do not edit by hand.
-- Pinned by packages/earnings/tests/test_console_schema_fixture.py; regenerate with
-- REGEN_CONSOLE_FIXTURE=1 there after a schema change.
CREATE TABLE daily_summary (
    summary_date    TEXT PRIMARY KEY,
    positions_opened INTEGER,
    positions_closed INTEGER,
    net_pnl        REAL
);
CREATE TABLE entry_reviews (
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
    arm            TEXT NOT NULL DEFAULT 'default',
    UNIQUE(scan_date, symbol, arm)
);
CREATE TABLE loop_iterations (
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
CREATE TABLE management_events (
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
    mark_id      INTEGER,
    -- Which book the verdict was FOR (added 2026-09-01, advisor spec earnings_comparison_integrity).
    -- An advised twin runs different exit params than its control, and without this stamp "did the
    -- advised target ever fire" was unanswerable from this table -- the order_id encodes it, but a
    -- reader should never have to parse identifiers to learn a fact the writer knew.
    arm          TEXT
);
CREATE TABLE market_context (
    context_date  TEXT PRIMARY KEY,
    vix           REAL,
    vix1d         REAL,
    updated_at    REAL
);
CREATE TABLE measurement_breaks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    break_date  TEXT NOT NULL,
    key         TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    note        TEXT,
    recorded_at REAL,
    UNIQUE(break_date, key)
);
CREATE TABLE open_leg_symbols (
    order_id        TEXT NOT NULL,
    streamer_symbol TEXT NOT NULL,
    PRIMARY KEY (order_id, streamer_symbol)
);
CREATE TABLE position_marks (
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
CREATE TABLE scan_log (
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
    arm            TEXT NOT NULL DEFAULT 'default'
);
CREATE TABLE trade_legs (
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
CREATE TABLE trades (
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
    arm             TEXT NOT NULL DEFAULT 'default',
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
, close_attempts INTEGER NOT NULL DEFAULT 0, last_close_error TEXT, last_close_attempt_at REAL, entry_slippage REAL, exit_slippage REAL, advice_params TEXT, experiment_id TEXT);
CREATE INDEX idx_loop_iterations_session ON loop_iterations(session_date, ran_at);
CREATE INDEX idx_management_events_order ON management_events(order_id, occurred_at);
CREATE INDEX idx_management_events_session ON management_events(session_date);
CREATE INDEX idx_position_marks_order ON position_marks(order_id, marked_at);
CREATE INDEX idx_position_marks_session ON position_marks(session_date);
