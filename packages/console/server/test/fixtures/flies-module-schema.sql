-- Generated from packages/flies (cherrypick.flies.db.connect on an empty file), 2026-10-08.
-- Regenerate rather than hand-edit, so the console tests read the module's real columns.

CREATE TABLE fly_books (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id           TEXT UNIQUE,
    trade_date        TEXT,
    arm               TEXT,
    symbol            TEXT,
    credit_collected  REAL,
    debits_paid       REAL,
    fees              REAL,
    net_cash          REAL,
    worst             REAL,
    worst_at          REAL,
    floor_holds       INTEGER,
    band_low          REAL,
    band_high         REAL,
    unbounded_below   INTEGER,
    completion_rate   REAL,
    risk_free_rate    REAL,
    pin_rate          REAL,
    settlement_price  REAL,
    pnl               REAL,
    status            TEXT,
    created_at        TEXT,
    updated_at        TEXT
, settlement_source TEXT, modeled_pnl REAL, modeled_fees REAL, broker_reconciled_at TEXT, broker_reconciliation_status TEXT);

CREATE TABLE fly_debit_ladder (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    stamped_at         TEXT NOT NULL,
    trade_date         TEXT NOT NULL,
    symbol             TEXT NOT NULL,
    arm                TEXT NOT NULL,
    anchor_position_id TEXT NOT NULL,
    direction          TEXT NOT NULL,     -- up | down
    k                  INTEGER NOT NULL,  -- strikes out of the money, centre vs the anchor's centre
    side               TEXT,
    center             REAL,
    wing_width         REAL,
    spot_at_stamp      REAL,
    refusal            TEXT,
    entry_debit        REAL,
    entry_fee          REAL,
    first_complete_at  TEXT,
    complete_credit    REAL,
    completion_fee     REAL,
    best_credit        REAL,
    best_credit_at     TEXT,
    settlement_price   REAL,
    pnl                REAL,
    UNIQUE (anchor_position_id, direction, k)
);

CREATE TABLE fly_decisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date    TEXT,
    arm           TEXT,
    symbol        TEXT,
    mode          TEXT,     -- legged | outright | completion
    reason        TEXT,     -- the engine's reason string, plus entered / completed on the accept path
    accepted      INTEGER,  -- 1 when this run represents action taken, 0 when it is a refusal
    first_seen    TEXT,
    last_seen     TEXT,
    occurrences   INTEGER,
    center_first  REAL,
    center_last   REAL,
    position_id   TEXT,     -- set on the accept path, so a decision links to what it produced
    detail        TEXT
);

CREATE TABLE fly_entry_attempts (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                          TEXT,
    trade_date                  TEXT,
    arm                         TEXT,
    symbol                      TEXT,
    expiry                      TEXT,
    mode                        TEXT,     -- legged | outright | bwb | debit_first
    outcome                     TEXT,     -- filled | cadence_blocked | sign_rule_blocked
                                          --   | duplicate_blocked | gate_blocked | window_blocked
                                          --   | no_candidate | no_fill
    block_detail                TEXT,     -- the specific engine reason, e.g. 'credit_below_floor'
    proposed_legs               TEXT,     -- JSON [{strike, sign, qty, type, bid, ask, delta}], the
                                          --   structure that was offered; bwb_roll only, from
                                          --   2026-09-30 (NULL on every earlier row, every mode)
    center                      REAL,
    wing_width                  REAL,
    blocking_strike             REAL,     -- populated for sign_rule_blocked
    seconds_until_cadence_clear REAL,     -- populated for cadence_blocked; the cost of the spacing
    spot                        REAL,
    net_gex                     REAL,
    gex_positive                INTEGER,
    regime_label                TEXT,
    would_be_credit             REAL,
    position_id                 TEXT      -- set on the filled path, linking an attempt to its result
);

CREATE TABLE fly_iterations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    iteration_ts      TEXT,
    trade_date        TEXT,
    symbol            TEXT,
    arm               TEXT,
    center            REAL,
    center_reason     TEXT,
    underlying_price  REAL,
    UNIQUE (iteration_ts, symbol, arm)
);

CREATE TABLE fly_live_marks (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    iteration_ts      TEXT,
    trade_date        TEXT,
    position_id       TEXT,
    kind              TEXT,     -- the structure as held at this tick (short_vertical -> fly)
    structure_mid     REAL,     -- per-contract mid value of the structure as held
    mark_pnl          REAL,     -- (net + structure_mid) x 100 x qty - fees
    spot              REAL,
    open_margin       REAL,     -- the tick's open worst-case exposure, every open position summed
    resting_limit     REAL,     -- the working completion order's limit debit, if one is resting
    UNIQUE (iteration_ts, position_id)
);

