"""Paper database for cherrypick-flies — two tables, both append-then-update.

`fly_positions` is the row-per-structure ledger the orchestrator reads (schema tag `fly_book`).
`fly_books` is the per-session book roll-up, and it exists because a per-position ledger cannot
express the one thing that separates this strategy's honest claim from its marketing: a book whose
risk graph is green in the middle may still lose outside the funding spreads' wings. The band lives
here, next to the floor it qualifies.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3

from cherrypick.flies import clock  # noqa: E402

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fly_positions (
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
);
CREATE INDEX IF NOT EXISTS idx_fly_positions_date ON fly_positions(trade_date);
CREATE INDEX IF NOT EXISTS idx_fly_positions_book ON fly_positions(book_id);

CREATE TABLE IF NOT EXISTS fly_books (
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
);

-- The decision journal: WHY an entry was made or refused, in a form you can query.
--
-- One row per RUN of an identical (trade_date, arm, symbol, mode, reason). A gate that blocks every
-- iteration from 09:45 to 11:20 is one row with occurrences=18, not eighteen identical rows -- so a
-- day where nothing traded reads as a handful of rows that tell the story. This is deliberately
-- unlike MEIC, which collapses its (equally rich) reasons into a free-text loop_log.reasoning blob
-- that later has to be regex-scraped and can't be aggregated at all.
CREATE TABLE IF NOT EXISTS fly_decisions (
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
CREATE INDEX IF NOT EXISTS idx_fly_decisions_date ON fly_decisions(trade_date);
-- Covers `record_decision`'s run lookup, which is the hottest read this table has: every arm asks
-- for its own latest row on every tick to decide whether to extend a collapsed run or open a new
-- one. On trade_date alone that seeks to the day and then walks every arm's rows in it, so the cost
-- grew through the session. Trailing `id` lets the ORDER BY id DESC LIMIT 1 come off the index.
CREATE INDEX IF NOT EXISTS idx_fly_decisions_run
    ON fly_decisions(trade_date, arm, symbol, mode, id);

-- One thin row per (iteration x arm): what each arm WANTED, whether or not it acted.
--
-- Separate from the journal because collapsing destroys exactly what this is for. Arm divergence asks
-- what the arms chose ON THE SAME ITERATION, and if gex and control agree most of the time then the
-- experiment cannot separate them -- which is worth discovering in week one rather than month three.
CREATE TABLE IF NOT EXISTS fly_iterations (
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
CREATE INDEX IF NOT EXISTS idx_fly_iterations_date ON fly_iterations(trade_date);

-- One row per (iteration x symbol): what the FEED gave us this tick, recorded whether or not a
-- snapshot could be built.
--
-- Separate from fly_iterations on purpose. That table is per-arm and only written when a snapshot
-- succeeds; a refused snapshot never reaches the arm loop, so a feed outage writes nothing there and
-- a silent stretch is indistinguishable from a healthy loop that chose not to trade. This records the
-- refusal too, so a gap on the timeline can say WHY -- feed stale vs streamer down vs loop not running
-- (the last being simply the absence of rows). status is "ok" or the provider's refusal reason.
CREATE TABLE IF NOT EXISTS fly_snapshots (
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
CREATE INDEX IF NOT EXISTS idx_fly_snapshots_date ON fly_snapshots(trade_date);

-- One row per EVALUATED ENTRY OPPORTUNITY per (iteration x arm): what was proposed and what
-- happened to it. Uncollapsed, unlike fly_decisions.
--
-- This exists because the arms became independent portfolios with unbounded capital (2026-08-11):
-- every arm now sees the same market with the same money, so the ONLY thing that differentiates
-- them is which entries the rules let through. That makes the refusals the primary measurement
-- rather than a diagnostic, and neither module recorded them in a queryable form before.
--
-- Deliberately NOT folded into fly_decisions, whose whole design is to collapse a run of identical
-- reasons into one row with occurrences=N. That collapse is right for reading a session and wrong
-- for measuring one: `seconds_until_cadence_clear` falls by one tick every iteration, so no two
-- rows would ever share a run key and the collapse would degenerate into one row per tick anyway --
-- with the aggregation machinery still in the path. fly_decisions stays exactly as it is, the
-- human-readable session narrative; this is the measurement record underneath it.
--
-- `outcome` is the taxonomy the read side buckets on, and `no_fill` is deliberately its own value:
-- under a fill-based cadence clock an entry that cleared every gate and simply did not fill neither
-- consumed the arm's slot nor was refused by a rule, and folding it into a gate outcome would make
-- the gates look stricter than they are.
CREATE TABLE IF NOT EXISTS fly_entry_attempts (
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
    proposed_legs               TEXT,     -- JSON [{strike, sign, type}], the structure that was offered
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
CREATE INDEX IF NOT EXISTS idx_fly_attempts_date ON fly_entry_attempts(trade_date, arm);
CREATE INDEX IF NOT EXISTS idx_fly_attempts_outcome ON fly_entry_attempts(trade_date, outcome);

-- One row per symbol: the current auto-escalated streamer ATM-window width this loop is requesting,
-- separate from the engine's own configured default. Written/read by stream_window.py, which widens
-- this after repeated missing_leg_quotes refusals and decays it back down once they stop. Paper and
-- live each have their own DB file, so their escalation state is naturally independent.
CREATE TABLE IF NOT EXISTS fly_stream_window (
    symbol                      TEXT PRIMARY KEY,
    width                       INTEGER NOT NULL,
    last_escalated_occurrences  INTEGER NOT NULL DEFAULT 0,
    last_checked_occurrences    INTEGER NOT NULL DEFAULT 0,
    last_escalated_at           TEXT,
    last_miss_at                TEXT,
    updated_at                  TEXT
);

-- Dates whose either side must never be pooled: a cadence change, an arm added, a gate retuned.
-- MEIC added this table citing THIS module's own tick-cadence cutover as the motivating example,
-- and this module never got one -- so flies is the only book whose arm comparisons have no record
-- of when the rules underneath them changed. The cross-module review reports that absence rather
-- than assuming continuity, which is honest but no substitute for the record itself.
--
-- Schema mirrors MEIC's deliberately (break_date, scope, kind, reason, detail) so a reader moving
-- between the two ledgers meets one shape. `scope` is an arm name or '*' for the whole book.
--
-- Its own table rather than a fly_decisions row: decisions are per-iteration and rotate with
-- volume, while a measurement break is a permanent property of the ledger and must outlive any
-- retention applied to the iteration log.
CREATE TABLE IF NOT EXISTS measurement_breaks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    break_date  TEXT NOT NULL,
    scope       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    reason      TEXT NOT NULL,
    detail      TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE (break_date, scope, kind)
);

CREATE INDEX IF NOT EXISTS idx_fly_measurement_breaks_date ON measurement_breaks (break_date);
"""


