-- calendars's ledger schema, as its own db.connect builds it. GENERATED -- do not edit by hand.
-- Pinned by packages/calendars/tests/test_console_schema_fixture.py; regenerate with
-- REGEN_CONSOLE_FIXTURE=1 there after a schema change.
CREATE TABLE dc_assignments (
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
CREATE TABLE dc_decisions (
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
CREATE TABLE dc_entry_attempts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    trade_date    TEXT NOT NULL,
    week_of       TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    outcome       TEXT NOT NULL,
    block_detail  TEXT,
    spot          REAL,
    em            REAL,
    put_target    REAL,
    call_target   REAL,
    put_strike    REAL,
    call_strike   REAL,
    put_debit     REAL,
    call_debit    REAL
);
CREATE TABLE dc_legs (
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
CREATE TABLE dc_loop_iterations (
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
CREATE TABLE dc_management_events (
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
CREATE TABLE dc_marks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id  TEXT NOT NULL,
    leg_role     TEXT,
    marked_at    REAL NOT NULL,
    session_date TEXT NOT NULL,
    bid          REAL,
    ask          REAL,
    mid          REAL,
    delta        REAL,
    iv           REAL,
    vega         REAL,
    spot         REAL,
    quote_age_s  REAL,
    usable       INTEGER NOT NULL DEFAULT 0,
    refusal      TEXT
);
CREATE TABLE dc_paired_debits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at     TEXT NOT NULL,
    week_of         TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    strike          REAL,
    friday_session  TEXT,
    friday_debit    REAL,
    friday_spot     REAL,
    monday_session  TEXT NOT NULL,
    monday_debit    REAL,
    monday_spot     REAL,
    usable          INTEGER NOT NULL DEFAULT 0,
    refusal         TEXT,
    UNIQUE(week_of, side)
);
CREATE TABLE dc_positions (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id              TEXT NOT NULL UNIQUE,
    week_of                  TEXT NOT NULL,
    entry_session            TEXT NOT NULL,
    arm                     TEXT NOT NULL,
    side                     TEXT NOT NULL,
    symbol                   TEXT NOT NULL,
    structure                TEXT NOT NULL,
    front_expiration         TEXT NOT NULL,
    back_expiration          TEXT NOT NULL,
    strike                   REAL NOT NULL,
    quantity                 INTEGER NOT NULL DEFAULT 1,
    entry_time               TEXT,
    entry_debit              REAL,
    entry_cost               REAL,
    entry_slippage           REAL,
    entry_spot               REAL,
    entry_em                 REAL,
    entry_em_pct             REAL,
    entry_front_atm_call_mid REAL,
    entry_front_atm_put_mid  REAL,
    entry_front_iv           REAL,
    entry_back_iv            REAL,
    entry_term_structure     REAL,
    entry_context            TEXT,
    advice_params            TEXT,
    experiment_id            TEXT,
    status                   TEXT NOT NULL DEFAULT 'open',
    exit_reason              TEXT,
    closed_at                TEXT,
    closed_session           TEXT,
    exit_value               REAL,
    exit_cost                REAL,
    exit_slippage            REAL,
    settlement_spot          REAL,
    itm_settlements          INTEGER,
    gross_pnl                REAL,
    fees                     REAL,
    created_at               TEXT,
    updated_at               TEXT
, settlement_fees REAL, advice_base TEXT);
CREATE TABLE dc_snapshots (
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
CREATE INDEX idx_dc_assignments_position ON dc_assignments(position_id);
CREATE INDEX idx_dc_assignments_status ON dc_assignments(status, assigned_session);
CREATE INDEX idx_dc_attempts_date ON dc_entry_attempts(trade_date);
CREATE INDEX idx_dc_decisions_date ON dc_decisions(trade_date);
CREATE INDEX idx_dc_events_position ON dc_management_events(position_id, occurred_at);
CREATE INDEX idx_dc_events_session ON dc_management_events(session_date);
CREATE INDEX idx_dc_iterations_session ON dc_loop_iterations(session_date, ran_at);
CREATE INDEX idx_dc_legs_status ON dc_legs(status, expiration);
CREATE INDEX idx_dc_marks_position ON dc_marks(position_id, marked_at);
CREATE INDEX idx_dc_marks_session ON dc_marks(session_date);
CREATE INDEX idx_dc_positions_status ON dc_positions(status);
CREATE INDEX idx_dc_positions_week ON dc_positions(week_of, arm);
CREATE INDEX idx_dc_snapshots_date ON dc_snapshots(trade_date);
