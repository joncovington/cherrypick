-- meic's ledger schema, as its own cmd_init_db builds it. GENERATED -- do not edit by hand.
-- Pinned by packages/meic/tests/test_console_schema_fixture.py; regenerate with
-- REGEN_CONSOLE_FIXTURE=1 there after a schema change.
CREATE TABLE daily_summary (
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
CREATE TABLE entry_attempts (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                          TEXT NOT NULL,
    trade_date                  TEXT NOT NULL,
    arm                         TEXT NOT NULL,
    symbol                      TEXT NOT NULL,
    expiration                  TEXT,
    outcome                     TEXT NOT NULL,  -- filled | cadence_blocked | sign_rule_blocked
                                                --   | gate_blocked | window_blocked | no_candidate
                                                --   | no_fill
    block_detail                TEXT,           -- the evaluate_entry reason, e.g. 'regime_gex_negative'
    proposed_legs               TEXT,           -- JSON [{strike, right, sign}] of the chosen candidate
    put_strike                  REAL,
    call_strike                 REAL,
    wing_width                  REAL,
    blocking_strike             REAL,           -- populated for sign_rule_blocked
    seconds_until_cadence_clear REAL,           -- populated for cadence_blocked
    underlying_price            REAL,
    iv_rank                     REAL,
    gex_net                     REAL,
    gex_positive                INTEGER,
    session_quality             TEXT,
    would_be_credit             REAL,
    ic_order_id                 TEXT            -- set on the filled path, linking attempt to result
);
CREATE TABLE ic_spread_legs (
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
CREATE TABLE ic_trades (
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
    arm                       TEXT,
    execution_mode            TEXT,
    iv_rank_source            TEXT,
    created_at                TEXT NOT NULL,
    updated_at                TEXT NOT NULL
, put_stop_fill_status TEXT, call_stop_fill_status TEXT, pending_exit_json TEXT, entry_mid_at_submit REAL, settlement_fees REAL, entry_vol_implied_bucket TEXT, entry_vol_implied_value REAL, entry_vol_event_bucket TEXT, entry_vol_event_value REAL, entry_vol_realized_bucket TEXT, entry_vol_realized_value REAL, entry_vol_intraday_bucket TEXT, entry_vol_intraday_value REAL, entry_gex_bucket TEXT, entry_gex_value REAL, entry_skew_bucket TEXT, entry_skew_value REAL, entry_center_offset_bucket TEXT, entry_center_offset_value REAL, entry_trend_bucket TEXT, entry_trend_value REAL, credit_richness REAL, put_credit_fraction REAL, minutes_to_close INTEGER, put_touch_time TEXT, put_touch_spot REAL, call_touch_time TEXT, call_touch_spot REAL, put_mae_spot REAL, put_mae_time TEXT, call_mae_spot REAL, call_mae_time TEXT, era TEXT DEFAULT 'sample', experiment_id TEXT);
CREATE TABLE iteration_regime (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    loop_date            TEXT NOT NULL,
    loop_time            TEXT NOT NULL,
    symbol               TEXT NOT NULL,
    underlying_price     REAL,
    entries_n            INTEGER DEFAULT 0,
    blocked_n            INTEGER DEFAULT 0,
    vol_implied_bucket   TEXT,
    vol_implied_value    REAL,
    vol_event_bucket     TEXT,
    vol_event_value      REAL,
    vol_realized_bucket  TEXT,
    vol_realized_value   REAL,
    vol_intraday_bucket  TEXT,
    vol_intraday_value   REAL,
    gex_bucket           TEXT,
    gex_value            REAL,
    gex_positive         INTEGER,
    trend_bucket         TEXT,
    trend_value          REAL,
    created_at           TEXT NOT NULL
);
CREATE TABLE loop_log (
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
CREATE TABLE market_context (
    context_date  TEXT PRIMARY KEY,
    vix           REAL,
    vix1d         REAL,
    vix1d_ratio   REAL,
    symbols_json  TEXT DEFAULT '{}',
    updated_at    TEXT NOT NULL
);
CREATE TABLE measurement_breaks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    break_date  TEXT NOT NULL,
    scope       TEXT NOT NULL,   -- an arm/profile name, or '*' for the whole book
    kind        TEXT NOT NULL,   -- 'cadence' | 'arm_added' | 'gate_changed' | ...
    reason      TEXT NOT NULL,
    detail      TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE (break_date, scope, kind)
);
CREATE INDEX idx_entry_attempts_date ON entry_attempts (trade_date, arm);
CREATE INDEX idx_entry_attempts_outcome ON entry_attempts (trade_date, outcome);
CREATE INDEX idx_ic_trades_date_status ON ic_trades(trade_date, status);
CREATE INDEX idx_ic_trades_profile_date ON ic_trades(arm, trade_date, status);
CREATE INDEX idx_ic_trades_symbol_status ON ic_trades(symbol, status);
CREATE INDEX idx_iteration_regime_date ON iteration_regime (loop_date, symbol);
CREATE INDEX idx_loop_log_symbol_date ON loop_log(symbol, loop_date);
CREATE INDEX idx_measurement_breaks_date ON measurement_breaks (break_date);