def default_db_path() -> str:
    home = os.environ.get("CHERRYPICK_HOME") or os.path.join(os.path.expanduser("~"), ".cherrypick")
    return os.path.join(home, "data", "flies", "paper_trades.db")


def live_db_path() -> str:
    """The LIVE ledger -- a separate file, same schema, never read by paper surfaces. The
    orchestrator's `live_db` config key should point here so it appears in `report --live`
    (and nowhere else)."""
    home = os.environ.get("CHERRYPICK_HOME") or os.path.join(os.path.expanduser("~"), ".cherrypick")
    return os.path.join(home, "data", "flies", "live_trades.db")


# Columns added to fly_positions after the first release. CREATE TABLE IF NOT EXISTS silently does
# nothing on an existing database, so a plain schema edit would leave older paper DBs missing these
# and every write against them would fail at runtime rather than at startup.
_ADDED_POSITION_COLUMNS = {
    "best_completing_debit": "REAL",
    "best_debit_at": "TEXT",
    "completion_latency_min": "REAL",
    "spot_at_completion": "REAL",
    # Live scaffold (docs/live-trading-plan.md): broker order ids on the position row. Paper rows
    # simply leave them NULL -- the live ledger is a separate FILE (live_db_path), same schema.
    "entry_order_id": "TEXT",
    "completion_order_id": "TEXT",
    # Rung-1: fill confirmation, distinct from "an order was placed" -- 'pending' | 'filled' |
    # 'rejected' | 'cancelled'. A pending entry still blocks a second entry (it's the position at
    # risk); a pending completion still leaves the position kind='short_vertical' until confirmed.
    "entry_fill_status": "TEXT",
    "completion_fill_status": "TEXT",
    # Live settlement provenance: 'last_trade_provisional' (auto-settled from the stream's last
    # trade at settle time) vs 'official' (a human re-settled with the official print). Paper rows
    # leave it NULL -- paper's last-trade settlement is its documented, accepted approximation.
    "settlement_source": "TEXT",
    # HISTORICAL ONLY -- nothing writes these columns any more. They belong to the pre-close ITM
    # exit, removed 2026-08-01 (it lost ~$34/position in paper and never fired in live; see
    # CLAUDE.md rule 5). Retained because 34 settled paper rows carry closed_before_expiry = 1 and
    # are the only record that the experiment ran: those rows closed at an intraday quote rather
    # than a settlement price (pinned = 0), so the flag is what marks them as not comparable to
    # ordinary settled rows. Kept in the schema so old ledgers still open and so analysis can
    # exclude them; close_order_id additionally still appears in live_loop's orphan sweep and
    # fee_reconcile, which must keep recognising order ids written before the removal.
    "close_order_id": "TEXT",
    "close_fill_status": "TEXT",
    "closed_before_expiry": "INTEGER",
    # debit_first: the running-max counterfactual for an uncompleted long_vertical, mirroring
    # best_completing_debit/best_debit_at above but in the opposite direction -- the best (highest)
    # credit the completing sale was ever offered, so a miss can be read as "the market never paid
    # enough" vs "our buffer was too tight" after the fact.
    "best_completing_credit": "REAL",
    "best_credit_at": "TEXT",
    # Which completion path closed a legged entry out: 'debit' (bought the completing debit
    # spread, kind -> 'fly') or 'iron' (sold the opposite-type credit spread, kind -> 'iron_fly').
    # NULL for pre-2026-07-31 rows and for entries that are still open or never completed.
    "completion_mode": "TEXT",
    # Regime tagging (engine.classify_regime): a pure read of the snapshot at the two moments that
    # matter for a future regime-conditioned mode selector -- what regime did we enter into, what
    # regime did we complete into (they can differ). Descriptive only; nothing here gates a
    # decision yet -- see engine.classify_regime's docstring. NULL for pre-2026-07-31 rows.
    "entry_vol_bucket": "TEXT",
    "entry_gex_bucket": "TEXT",
    "entry_time_bucket": "TEXT",
    "entry_skew_bucket": "TEXT",
    "completion_vol_bucket": "TEXT",
    "completion_gex_bucket": "TEXT",
    "completion_time_bucket": "TEXT",
    "completion_skew_bucket": "TEXT",
    # Signed centre-vs-spot at entry/completion (2026-08-04; NULL for earlier rows). Unlike the four
    # above this is a property of OUR choice, not of the market -- and it is the one that decides
    # which side engine.choose_side legs into, hence which way spot must move to complete at all.
    # See engine._classify_center_offset and docs/centre-lag.md. Descriptive; nothing gates on it.
    "entry_center_offset_bucket": "TEXT",
    "completion_center_offset_bucket": "TEXT",
    # Session drift from the day's own open (2026-08-04; NULL for earlier rows and for any session
    # the shared cache has no stream_summary row for -- coverage starts 2026-07-29). The dimension
    # this module spent three weeks believing a single snapshot could not carry; see
    # engine._classify_trend and provider._session_bounds. Descriptive; nothing gates on it.
    "entry_trend_bucket": "TEXT",
    "completion_trend_bucket": "TEXT",
    # The continuous measures the buckets above were derived from, plus the GEX surface's own
    # provenance (added 2026-08-01; NULL for earlier rows). Every regime threshold in this module
    # is a placeholder pending recalibration, and a bucket alone cannot be recalibrated: re-deriving
    # "would this session have been 'pinning' at a 0.5 cut instead of 0.6?" needs the number, and
    # re-running the session to get it is impossible. Storing the float makes the thresholds a
    # analysis-time choice instead of a permanent one. gex_strikes/gex_input_age are the coverage
    # pair -- a regime tag off four surviving stale strikes must be distinguishable from a real one.
    "entry_vol_value": "REAL",
    "entry_gex_concentration": "REAL",
    "entry_time_value": "REAL",
    "entry_skew_value": "REAL",
    "entry_net_gex": "REAL",
    "entry_gamma_flip": "REAL",
    "entry_gex_spot": "REAL",
    "entry_gex_strikes": "REAL",
    "entry_gex_input_age": "REAL",
    "completion_vol_value": "REAL",
    "completion_gex_concentration": "REAL",
    "completion_time_value": "REAL",
    "completion_skew_value": "REAL",
    "completion_net_gex": "REAL",
    "completion_gamma_flip": "REAL",
    "completion_gex_spot": "REAL",
    "completion_gex_strikes": "REAL",
    "completion_gex_input_age": "REAL",
    # Signed `center - spot` in points, the measure behind the offset buckets above. Derived rather
    # than new -- `center` and `underlying_at_entry` are both already on the row -- and stored anyway
    # so the cut is one by_regime call instead of a bespoke query, and so the completion-phase
    # version (which has no stored spot of its own to difference against) exists at all.
    "entry_center_offset_value": "REAL",
    "completion_center_offset_value": "REAL",
    # Signed `spot - day_open` in points. Unlike the offset pair above this canNOT be backfilled:
    # nothing on the row records where the session opened, and the cache keeps one summary row per
    # (symbol, trade_date) rather than a history -- so these start empty and fill forward only.
    "entry_trend_value": "REAL",
    "completion_trend_value": "REAL",
    # bwb_roll: the far (skipped) wing's width, kept AFTER the roll for history/rewind (wing_width
    # stays the near/protected width, unchanged by the roll). NULL for every other kind.
    "far_width": "REAL",
    "rolled_at": "TEXT",
    "roll_debit": "REAL",
    "roll_latency_min": "REAL",
    "spot_at_roll": "REAL",
    # Running MINIMUM roll debit ever seen for an open bwb -- mirrors best_completing_debit's
    # counterfactual role: after the fact, "the roll was never cheap enough" vs "our buffer was
    # too tight" call for opposite remedies.
    "best_roll_debit": "REAL",
    "best_roll_debit_at": "TEXT",
    # Post-completion counterfactual (added 2026-08-03): the trackers above stop at the completion
    # tick by construction (a completed position leaves the completion loops), so they can answer
    # "did the market ever offer it" for a MISS but not "how much better did the completing price
    # get after we took the first qualifying one" for a completion. These keep the same running
    # min/max going AFTER completion, until settlement, for completed flies only. Pure telemetry:
    # nothing reads them on a decision path — they exist so a wait-for-better completion rule can
    # be evaluated against recorded reality (split by completion_gex_bucket) instead of theory.
    # The stream cache keeps no quote history, so this cannot be reconstructed offline; it must be
    # recorded live or not at all. NULL for misses, iron/bwb completions, and pre-2026-08-03 rows.
    "post_best_completing_debit": "REAL",
    "post_best_debit_at": "TEXT",
    "post_best_completing_credit": "REAL",
    "post_best_credit_at": "TEXT",
    # Broker-cash reconciliation (fee_reconcile.py): a settled live position's net/fees/gross_pnl/
    # pnl/expiry_payoff are modeled at settlement time (quoted fill-price approximation + a flat
    # $5/ITM-contract assignment fee estimate). These columns hold that ORIGINAL modeled snapshot,
    # written once the first time reconciliation runs, so overwriting the canonical columns below
    # with broker-confirmed values never loses the model's own answer. NULL until reconciled; NULL
    # forever on paper rows (nothing to reconcile against).
    "modeled_net": "REAL",
    "modeled_fees": "REAL",
    "modeled_gross_pnl": "REAL",
    "modeled_pnl": "REAL",
    "modeled_expiry_payoff": "REAL",
    "broker_reconciled_at": "TEXT",
    # 'reconciled' (canonical columns now hold broker-confirmed values) | 'unmatched' (broker
    # transactions couldn't be confidently tied to this position -- canonical columns left
    # untouched, per the module's own honesty rule: don't silently guess). NULL = not attempted yet.
    "broker_reconciliation_status": "TEXT",
    # Why this row is not evidence; NULL when it is. For rows whose DECISIONS were made on numbers a
    # later fix proved wrong -- not rows that merely lost money, and not rows a caller chose to
    # filter. `closed_before_expiry` is the same idea done narrowly for one episode; this is the
    # general form, because the alternative used the second time was a date cutoff written in prose,
    # which `analytics.py` cannot see and a reader who skipped the doc cannot apply. Read surfaces
    # exclude these by default (`analytics._period_clause`) and account for them explicitly
    # (`analytics.voided`), so the exclusion is stated rather than inferred from a gap in a total.
    "void_reason": "TEXT",
}

