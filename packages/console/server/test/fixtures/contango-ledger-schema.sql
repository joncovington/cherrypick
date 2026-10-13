-- contango's ledger schema, as its own db.connect builds it. GENERATED -- do not edit by hand.
-- Pinned by packages/contango/tests/test_console_schema_fixture.py; regenerate with
-- REGEN_CONSOLE_FIXTURE=1 there after a schema change.
CREATE TABLE contango_accounts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    arm              TEXT NOT NULL UNIQUE,
    starting_capital REAL NOT NULL,
    cash             REAL NOT NULL,
    opened_session   TEXT NOT NULL,
    created_at       TEXT,
    updated_at       TEXT
);
CREATE TABLE contango_decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT NOT NULL,
    arm         TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    mode        TEXT NOT NULL,
    reason      TEXT NOT NULL,
    accepted    INTEGER NOT NULL DEFAULT 0,
    occurrences INTEGER NOT NULL DEFAULT 1,
    first_ts    TEXT,
    last_ts     TEXT,
    detail      TEXT
);
CREATE TABLE contango_distributions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id      TEXT NOT NULL,
    arm              TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    ex_date          TEXT NOT NULL,
    per_share        REAL NOT NULL,
    shares           INTEGER NOT NULL,
    amount           REAL NOT NULL,
    credited_session TEXT NOT NULL,
    created_at       TEXT,
    updated_at       TEXT,
    UNIQUE(position_id, ex_date)
);
CREATE TABLE contango_loop_iterations (
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
CREATE TABLE contango_marks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    bid         REAL,
    ask         REAL,
    mid         REAL,
    age_s       REAL,
    created_at  TEXT,
    updated_at  TEXT,
    UNIQUE(trade_date, symbol)
);
CREATE TABLE contango_positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id     TEXT NOT NULL UNIQUE,
    arm             TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    role            TEXT NOT NULL,
    shares          INTEGER NOT NULL,
    entry_session   TEXT NOT NULL,
    entry_time      TEXT,
    entry_bid       REAL,
    entry_ask       REAL,
    entry_mid       REAL,
    entry_value     REAL,
    entry_fees      REAL,
    entry_slippage  REAL,
    entry_ratio     REAL,
    status          TEXT NOT NULL DEFAULT 'open',
    exit_session    TEXT,
    exit_time       TEXT,
    exit_bid        REAL,
    exit_ask        REAL,
    exit_mid        REAL,
    exit_value      REAL,
    exit_fees       REAL,
    exit_slippage   REAL,
    exit_ratio      REAL,
    exit_reason     TEXT,
    distributions   REAL NOT NULL DEFAULT 0,
    gross_pnl       REAL,
    fees            REAL,
    slippage        REAL,
    net_pnl         REAL,
    created_at      TEXT,
    updated_at      TEXT
);
CREATE TABLE contango_regime (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT NOT NULL UNIQUE,
    tick        TEXT NOT NULL,
    ratio       REAL,
    vix         REAL,
    vix3m       REAL,
    vix_age_s   REAL,
    vix3m_age_s REAL,
    usable      INTEGER NOT NULL DEFAULT 0,
    refusal     TEXT,
    created_at  TEXT,
    updated_at  TEXT
);
CREATE TABLE contango_sessions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date     TEXT NOT NULL,
    arm            TEXT NOT NULL,
    decided_at     TEXT,
    ratio          REAL,
    state_before   TEXT,
    state_after    TEXT,
    action         TEXT,
    refusal        TEXT,
    holding_symbol TEXT,
    shares         INTEGER,
    mark           REAL,
    cash           REAL,
    nav            REAL,
    created_at     TEXT,
    updated_at     TEXT,
    UNIQUE(trade_date, arm)
);
CREATE TABLE contango_snapshots (
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
CREATE INDEX idx_contango_decisions_date ON contango_decisions(trade_date);
CREATE INDEX idx_contango_iterations_session ON contango_loop_iterations(session_date, ran_at);
CREATE INDEX idx_contango_positions_arm ON contango_positions(arm, status);
CREATE INDEX idx_contango_positions_session ON contango_positions(entry_session);
CREATE INDEX idx_contango_snapshots_date ON contango_snapshots(trade_date);
