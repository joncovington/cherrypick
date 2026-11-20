-- ladder's LIVE ledger schema, as its own live_db.connect builds it. GENERATED -- do not edit by hand.
CREATE TABLE ladder_positions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id      TEXT NOT NULL UNIQUE,
    arm              TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    entry_rule       TEXT NOT NULL,
    target_rule      TEXT NOT NULL,
    quantity         INTEGER NOT NULL,
    ref_close        REAL,
    chained_from     REAL,
    buy_level        REAL NOT NULL,
    buy_price        REAL NOT NULL,
    buy_session      TEXT NOT NULL,
    buy_time         TEXT NOT NULL,
    buy_bid          REAL,
    buy_ask          REAL,
    status           TEXT NOT NULL DEFAULT 'open',
    target_price     REAL,
    target_stage     TEXT,
    low_bid          REAL,
    low_bid_time     TEXT,
    last_bid         REAL,
    last_mark_time   TEXT,
    sell_price       REAL,
    sell_session     TEXT,
    sell_time        TEXT,
    sell_bid         REAL,
    sell_ask         REAL,
    exit_reason      TEXT,
    hold_days        INTEGER,
    entry_value      REAL NOT NULL,
    exit_value       REAL,
    gross_pnl        REAL,
    fees             REAL,
    settlement       REAL,
    slippage         REAL,
    net_pnl          REAL,
    net_pct          REAL,
    created_at       TEXT,
    updated_at       TEXT
, account TEXT, buy_order_id TEXT, buy_client_id TEXT, buy_fees REAL, sell_order_id TEXT, sell_client_id TEXT, sell_fees REAL, fees_source TEXT);
CREATE INDEX idx_ladder_positions_status ON ladder_positions(status);
CREATE INDEX idx_ladder_positions_arm ON ladder_positions(arm, symbol, buy_session);
CREATE TABLE ladder_decisions (
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
CREATE INDEX idx_ladder_decisions_date ON ladder_decisions(trade_date);
CREATE TABLE ladder_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    trade_date    TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    kind          TEXT NOT NULL,
    status        TEXT NOT NULL,
    bid           REAL,
    ask           REAL,
    ref_close     REAL
);
CREATE INDEX idx_ladder_snapshots_date ON ladder_snapshots(trade_date);
CREATE TABLE ladder_loop_iterations (
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
CREATE INDEX idx_ladder_iterations_session ON ladder_loop_iterations(session_date, ran_at);
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
CREATE TABLE ladder_orders (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id           TEXT NOT NULL UNIQUE,
    order_id            TEXT,
    side                TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    lot_id              TEXT,
    shares              INTEGER NOT NULL,
    limit_price         REAL NOT NULL,
    time_in_force       TEXT,
    account             TEXT,
    state               TEXT NOT NULL DEFAULT 'working',
    final               INTEGER NOT NULL DEFAULT 0,
    placed_at           TEXT,
    placed_session      TEXT,
    adopted             INTEGER NOT NULL DEFAULT 0,
    cancel_requested_at TEXT,
    ended_session       TEXT,
    filled_shares       INTEGER,
    fill_price          REAL,
    filled_at           TEXT,
    fill_source         TEXT,
    fees                REAL,
    fees_source         TEXT,
    created_at          TEXT,
    updated_at          TEXT
);
CREATE INDEX idx_ladder_orders_open ON ladder_orders(final, symbol);
CREATE TABLE ladder_lot_splits (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id   TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    ex_date       TEXT NOT NULL,
    to_shares     REAL NOT NULL,
    for_shares    REAL NOT NULL,
    shares_before INTEGER NOT NULL,
    shares_after  INTEGER NOT NULL,
    price_before  REAL NOT NULL,
    price_after   REAL NOT NULL,
    applied_at    TEXT,
    UNIQUE (position_id, ex_date)
);