# Rows whose decisions rest on a defect, stamped once when `void_reason` is first added. Keyed on
# what is identifiable in the ledger rather than by position_id, since these predate any marker.
# The bwb entry: `evaluate_roll` priced the wrong legs (a `far + wing` span instead of `far - wing`,
# 3x too wide at the default ratio) and `evaluate_bwb_entry` inherited the legged side rule, which
# put the roll spread in the money -- so the 14 rolls and the 11 refusals alike rest on a spread
# that was never the trade. The stream cache keeps no quote history, so they cannot be re-derived.
_VOID_BACKFILL = (
    (
        "entry_mode = 'bwb_roll' AND trade_date < '2026-08-07'",
        "bwb roll priced the wrong legs and the wrong side (fixed 2026-08-07); not re-derivable",
    ),
)

_ADDED_BOOK_COLUMNS = {
    "settlement_source": "TEXT",
    # Book-level mirror of the position-level reconciliation above -- see fee_reconcile.py.
    "modeled_pnl": "REAL",
    "modeled_fees": "REAL",
    "broker_reconciled_at": "TEXT",
    "broker_reconciliation_status": "TEXT",
}


def _migrate(conn: sqlite3.Connection) -> list[str]:
    """Add any columns missing from an older paper DB. Returns what it added (for tests and logs)."""
    added = []
    for table, columns in (
        ("fly_positions", _ADDED_POSITION_COLUMNS),
        ("fly_books", _ADDED_BOOK_COLUMNS),
    ):
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for column, sql_type in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
                added.append(f"{table}.{column}")
                # Stamp the known-void rows the ONE time the column appears. Guarded on the column
                # having just been added rather than run every open, so a later deliberate un-void
                # (or a correction to the reason text) is not silently overwritten on next startup.
                if column == "void_reason":
                    for predicate, reason in _VOID_BACKFILL:
                        conn.execute(f"UPDATE fly_positions SET void_reason = ? WHERE {predicate}", (reason,))
    if added:
        conn.commit()
    return added