CREATE TABLE fly_live_orders (
    order_id           TEXT PRIMARY KEY,
    trade_date         TEXT NOT NULL,
    position_id        TEXT NOT NULL,
    leg                TEXT NOT NULL,     -- entry | completion
    side               TEXT,
    center             REAL,
    wing_width         REAL,
    quantity           INTEGER,
    limit_price        REAL,              -- per share, positive: the credit asked or the debit bid
    mid_at_submit      REAL,              -- the spread's mid in the order's own direction
    spot_at_submit     REAL,
    placed_at          TEXT,
    outcome            TEXT,              -- working | filled | cancelled | cutoff_cancelled |
                                          --   replaced | <broker terminal state>
    resolved_at        TEXT,
    broker_filled_at   TEXT,
    broker_fill_price  REAL,              -- per share, positive, in the order's own direction
    fill_time_source   TEXT,              -- broker_order | transactions | noticed
    fill_spot          REAL,
    fill_spot_source   TEXT,              -- path | trail
    fill_dist_center   REAL,
    fill_dist_long     REAL,
    fill_dist_widths   REAL,
    fill_dist_moves    REAL,
    fill_obs_at        TEXT,              -- the path observation the price measures came from
    fill_mid           REAL,
    fill_natural       REAL,
    fill_mid_gap       REAL,
    fill_natural_gap   REAL,
    source             TEXT NOT NULL DEFAULT 'live',  -- live | backfill
    updated_at         TEXT
);

CREATE TABLE fly_order_path (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at   TEXT NOT NULL,
    trade_date    TEXT NOT NULL,
    position_id   TEXT NOT NULL,
    order_id      TEXT NOT NULL,
    leg           TEXT NOT NULL,
    source        TEXT,                -- tick | watch
    limit_price   REAL,
    buy_bid       REAL,
    buy_ask       REAL,
    sell_bid      REAL,
    sell_ask      REAL,
    spot          REAL,
    UNIQUE (observed_at, order_id)
);

CREATE TABLE fly_positions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id         TEXT UNIQUE,
    book_id             TEXT,
    trade_date          TEXT,
    arm                 TEXT,
    entry_mode          TEXT,
    symbol              TEXT,
    kind                TEXT,
    side                TEXT,
    center              REAL,
    wing_width          REAL,
    quantity            INTEGER,
    net                 REAL,
    credit              REAL,
    debit               REAL,
    fees                REAL,
    floor_dollars       REAL,
    risk_free           INTEGER,
    entry_time          TEXT,
    entry_window        TEXT,
    center_reason       TEXT,
    completing_direction TEXT,
    completed_at        TEXT,
    underlying_at_entry REAL,
    -- Counterfactual: the LOWEST completing debit seen while this spread was open, recorded whether
    -- or not the gate fired. Without it, "never completed" is ambiguous between "the market never
    -- offered it" and "our fee_buffer was too tight" -- and those need opposite fixes.
    best_completing_debit REAL,
    best_debit_at       TEXT,
    -- Minutes from open to completion, and where spot was when it happened. Feeds the paper-vs-live
    -- gap: a completion that took three seconds of quote drift is far less likely to fill live than
    -- one that took forty minutes.
    completion_latency_min REAL,
    spot_at_completion  REAL,
    settlement_price    REAL,
    expiry_payoff       REAL,
    gross_pnl           REAL,
    pnl                 REAL,
    pinned              INTEGER,
    status              TEXT,
    exit_time           TEXT,
    created_at          TEXT,
    updated_at          TEXT
