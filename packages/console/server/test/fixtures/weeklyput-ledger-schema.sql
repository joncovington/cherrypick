-- weeklyput's ledger schema, as its own db.connect builds it. GENERATED -- do not edit by hand.
-- Same recipe as contango-ledger-schema.sql; the module has no pinning test for it yet.
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
CREATE TABLE weeklyput_decisions (
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
CREATE TABLE weeklyput_entries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    arm            TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    expiration     TEXT NOT NULL,
    entry_session  TEXT NOT NULL,
    outcome        TEXT NOT NULL,
    refusal        TEXT,
    position_id    TEXT,
    decided_at     TEXT,
    created_at     TEXT,
    updated_at     TEXT,
    UNIQUE(arm, symbol, expiration)
);
CREATE TABLE weeklyput_loop_iterations (
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
CREATE TABLE weeklyput_marks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id    TEXT NOT NULL,
    ts             TEXT NOT NULL,
    session_date   TEXT NOT NULL,
    usable         INTEGER NOT NULL DEFAULT 0,
    refusal        TEXT,
    bid            REAL,
    ask            REAL,
    mid            REAL,
    spot           REAL,
    mark_value     REAL,
    extrinsic      REAL
);
CREATE TABLE weeklyput_positions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id         TEXT NOT NULL UNIQUE,
    arm                 TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    root                TEXT NOT NULL,
    settlement_style    TEXT NOT NULL,
    expiration          TEXT NOT NULL,
    entry_session       TEXT NOT NULL,
    entry_time          TEXT,
    streamer_symbol     TEXT NOT NULL,
    occ_symbol          TEXT,
    strike              REAL NOT NULL,
    quantity            INTEGER NOT NULL DEFAULT 1,
    entry_spot          REAL,
    entry_forward       REAL NOT NULL,
    entry_years         REAL,
    entry_bid           REAL,
    entry_ask           REAL,
    entry_mid           REAL,
    entry_iv            REAL,
    entry_delta         REAL,
    neighbours_json     TEXT,
    entry_value         REAL NOT NULL,
    entry_fees          REAL NOT NULL DEFAULT 0,
    entry_slippage      REAL NOT NULL DEFAULT 0,
    notional            REAL NOT NULL,
    status              TEXT NOT NULL DEFAULT 'open',
    settle_session      TEXT,
    settle_time         TEXT,
    settle_price        REAL,
    settle_source       TEXT,
    exit_value          REAL,
    exit_reason         TEXT,
    settlement          REAL,
    gross_pnl           REAL,
    fees                REAL,
    slippage            REAL,
    net_pnl             REAL,
    net_bp              REAL,
    worst_mark_value    REAL,
    worst_mark_time     TEXT,
    assignment_exposed_at TEXT,
    created_at          TEXT,
    updated_at          TEXT
);
CREATE TABLE weeklyput_snapshots (
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
CREATE INDEX idx_weeklyput_decisions_date ON weeklyput_decisions(trade_date);
CREATE INDEX idx_weeklyput_iterations_session
    ON weeklyput_loop_iterations(session_date, ran_at);
CREATE INDEX idx_weeklyput_marks_position ON weeklyput_marks(position_id, ts);
CREATE INDEX idx_weeklyput_positions_expiry ON weeklyput_positions(expiration);
CREATE INDEX idx_weeklyput_positions_status ON weeklyput_positions(status);
CREATE INDEX idx_weeklyput_snapshots_date ON weeklyput_snapshots(trade_date);