def stale_writer_columns(conn: sqlite3.Connection) -> list[str]:
    """Regime columns the LEDGER has but the RUNNING CODE will never write. Empty is healthy.

    Detects one specific, silent failure, observed on 2026-08-05: the loop imports from the working
    tree, so whichever branch happens to be checked out decides what gets recorded. That session ran
    from a checkout predating the `trend`/`center_offset` dimensions and wrote NULL to all four of
    their columns for the entire day. Nothing errored. The four older dimensions populated normally,
    which is exactly what made it look fine at a glance -- and regime data has no backfill path in
    general, so a day lost this way is usually lost for good.

    It works by comparing the code against the DATABASE FILE rather than against itself, which is the
    only comparison that can catch this. Migration is additive and permanent, so once a newer
    checkout has opened a ledger the columns stay; an older checkout then has a schema it does not
    know how to fill, and that gap is the signal. Comparing `_ADDED_POSITION_COLUMNS` to
    `classify_regime`'s keys would detect nothing at all -- on a stale checkout both are stale
    together and agree perfectly.

    Reports rather than repairs: a stale checkout cannot be fixed from inside itself, and refusing to
    trade over it would turn a telemetry gap into an outage. The caller logs it loudly.
    """
    from cherrypick.flies import engine

    written = {
        f"{phase}_{key}" for key in engine.classify_regime({}, {}) for phase in ("entry", "completion")
    }
    present = {r["name"] for r in conn.execute("PRAGMA table_info(fly_positions)")}
    # Matched on the regime naming convention -- `<phase>_<dimension>_bucket` and `_value` -- rather
    # than an exclusion list of everything else. Every dimension ships as that pair, so a new one
    # cannot slip past, while ordinary phase-prefixed columns (entry_time, completion_latency_min,
    # the order-id/fill-status family) are excluded by shape instead of by being remembered. An
    # exclusion list would need editing every time an unrelated column is added, and the day it was
    # forgotten this check would start crying wolf and get ignored.
    regime = {
        c for c in present if c.startswith(("entry_", "completion_")) and c.endswith(("_bucket", "_value"))
    }
    return sorted(regime - written)


