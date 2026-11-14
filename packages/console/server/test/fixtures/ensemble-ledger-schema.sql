-- ensemble's ledger schema, as its own db.connect builds it. GENERATED -- do not edit by hand.
-- Same recipe as weeklyput-ledger-schema.sql: the _SCHEMA of packages/ensemble db.py, as sqlite stores it.
CREATE TABLE ensemble_decisions (
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
CREATE TABLE ensemble_entries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    arm            TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    entry_session  TEXT NOT NULL,
    outcome        TEXT NOT NULL,
    refusal        TEXT,
    detail         TEXT,
    position_id    TEXT,
    decided_at     TEXT,
    created_at     TEXT,
    updated_at     TEXT,
    UNIQUE(arm, symbol, entry_session)
);
CREATE TABLE ensemble_legs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id      TEXT NOT NULL,
    leg_role         TEXT NOT NULL,
    right            TEXT NOT NULL,
    side             INTEGER NOT NULL,
    strike           REAL NOT NULL,
    streamer_symbol  TEXT,
    occ_symbol       TEXT,
    status           TEXT NOT NULL DEFAULT 'open',
    created_at       TEXT,
    updated_at       TEXT,
    UNIQUE(position_id, leg_role)
);
CREATE TABLE ensemble_loop_iterations (
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
CREATE TABLE ensemble_marks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id    TEXT NOT NULL,
    ts             TEXT NOT NULL,
    session_date   TEXT NOT NULL,
    usable         INTEGER NOT NULL DEFAULT 0,
    refusal        TEXT,
    spot           REAL,
    mark_value     REAL
);
CREATE TABLE ensemble_positions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id         TEXT NOT NULL UNIQUE,
    arm                 TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    root                TEXT,
    entry_session       TEXT NOT NULL,
    entry_minute        INTEGER,
    entry_time          TEXT,
    expiration          TEXT NOT NULL,
    quantity            INTEGER NOT NULL DEFAULT 1,
    entry_spot          REAL,
    entry_mid           REAL,
    max_loss            REAL,
    max_profit          REAL,
    capital             REAL,
    entry_value         REAL NOT NULL,
    entry_fees          REAL NOT NULL DEFAULT 0,
    entry_slippage      REAL NOT NULL DEFAULT 0,
    entry_json          TEXT NOT NULL,
    exit_json           TEXT,
    status              TEXT NOT NULL DEFAULT 'open',
    exit_reason         TEXT,
    exit_session        TEXT,
    exit_minute         INTEGER,
    exit_time           TEXT,
    exit_spot           REAL,
    exit_mid            REAL,
    exit_value          REAL,
    settle_price        REAL,
    settle_source       TEXT,
    gross_pnl           REAL,
    fees                REAL,
    settlement          REAL,
    slippage            REAL,
    net_pnl             REAL,
    slippage_stress     REAL,
    net_stress          REAL,
    net_mid             REAL,
    worst_mark_value    REAL,
    worst_mark_time     TEXT,
    best_mark_value     REAL,
    best_mark_time      TEXT,
    created_at          TEXT,
    updated_at          TEXT
);
CREATE TABLE ensemble_snapshots (
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
CREATE TABLE ensemble_trail (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    minute      INTEGER NOT NULL,
    spot        REAL NOT NULL,
    UNIQUE(symbol, trade_date, minute)
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
CREATE INDEX idx_ensemble_decisions_date ON ensemble_decisions(trade_date);
CREATE INDEX idx_ensemble_iterations_session
    ON ensemble_loop_iterations(session_date, ran_at);
CREATE INDEX idx_ensemble_legs_status ON ensemble_legs(status);
CREATE INDEX idx_ensemble_marks_position ON ensemble_marks(position_id, ts);
CREATE INDEX idx_ensemble_positions_arm ON ensemble_positions(arm, entry_session);
CREATE INDEX idx_ensemble_positions_status ON ensemble_positions(status);
CREATE INDEX idx_ensemble_snapshots_date ON ensemble_snapshots(trade_date);