, entry_order_id TEXT, completion_order_id TEXT, entry_fill_status TEXT, completion_fill_status TEXT, settlement_source TEXT, close_order_id TEXT, close_fill_status TEXT, closed_before_expiry INTEGER, best_completing_credit REAL, best_credit_at TEXT, completion_mode TEXT, entry_vol_bucket TEXT, entry_gex_bucket TEXT, entry_time_bucket TEXT, entry_skew_bucket TEXT, completion_vol_bucket TEXT, completion_gex_bucket TEXT, completion_time_bucket TEXT, completion_skew_bucket TEXT, entry_center_offset_bucket TEXT, completion_center_offset_bucket TEXT, entry_trend_bucket TEXT, completion_trend_bucket TEXT, entry_vol_value REAL, entry_gex_concentration REAL, entry_time_value REAL, entry_skew_value REAL, entry_net_gex REAL, entry_gamma_flip REAL, entry_gex_spot REAL, entry_gex_strikes REAL, entry_gex_input_age REAL, completion_vol_value REAL, completion_gex_concentration REAL, completion_time_value REAL, completion_skew_value REAL, completion_net_gex REAL, completion_gamma_flip REAL, completion_gex_spot REAL, completion_gex_strikes REAL, completion_gex_input_age REAL, entry_center_offset_value REAL, completion_center_offset_value REAL, entry_center_delta REAL, entry_far_wing_delta REAL, hedge_strike REAL, hedge_delta REAL, hedge_premium REAL, hedge_fee REAL, hedge_leg_symbol TEXT, hedge_best_mid REAL, hedge_best_mid_at TEXT, hedge_settle_value REAL, hedge_mid_at_completion REAL, hedge_mid_at_completion_at TEXT, selected_from TEXT, selector_model_id TEXT, entry_trend_value REAL, completion_trend_value REAL, far_width REAL, rolled_at TEXT, roll_debit REAL, roll_latency_min REAL, spot_at_roll REAL, best_roll_debit REAL, best_roll_debit_at TEXT, post_best_completing_debit REAL, post_best_debit_at TEXT, post_best_completing_credit REAL, post_best_credit_at TEXT, modeled_net REAL, modeled_fees REAL, modeled_gross_pnl REAL, modeled_pnl REAL, modeled_expiry_payoff REAL, broker_reconciled_at TEXT, broker_reconciliation_status TEXT, void_reason TEXT, experiment_id TEXT, center_leg_symbol TEXT, wing_leg_symbol TEXT, far_leg_symbol TEXT, completing_leg_symbol TEXT, settlement_fees REAL, slippage_dollars REAL, entry_mid_at_submit REAL, entry_event_bucket TEXT, completion_event_bucket TEXT, entry_event_value REAL, completion_event_value REAL, entry_event_labels TEXT, completion_event_labels TEXT, shadow_completion_limit REAL, shadow_touches TEXT, close_tag_at TEXT, close_tag_source TEXT, close_tag_natural REAL, close_tag_mid REAL, close_tag_spot REAL, close_tag_fees REAL, agent_mode TEXT, agent_gate TEXT, agent_would_refuse INTEGER);

CREATE TABLE fly_selector_choices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    model_id    TEXT,
    candidates  TEXT,                 -- JSON, one entry per source: refusal or plan summary + score
    chosen      TEXT,                 -- the merged source label, or NULL for skip / nothing offered
    reason      TEXT NOT NULL
);

CREATE TABLE fly_snapshots (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    iteration_ts      TEXT,
    trade_date        TEXT,
    symbol            TEXT,
    status            TEXT,     -- "ok" | the refusal reason (no_fresh_quotes, no_spot_price, ...)
    quotes_fresh      INTEGER,  -- NULL on refusals that failed before the quote scan
    quotes_rejected   INTEGER,
    underlying_price  REAL,
    UNIQUE (iteration_ts, symbol)
);

CREATE TABLE fly_stream_window (
    symbol                      TEXT PRIMARY KEY,
    width                       INTEGER NOT NULL,
    last_escalated_occurrences  INTEGER NOT NULL DEFAULT 0,
    last_checked_occurrences    INTEGER NOT NULL DEFAULT 0,
    last_escalated_at           TEXT,
    last_miss_at                TEXT,
    updated_at                  TEXT
);

CREATE TABLE measurement_breaks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    break_date  TEXT NOT NULL,
    scope       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    reason      TEXT NOT NULL,
    detail      TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE (break_date, scope, kind)
);

CREATE INDEX idx_fly_attempts_date ON fly_entry_attempts(trade_date, arm);

CREATE INDEX idx_fly_attempts_outcome ON fly_entry_attempts(trade_date, outcome);

CREATE INDEX idx_fly_debit_ladder_day ON fly_debit_ladder(trade_date, symbol, arm);

CREATE INDEX idx_fly_decisions_date ON fly_decisions(trade_date);

CREATE INDEX idx_fly_decisions_run
    ON fly_decisions(trade_date, arm, symbol, mode, id);

CREATE INDEX idx_fly_iterations_date ON fly_iterations(trade_date);

CREATE INDEX idx_fly_live_marks_date ON fly_live_marks(trade_date);

CREATE INDEX idx_fly_live_orders_date ON fly_live_orders(trade_date);

CREATE INDEX idx_fly_measurement_breaks_date ON measurement_breaks (break_date);

CREATE INDEX idx_fly_order_path_order ON fly_order_path(order_id, observed_at);

CREATE INDEX idx_fly_positions_book ON fly_positions(book_id);

CREATE INDEX idx_fly_positions_date ON fly_positions(trade_date);

CREATE INDEX idx_fly_selector_choices_day ON fly_selector_choices(trade_date, symbol);

CREATE INDEX idx_fly_snapshots_date ON fly_snapshots(trade_date);
