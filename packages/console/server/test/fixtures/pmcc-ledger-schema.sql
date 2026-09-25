-- pmcc's ledger schema, as its own db.connect builds it. GENERATED -- do not edit by hand.
-- Pinned by packages/pmcc/tests/test_console_schema_fixture.py; regenerate with
-- REGEN_CONSOLE_FIXTURE=1 there after a schema change.
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
CREATE TABLE pmcc_assignments (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id      TEXT NOT NULL,
    leg_role         TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    assigned_session TEXT NOT NULL,
    assigned_at      TEXT,
    direction        TEXT NOT NULL,
    shares           INTEGER NOT NULL,
    basis            REAL NOT NULL,
    strike           REAL NOT NULL,
    option_type      TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'open',
    disposed_session TEXT,
    disposed_at      TEXT,
    disposal_price   REAL,
    share_pnl        REAL,
    fees             REAL,
    created_at       TEXT,
    updated_at       TEXT,
    UNIQUE(position_id, leg_role)
);
CREATE TABLE pmcc_daily_bars (
    symbol          TEXT NOT NULL,
    trade_date      TEXT NOT NULL,
    day_open        REAL,
    day_high        REAL,
    day_low         REAL,
    day_close       REAL,
    prev_day_close  REAL,
    source          TEXT,
    updated_at      REAL,
    PRIMARY KEY (symbol, trade_date)
);
CREATE TABLE pmcc_decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT NOT NULL,
    arm        TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    mode        TEXT NOT NULL,
    reason      TEXT NOT NULL,
    accepted    INTEGER NOT NULL DEFAULT 0,
    occurrences INTEGER NOT NULL DEFAULT 1,
    first_ts    TEXT,
    last_ts     TEXT,
    detail      TEXT
);
CREATE TABLE pmcc_entry_attempts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    trade_date     TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    arm           TEXT NOT NULL,
    outcome        TEXT NOT NULL,
    block_detail   TEXT,
    spot           REAL,
    target_yield   REAL,
    achieved_yield REAL,
    best_yield     REAL,
    long_strike    REAL,
    short_strike   REAL,
    net_debit      REAL,
    protection_pct REAL
);
CREATE TABLE pmcc_legs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id     TEXT NOT NULL,
    leg_role        TEXT NOT NULL,
    occ_symbol      TEXT NOT NULL,
    streamer_symbol TEXT NOT NULL,
    expiration      TEXT NOT NULL,
    strike          REAL NOT NULL,
    option_type     TEXT NOT NULL,
    action          TEXT NOT NULL,
    quantity        INTEGER NOT NULL DEFAULT 1,
    entry_bid       REAL,
    entry_ask       REAL,
    entry_mid       REAL,
    entry_iv        REAL,
    entry_delta     REAL,
    status          TEXT NOT NULL DEFAULT 'open',
    close_kind      TEXT,
    closed_at       TEXT,
    close_bid       REAL,
    close_ask       REAL,
    close_value     REAL,
    created_at      TEXT,
    updated_at      TEXT,
    UNIQUE(position_id, leg_role)
);
CREATE TABLE pmcc_loop_iterations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at         REAL NOT NULL,
    session_date   TEXT NOT NULL,
    phase          TEXT NOT NULL,
    status         TEXT NOT NULL,
    open_positions INTEGER,
    marks_written  INTEGER,
    actions_taken  INTEGER,
    note           TEXT
);
CREATE TABLE pmcc_management_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id  TEXT NOT NULL,
    occurred_at  REAL NOT NULL,
    session_date TEXT NOT NULL,
    action       TEXT NOT NULL,
    reason       TEXT NOT NULL,
    executed     INTEGER NOT NULL DEFAULT 0,
    gate         TEXT,
    detail_json  TEXT
);
CREATE TABLE pmcc_marks (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id        TEXT NOT NULL,
    leg_role           TEXT,
    marked_at          REAL NOT NULL,
    session_date       TEXT NOT NULL,
    bid                REAL,
    ask                REAL,
    mid                REAL,
    delta              REAL,
    iv                 REAL,
    vega               REAL,
    spot               REAL,
    short_tv           REAL,
    assignment_exposed INTEGER,
    quote_age_s        REAL,
    usable             INTEGER NOT NULL DEFAULT 0,
    refusal            TEXT
);
CREATE TABLE pmcc_positions (
    id                             INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id                    TEXT NOT NULL UNIQUE,
    symbol                         TEXT NOT NULL,
    arm                           TEXT NOT NULL,
    entry_session                  TEXT NOT NULL,
    quantity                       INTEGER NOT NULL DEFAULT 1,
    long_expiration                TEXT NOT NULL,
    long_strike                    REAL NOT NULL,
    short_expiration               TEXT NOT NULL,
    short_strike                   REAL NOT NULL,
    entry_time                     TEXT,
    entry_spot                     REAL,
    long_entry_mid                 REAL,
    short_entry_mid                REAL,
    net_debit                      REAL,
    entry_cost                     REAL,
    entry_slippage                 REAL,
    entry_short_dte                INTEGER,
    entry_long_dte                 INTEGER,
    entry_total_premium            REAL,
    entry_short_intrinsic          REAL,
    entry_short_tv                 REAL,
    entry_net_tv                   REAL,
    entry_long_extrinsic           REAL,
    entry_profit_pct               REAL,
    entry_weekly_yield_pct         REAL,
    entry_downside_protection_pct  REAL,
    entry_breakeven                REAL,
    entry_buffer_to_breakeven_pct  REAL,
    entry_long_delta               REAL,
    entry_short_delta              REAL,
    entry_long_iv                  REAL,
    entry_short_iv                 REAL,
    long_selected_by               TEXT,
    keltner_mid                    REAL,
    keltner_atr                    REAL,
    keltner_days                   INTEGER,
    keltner_distance_atr           REAL,
    keltner_bounce_atr             REAL,
    keltner_prev_close_gap         REAL,
    advice_params                  TEXT,
    experiment_id                  TEXT,
    roll_count                     INTEGER NOT NULL DEFAULT 0,
    exposure_ticks                 INTEGER,
    status                         TEXT NOT NULL DEFAULT 'open',
    exit_reason                    TEXT,
    closed_at                      TEXT,
    closed_session                 TEXT,
    exit_value                     REAL,
    exit_cost                      REAL,
    exit_slippage                  REAL,
    settlement_spot                REAL,
    itm_settlements                INTEGER,
    gross_pnl                      REAL,
    fees                           REAL,
    created_at                     TEXT,
    updated_at                     TEXT,
    era                            TEXT
, settlement_fees REAL, advice_base TEXT);
CREATE TABLE pmcc_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    trade_date    TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    kind          TEXT NOT NULL,
    status        TEXT NOT NULL,
    quotes_fresh  INTEGER,
    quotes_stale  INTEGER,
    spot          REAL
);
CREATE TABLE pmcc_stream_window (
    symbol                     TEXT PRIMARY KEY,
    width                      INTEGER,
    last_escalated_occurrences INTEGER DEFAULT 0,
    last_checked_occurrences   INTEGER DEFAULT 0,
    last_escalated_at          TEXT,
    last_miss_at               TEXT,
    updated_at                 TEXT
);
CREATE INDEX idx_pmcc_assignments_position ON pmcc_assignments(position_id);
CREATE INDEX idx_pmcc_assignments_status ON pmcc_assignments(status, assigned_session);
CREATE INDEX idx_pmcc_attempts_date ON pmcc_entry_attempts(trade_date);
CREATE INDEX idx_pmcc_decisions_date ON pmcc_decisions(trade_date);
CREATE INDEX idx_pmcc_events_position ON pmcc_management_events(position_id, occurred_at);
CREATE INDEX idx_pmcc_events_session ON pmcc_management_events(session_date);
CREATE INDEX idx_pmcc_iterations_session ON pmcc_loop_iterations(session_date, ran_at);
CREATE INDEX idx_pmcc_legs_status ON pmcc_legs(status, expiration);
CREATE INDEX idx_pmcc_marks_position ON pmcc_marks(position_id, marked_at);
CREATE INDEX idx_pmcc_marks_session ON pmcc_marks(session_date);
CREATE INDEX idx_pmcc_positions_session ON pmcc_positions(entry_session, arm);
CREATE INDEX idx_pmcc_positions_status ON pmcc_positions(status);
CREATE INDEX idx_pmcc_snapshots_date ON pmcc_snapshots(trade_date);