def connect(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or os.environ.get("FLIES_DB_PATH") or default_db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


def _now() -> str:
    """ET, with offset — see clock.py. Every timestamp this module persists is ET; it was naive
    machine-local until 2026-07-27."""
    return clock.now_iso()


def _upsert(conn, table: str, key: str, row: dict) -> None:
    """Insert `row`, or update the existing row with the same natural key. Keeps the loop idempotent:
    a restart mid-session re-writes the same position rather than duplicating it."""
    row = {**row, "updated_at": _now()}
    existing = conn.execute(f"SELECT id FROM {table} WHERE {key} = ?", (row[key],)).fetchone()
    if existing is None:
        row.setdefault("created_at", _now())
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(row.values()))
    else:
        sets = ", ".join(f"{c} = ?" for c in row if c != key)
        vals = [v for c, v in row.items() if c != key] + [row[key]]
        conn.execute(f"UPDATE {table} SET {sets} WHERE {key} = ?", vals)
    conn.commit()


def save_position(conn, row: dict) -> None:
    _upsert(conn, "fly_positions", "position_id", row)


def save_book(conn, row: dict) -> None:
    _upsert(conn, "fly_books", "book_id", row)


def record_decision(
    conn,
    *,
    trade_date: str,
    arm: str,
    symbol: str,
    mode: str,
    reason: str,
    accepted: bool = False,
    center: float | None = None,
    position_id: str | None = None,
    detail: str | None = None,
    when: str | None = None,
) -> None:
    """Append to the decision journal, extending the current run when the reason is unchanged.

    "Current run" means the most recent row for this (trade_date, arm, symbol, mode) — so an unchanged
    reason bumps `occurrences` and `last_seen`, and a changed one opens a new row. Accepted decisions
    never extend a run: an entry is a distinct event even when two happen back to back, and collapsing
    them would lose the count of trades actually taken.
    """
    now = when or _now()
    latest = conn.execute(
        "SELECT * FROM fly_decisions WHERE trade_date = ? AND arm = ? AND symbol = ? AND mode = ? "
        "ORDER BY id DESC LIMIT 1",
        (trade_date, arm, symbol, mode),
    ).fetchone()

    if latest is not None and latest["reason"] == reason and not accepted and not latest["accepted"]:
        conn.execute(
            "UPDATE fly_decisions SET last_seen = ?, occurrences = occurrences + 1, center_last = ? "
            "WHERE id = ?",
            (now, center if center is not None else latest["center_last"], latest["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO fly_decisions (trade_date, arm, symbol, mode, reason, accepted, first_seen, "
            "last_seen, occurrences, center_first, center_last, position_id, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
            (
                trade_date,
                arm,
                symbol,
                mode,
                reason,
                int(accepted),
                now,
                now,
                center,
                center,
                position_id,
                detail,
            ),
        )
    conn.commit()


def record_entry_attempt(
    conn,
    *,
    trade_date: str,
    arm: str,
    symbol: str,
    outcome: str,
    mode: str | None = None,
    expiry: str | None = None,
    block_detail: str | None = None,
    proposed_legs: list | None = None,
    center: float | None = None,
    wing_width: float | None = None,
    blocking_strike: float | None = None,
    seconds_until_cadence_clear: float | None = None,
    spot: float | None = None,
    net_gex: float | None = None,
    gex_positive: bool | None = None,
    regime_label: str | None = None,
    would_be_credit: float | None = None,
    position_id: str | None = None,
    ts: str | None = None,
) -> None:
    """Append one evaluated entry opportunity. Never collapses — see the table comment for why.

    Pure telemetry on the write side: nothing in the loop reads this back to make a decision, so a
    failure here must never cost a trade. Callers are expected to wrap it accordingly; it is kept
    free of any read-modify-write so a single INSERT is the whole operation.
    """
    conn.execute(
        "INSERT INTO fly_entry_attempts (ts, trade_date, arm, symbol, expiry, mode, outcome, "
        "block_detail, proposed_legs, center, wing_width, blocking_strike, "
        "seconds_until_cadence_clear, spot, net_gex, gex_positive, regime_label, would_be_credit, "
        "position_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ts or _now(),
            trade_date,
            arm,
            symbol,
            expiry,
            mode,
            outcome,
            block_detail,
            json.dumps(proposed_legs) if proposed_legs is not None else None,
            center,
            wing_width,
            blocking_strike,
            seconds_until_cadence_clear,
            spot,
            net_gex,
            None if gex_positive is None else int(bool(gex_positive)),
            regime_label,
            would_be_credit,
            position_id,
        ),
    )
    conn.commit()


def last_entry_fill_ts(conn, *, trade_date: str, arm: str, symbol: str) -> str | None:
    """When this arm last FILLED an entry on this symbol today, or None.

    The cadence clock's input. Read from the position ledger rather than from the attempts table:
    the ledger is what actually holds a position, and a fill that was written there but whose
    attempt row failed to write must still consume the arm's slot. Telemetry may be lossy; the
    clock may not.

    Live rows carry `entry_fill_status` -- a *placed* order is 'pending' until confirmed, and it is
    counted here, because the slot is spent the moment an order is working. Paper rows leave the
    column NULL and are counted unconditionally. Rejected and cancelled entries are excluded: those
    never became a position, so they never used the slot.
    """
    row = conn.execute(
        "SELECT MAX(entry_time) AS t FROM fly_positions WHERE trade_date = ? AND arm = ? "
        "AND symbol = ? AND status != 'voided' AND void_reason IS NULL "
        "AND (entry_fill_status IS NULL OR entry_fill_status IN ('filled', 'pending'))",
        (trade_date, arm, symbol.upper()),
    ).fetchone()
    return row["t"] if row and row["t"] else None


def day_structure_keys(conn, *, trade_date: str, arm: str, symbol: str) -> set:
    """Every structure this arm has already established today, as (kind, side, center, wing, far).

    The duplicate rule's input. Deliberately spans the WHOLE day regardless of status: flies are
    completed, not closed, so a structure never leaves the book before EOD and re-entering it later
    in the session is the same trade twice. Voided rows are excluded -- those are rows the module
    has disavowed as evidence, so they constrain nothing.
    """
    rows = conn.execute(
        "SELECT kind, side, center, wing_width, far_width FROM fly_positions "
        "WHERE trade_date = ? AND arm = ? AND symbol = ? AND status != 'voided' AND void_reason IS NULL",
        (trade_date, arm, symbol.upper()),
    ).fetchall()
    return {
        (
            str(r["kind"] or ""),
            str(r["side"] or ""),
            None if r["center"] is None else float(r["center"]),
            None if r["wing_width"] is None else float(r["wing_width"]),
            None if r["far_width"] is None else float(r["far_width"]),
        )
        for r in rows
    }


def record_iteration(
    conn,
    *,
    iteration_ts: str,
    trade_date: str,
    symbol: str,
    arm: str,
    center: float | None,
    center_reason: str | None,
    underlying_price: float | None,
) -> None:
    """Record what one arm wanted on one iteration. Idempotent on (iteration_ts, symbol, arm) so a
    re-run of the same snapshot doesn't inflate the divergence denominator."""
    conn.execute(
        "INSERT OR REPLACE INTO fly_iterations (iteration_ts, trade_date, symbol, arm, center, "
        "center_reason, underlying_price) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (iteration_ts, trade_date, symbol, arm, center, center_reason, underlying_price),
    )
    conn.commit()


def record_measurement_break(
    conn,
    *,
    break_date: str,
    kind: str,
    reason: str,
    scope: str = "*",
    detail: str | None = None,
    created_at: str | None = None,
) -> None:
    """Record that sessions either side of `break_date` cannot be pooled.

    Idempotent on (break_date, scope, kind), so a loop that notices the same change on every tick
    writes one row. `scope` is an arm name, or '*' for the whole book.

    A break that lives only in a commit message is invisible to every read surface and to whoever
    reads a per-arm ranking three weeks from now -- which is exactly the state this module was in:
    it had a tick-cadence cutover and two arms added with nothing in the ledger to say so.
    """
    conn.execute(
        "INSERT INTO measurement_breaks (break_date, scope, kind, reason, detail, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(break_date, scope, kind) DO UPDATE SET "
        "  reason = excluded.reason, detail = COALESCE(excluded.detail, detail)",
        (break_date, scope, kind, reason, detail, created_at or _now()),
    )
    conn.commit()


def measurement_breaks(conn, *, scope: str | None = None) -> list[dict]:
    """Every recorded break, oldest first. `scope` filters to one arm plus the book-wide ones."""
    if scope:
        rows = conn.execute(
            "SELECT * FROM measurement_breaks WHERE scope IN (?, '*') ORDER BY break_date",
            (scope,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM measurement_breaks ORDER BY break_date").fetchall()
    return [dict(r) for r in rows]


def record_snapshot(
    conn,
    *,
    trade_date: str,
    symbol: str,
    status: str,
    quotes_fresh: int | None = None,
    quotes_rejected: int | None = None,
    underlying_price: float | None = None,
    iteration_ts: str | None = None,
) -> None:
    """Record what the feed gave us this tick — on both the snapshot-built and the refused path.

    Idempotent on (iteration_ts, symbol) so a re-run of the same tick doesn't double-count. This is
    pure telemetry: it records what the data looked like, never what was decided from it.
    """
    conn.execute(
        "INSERT OR REPLACE INTO fly_snapshots (iteration_ts, trade_date, symbol, status, "
        "quotes_fresh, quotes_rejected, underlying_price) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (iteration_ts or _now(), trade_date, symbol, status, quotes_fresh, quotes_rejected, underlying_price),
    )
    conn.commit()


def open_positions(conn, book_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM fly_positions WHERE book_id = ? AND status = 'open'", (book_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def book_positions(conn, book_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM fly_positions WHERE book_id = ?", (book_id,)).fetchall()
    return [dict(r) for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser(description="Inspect the cherrypick-flies paper database.")
    ap.add_argument("command", choices=["positions", "books"])
    ap.add_argument("--db")
    ap.add_argument("--date")
    args = ap.parse_args()

    conn = connect(args.db)
    table = "fly_positions" if args.command == "positions" else "fly_books"
    if args.date:
        rows = conn.execute(f"SELECT * FROM {table} WHERE trade_date = ?", (args.date,)).fetchall()
    else:
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT 50").fetchall()
    print(json.dumps([dict(r) for r in rows], indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
